# Agent Orchestration 设计原理与架构详解

> 基于 Claude Code TypeScript 原始源码的深度逆向分析。
> 涵盖文件：`query.ts` (1730行)、`AgentTool/` 目录 (14个文件, ~5000行)。

---

## 目录

1. [架构总览](#1-架构总览)
2. [Query Loop — 核心驱动循环](#2-query-loop--核心驱动循环)
3. [Agent 定义与加载系统](#3-agent-定义与加载系统)
4. [Agent 生命周期管理](#4-agent-生命周期管理)
5. [Fork Subagent 实验性架构](#5-fork-subagent-实验性架构)
6. [Tool 解析与权限继承](#6-tool-解析与权限继承)
7. [Prompt Cache 优化策略](#7-prompt-cache-优化策略)
8. [错误恢复与容错机制](#8-错误恢复与容错机制)
9. [Stop Hooks 与安全退出](#9-stop-hooks-与安全退出)
10. [Agent Memory 持久化](#10-agent-memory-持久化)
11. [Agent Resume 恢复机制](#11-agent-resume-恢复机制)
12. [Multi-Agent Swarms (Teammates)](#12-multi-agent-swarms-teammates)
13. [Worktree 隔离机制](#13-worktree-隔离机制)
14. [Notification 与 Queue 系统](#14-notification-与-queue-系统)
15. [完整数据流图](#15-完整数据流图)
16. [Python 移植要点](#16-python-移植要点)

---

## 1. 架构总览

### 核心设计哲学

Claude Code 的 Agent 编排采用 **递归自相似架构** — 主线程和子 Agent 共用同一套 `query()` 循环，区别仅在于：
- 上下文边界（system prompt、tool pool、permission mode）
- 生命周期管理（前台同步 vs 后台异步）
- 通知机制（主线程直接输出 vs 子 Agent 通过 task-notification 回报）

### 调用链路

```
用户输入
  ↓
REPL / SDK
  ↓
QueryEngine.submit_message()
  ↓
query() — 核心查询循环 (query.ts)
  ├── callModel() → 流式 API 调用
  ├── StreamingToolExecutor → 工具并行执行
  │     ├── AgentTool.call() → 创建子 Agent
  │     │     ├── runAgent() → 子 Agent 独立 query 循环
  │     │     │     └── query() → 递归 (相同循环)
  │     │     ├── forkSubagent → 缓存共享的分叉路径
  │     │     └── spawnTeammate → 多Agent协作路径
  │     ├── BashTool / FileEditTool / ...
  │     └── MCPTools
  ├── handleStopHooks() → 安全退出检查
  ├── getAttachmentMessages() → 队列命令注入
  └── state transition → 下一轮迭代
```

### 关键抽象

| 抽象 | 文件 | 职责 |
|------|------|------|
| `query()` / `queryLoop()` | query.ts | 核心驱动循环，状态机 |
| `AgentTool` | AgentTool.tsx | 工具接口层，路由分发 |
| `runAgent()` | runAgent.ts | Agent 执行生成器 |
| `AgentDefinition` | loadAgentsDir.ts | Agent 元数据与配置 |
| `resolveAgentTools()` | agentToolUtils.ts | 工具解析与过滤 |
| `FORK_AGENT` | forkSubagent.ts | 缓存共享分叉机制 |
| `finalizeAgentTool()` | agentToolUtils.ts | 结果收集与汇总 |

---

## 2. Query Loop — 核心驱动循环

### 2.1 状态定义 (State)

`queryLoop()` 是一个 `while(true)` 无限循环的异步生成器，通过 `State` 对象驱动状态转换：

```typescript
type State = {
  messages: MessageType[]           // 当前对话消息数组
  toolUseContext: ToolUseContext     // 工具执行上下文
  autoCompactTracking?: {           // 自动压缩追踪
    compacted: boolean
    turnCounter: number
    turnId: string
  }
  maxOutputTokensRecoveryCount: number  // max_output_tokens 恢复计数
  hasAttemptedReactiveCompact: boolean  // 是否已尝试响应式压缩
  maxOutputTokensOverride?: number      // token 上限覆盖值
  pendingToolUseSummary?: Promise<...>  // 待处理的工具使用摘要
  stopHookActive?: boolean              // stop hook 是否激活
  turnCount: number                     // 当前轮次计数
  transition?: {                        // 状态转换原因（用于调试/分析）
    reason: string
    [key: string]: unknown
  }
}
```

### 2.2 循环步骤详解

每轮迭代精确按以下顺序执行：

#### Step 1: 预取 (Prefetch)
```
首次轮次:
  ├── Memory prefetch (异步, 非阻塞)
  ├── Skill discovery prefetch (异步, 非阻塞)
  └── Tool use summary 注入 (从上一轮的异步 promise)
```
- Memory prefetch: 预加载 memdir 中相关的记忆条目
- Skill prefetch: 预发现可能需要的 skills（低延迟 Haiku 调用）
- 两者均为 zero-wait 设计，如果未 settle 则跳过本轮次，下轮重试

#### Step 2: Auto Compact (主动压缩)
```
if (tracking && !tracking.compacted):
  计算当前 token 总量
  if (超过阈值):
    执行 autoCompact
    yield compacted messages
    state = { messages: compacted, ... }
    continue  // 用压缩后的消息重新进入循环
```
- 在 API 调用之前检查，防止 prompt-too-long 错误
- 压缩后保留 task budget 的已消耗量（carryover）

#### Step 3: Token 阻塞检查
```
if (tokenBlockingLimit):
  检查输入 token 是否超过硬限制
  if (超过): return { reason: 'token_blocking_limit' }
```

#### Step 4: API 调用 (callModel)
```
response = deps.callModel({
  model,
  fastMode,
  toolChoice,
  fallbackModel,        // 流式回退模型
  taskBudget,           // 子Agent预算
  queryTracking,        // 深度追踪
  effortValue,          // 推理努力等级
  maxOutputTokens,      // 动态上限
  tools,                // 工具定义数组
  messages,             // 对话消息
  systemPrompt,         // 系统提示
  ...
})
```

关键细节：
- **StreamingToolExecutor**: 在流式响应过程中，工具调用的输入一旦完整就立即开始执行（不等整个响应结束）
- **Backfill observable input**: 对每个 tool_use block，将可观察的输入回填到 block 中（用于 UI 展示和权限检查）
- **Withheld errors**: 某些可恢复错误（prompt-too-long、max_output_tokens、media size）被"扣留"而不立即返回，留给后续恢复逻辑处理

#### Step 5: 模型回退 (Fallback)
```
if (FallbackTriggeredError):
  切换到 fallbackModel
  state = { ...state, 使用新模型 }
  continue
```

#### Step 6: Post-Sampling Hooks
```
for (hook of postSamplingHooks):
  await hook(lastMessage)
```

#### Step 7: 工具使用摘要 (Tool Use Summary)
```
if (有 tool_use blocks):
  yield toolUseSummary (异步生成，不阻塞)
```

#### Step 8: 终止判断
如果没有 tool_use blocks（纯文本回复），进入终止路径：

```
终止路径:
  ├── 恢复路径检查
  │   ├── Prompt-too-long (withheld 413)
  │   │   ├── Context Collapse Drain → retry
  │   │   └── Reactive Compact → retry
  │   ├── Max output tokens (withheld)
  │   │   ├── Escalate 8k → 64k → retry (同请求, 无meta message)
  │   │   └── Multi-turn recovery (注入恢复消息) × 3次上限
  │   └── Media size error → reactive compact
  │
  ├── API 错误 → 跳过 stop hooks (防止死循环)
  │
  ├── Stop Hooks 评估 → 可能阻塞/允许
  │
  ├── Token Budget 检查 → 可能注入 nudge 继续
  │
  └── return { reason: 'completed' }
```

#### Step 9: 工具执行 (Tool Execution)
如果有 tool_use blocks，执行工具：

```
工具执行:
  ├── StreamingToolExecutor.getRemainingResults() (已提前开始的)
  │   或
  ├── runTools(toolUseBlocks, ...) (传统串行)
  │
  ├── for each update:
  │   ├── yield message
  │   ├── 收集 toolResults
  │   └── 检查 hook_stopped_continuation
  │
  ├── 异步生成下一轮的 toolUseSummary (fire-and-forget)
  │
  ├── 中断检查 (aborted during tools)
  │   └── return { reason: 'aborted_tools' }
  │
  └── hook 阻止检查
      └── return { reason: 'hook_stopped' }
```

#### Step 10: 附件注入 (Attachment Injection)
```
附件注入 (工具执行完成后, API调用前):
  ├── Queue commands 快照
  │   ├── 主线程: agentId === undefined 的命令
  │   └── 子Agent: 仅 task-notification 且匹配 agentId
  │
  ├── File change attachments (edited_text_file)
  │
  ├── Memory prefetch consume (如果已 settle)
  │
  ├── Skill discovery prefetch consume
  │
  └── MCP tools refresh (新连接的服务器)
```

#### Step 11: Max Turns 检查
```
if (nextTurnCount > maxTurns):
  yield max_turns_reached attachment
  return { reason: 'max_turns' }
```

#### Step 12: 递归 (State Transition)
```
state = {
  messages: [...messagesForQuery, ...assistantMessages, ...toolResults],
  turnCount: nextTurnCount,
  transition: { reason: 'next_turn' },
  ...
}
// continue while(true)
```

### 2.3 状态转换原因完整列表

| `transition.reason` | 触发条件 |
|---------------------|----------|
| `next_turn` | 正常的下一轮工具执行后 |
| `collapse_drain_retry` | Context Collapse 排干后重试 |
| `reactive_compact_retry` | 响应式压缩后重试 |
| `max_output_tokens_escalate` | 从 8k 升级到 64k |
| `max_output_tokens_recovery` | 注入恢复消息重试 |
| `stop_hook_blocking` | Stop hook 阻塞错误后继续 |
| `token_budget_continuation` | Token 预算内 nudge 继续 |
| `proactive_compact` | 主动压缩后重试 |
| `auto_compact` | 自动压缩后重试 |

---

## 3. Agent 定义与加载系统

### 3.1 AgentDefinition 类型体系

```typescript
// 基础类型
type BaseAgentDefinition = {
  agentType: string              // 唯一标识符 (如 "general-purpose")
  whenToUse: string              // 描述何时使用 (传给模型选择)
  tools?: string[]               // 允许的工具列表, ['*'] = 全部
  disallowedTools?: string[]     // 禁止的工具列表
  skills?: string[]              // 预加载的 skill 名称
  mcpServers?: AgentMcpServerSpec[]  // Agent 专属 MCP 服务器
  hooks?: HooksSettings          // 会话级 hooks
  color?: AgentColorName         // UI 显示颜色
  model?: string                 // 模型覆盖 (或 'inherit')
  effort?: EffortValue           // 推理努力等级
  permissionMode?: PermissionMode // 权限模式
  maxTurns?: number              // 最大轮次
  background?: boolean           // 默认后台运行
  initialPrompt?: string         // 首轮预注入提示
  memory?: AgentMemoryScope      // 持久化记忆范围
  isolation?: 'worktree' | 'remote'  // 隔离模式
  requiredMcpServers?: string[]  // 必需的 MCP 服务器
  omitClaudeMd?: boolean         // 省略 CLAUDE.md (节省 token)
}

// 三种具体类型
type BuiltInAgentDefinition = BaseAgentDefinition & {
  source: 'built-in'
  getSystemPrompt: (params: { toolUseContext }) => string  // 动态 prompt
}

type CustomAgentDefinition = BaseAgentDefinition & {
  source: SettingSource  // 'userSettings' | 'projectSettings' | 'policySettings' | 'flagSettings'
  getSystemPrompt: () => string  // 闭包存储 prompt
  filename?: string              // 原始 .md 文件名
}

type PluginAgentDefinition = BaseAgentDefinition & {
  source: 'plugin'
  getSystemPrompt: () => string
  plugin: string                 // 插件标识
}
```

### 3.2 Agent 加载优先级

Agent 按以下顺序加载，**后加载的覆盖先加载的** (相同 agentType):

```
1. built-in agents (内置)
   ↓ 被覆盖
2. plugin agents (插件)
   ↓ 被覆盖
3. userSettings agents (~/.claude/agents/*.md)
   ↓ 被覆盖
4. projectSettings agents (.claude/agents/*.md)
   ↓ 被覆盖
5. flagSettings agents (GrowthBook 远程配置)
   ↓ 被覆盖
6. policySettings agents (企业管理策略)
```

### 3.3 内置 Agent 注册

```typescript
function getBuiltInAgents(): AgentDefinition[] {
  agents = [
    GENERAL_PURPOSE_AGENT,  // 通用 Agent (tools: ['*'])
    STATUSLINE_SETUP_AGENT, // 状态栏设置
  ]
  
  if (areExplorePlanAgentsEnabled()) {
    agents.push(EXPLORE_AGENT)  // 只读探索 (omitClaudeMd: true)
    agents.push(PLAN_AGENT)     // 只读规划 (omitClaudeMd: true)
  }
  
  if (isNonSdkEntrypoint) {
    agents.push(CLAUDE_CODE_GUIDE_AGENT)  // 代码指南
  }
  
  if (VERIFICATION_AGENT_ENABLED) {
    agents.push(VERIFICATION_AGENT)  // 验证 Agent
  }
  
  return agents
}
```

### 3.4 Markdown Agent 定义格式

```yaml
---
name: my-agent
description: "何时使用这个 Agent 的描述"
tools:
  - Read
  - Grep
  - Glob
  - Agent(worker, researcher)  # 限制可用的子Agent类型
disallowedTools:
  - Bash
model: sonnet       # 或 opus, haiku, inherit, 或完整模型名
effort: high         # low, medium, high, 或整数
permissionMode: auto  # auto | acceptEdits | bypassPermissions | plan
maxTurns: 20
color: blue
background: true     # 始终后台运行
memory: user         # user | project | local
isolation: worktree  # worktree | remote
skills:
  - python-patterns
  - security
mcpServers:
  - slack            # 引用已配置的服务器
  - name: my-server  # 内联定义
    command: npx
    args: ["-y", "my-mcp-server"]
hooks:
  PreToolUse:
    - matcher: Bash
      hooks:
        - type: command
          command: "echo validating..."
initialPrompt: "开始前先阅读 README.md"
---

这里是系统提示词的内容...
Agent 的详细指令写在 Markdown 正文中。
```

### 3.5 JSON Agent 定义格式

```json
{
  "my-agent": {
    "description": "何时使用这个 Agent",
    "prompt": "系统提示词内容",
    "tools": ["Read", "Grep", "Glob"],
    "disallowedTools": ["Bash"],
    "model": "sonnet",
    "effort": "high",
    "permissionMode": "acceptEdits",
    "maxTurns": 20,
    "background": true,
    "memory": "user",
    "isolation": "worktree",
    "skills": ["python-patterns"],
    "mcpServers": ["slack"],
    "hooks": { ... }
  }
}
```

---

## 4. Agent 生命周期管理

### 4.1 AgentTool.call() 路由决策

```
AgentTool.call(input)
  │
  ├── [Multi-Agent Swarms] team_name + name → spawnTeammate()
  │     返回 { status: 'teammate_spawned' }
  │
  ├── [Fork Path] subagent_type 省略 + FORK 开关开启 → FORK_AGENT
  │     ├── 递归保护: fork 子进程中不能再 fork
  │     └── 使用 buildForkedMessages() 构建缓存共享消息
  │
  ├── [Normal Path] subagent_type 指定 → 查找 selectedAgent
  │     ├── 不存在 → 检查是否被权限规则拒绝 → 抛出异常
  │     └── 存在 → 继续
  │
  ├── [MCP 依赖检查] requiredMcpServers
  │     ├── 有 pending 服务器 → 轮询等待 (最长30秒, 500ms间隔)
  │     ├── 有 failed 服务器 → 提前退出
  │     └── 验证工具可用性 → 缺失则抛异常
  │
  ├── [Remote Isolation] isolation === 'remote' → teleportToRemote()
  │     返回 { status: 'remote_launched' }
  │
  ├── [Worktree Isolation] isolation === 'worktree'
  │     创建 git worktree → agent-{id[:8]} slug
  │
  ├── [Async Path] shouldRunAsync 判断
  │     条件: run_in_background=true OR agent.background=true
  │           OR isCoordinator OR isForkEnabled OR isKairos
  │     路径: registerAsyncAgent() → void runAsyncAgentLifecycle()
  │     返回 { status: 'async_launched', agentId, outputFile }
  │
  └── [Sync Path] 同步前台执行
        ├── registerAgentForeground() (支持运行中转后台)
        ├── race(agentIterator.next(), backgroundSignal)
        │     如果 backgrounded → 转为 runAsyncAgentLifecycle
        ├── 消息转发给 parent (onProgress)
        ├── 最终 finalizeAgentTool()
        └── 返回 { status: 'completed', content, usage }
```

### 4.2 shouldRunAsync 计算公式

```typescript
const shouldRunAsync = (
  run_in_background === true ||        // 用户显式指定
  selectedAgent.background === true ||  // Agent 定义强制
  isCoordinator ||                     // 协调者模式
  forceAsync ||                        // Fork 实验全部异步化
  assistantForceAsync ||               // Kairos 助手模式
  isProactiveActive()                  // 主动模式激活
) && !isBackgroundTasksDisabled        // 未禁用后台任务
```

### 4.3 runAgent() 执行生成器

`runAgent()` 是 `async function*`，核心逻辑：

```
runAgent(params)
  │
  ├── 1. Permission Mode 解析
  │     优先级: agentDefinition.permissionMode
  │         → parent bypassPermissions 时继承
  │         → parent acceptEdits 时继承
  │         → 默认 'auto'
  │     异步 Agent 额外: shouldAvoidPermissionPrompts = true
  │
  ├── 2. Tool Pool 组装
  │     if (useExactTools): 直接使用 parent 的 tools (Fork 路径)
  │     else: resolveAgentTools(agentDefinition, availableTools, isAsync)
  │
  ├── 3. System Prompt 构建
  │     if (override.systemPrompt): 使用覆盖 (Fork 缓存共享)
  │     else: buildAgentSystemPrompt()
  │       → agentDefinition.getSystemPrompt()
  │       → enhanceSystemPromptWithEnvDetails()
  │
  ├── 4. AbortController 隔离
  │     异步 Agent: new AbortController() (独立, 不连父级)
  │     同步 Agent: 共享父级 abortController
  │
  ├── 5. SubagentStart Hooks 触发
  │     yield* executeSubagentStartHooks()
  │
  ├── 6. Agent-specific MCP 服务器初始化
  │     initializeAgentMcpServers(agentDefinition.mcpServers)
  │     → 增量添加到工具池 (不影响父级)
  │
  ├── 7. Frontmatter Hooks 注册
  │     agentDefinition.hooks → registerSessionScopedHooks()
  │
  ├── 8. Skills 预加载
  │     agentDefinition.skills → preloadSkills()
  │
  ├── 9. 核心执行
  │     yield* query({
  │       messages: promptMessages,
  │       systemPrompt,
  │       tools,
  │       maxTurns: agentDefinition.maxTurns,
  │       taskBudget,
  │       ...
  │     })
  │
  └── 10. Cleanup (finally)
        ├── 注销 session-scoped hooks
        ├── 关闭 agent-specific MCP 连接
        └── 释放 prompt cache tracking
```

### 4.4 前台 → 后台无缝切换

同步 Agent 支持运行中切换到后台（用户按某个键触发）:

```
sync agent 运行中:
  │
  registerAgentForeground({
    ...,
    autoBackgroundMs: 120_000  // 2分钟自动后台 (可选)
  })
  │
  while(true):
    race(agentIterator.next(), backgroundSignal)
    │
    ├── [message wins] → 正常处理消息, 转发给 parent
    │
    └── [background wins] → 切换!
          ├── stopForegroundSummarization()
          ├── agentIterator.return() (超时1秒)
          ├── void runAsyncAgentLifecycle({
          │     makeStream: () => runAgent({ ...params, isAsync: true })
          │   })
          └── return { status: 'async_launched' }
```

关键：切换时会重新调用 `runAgent()` 创建新的 query 循环，已有的 `agentMessages` 作为上下文保留。

### 4.5 异步 Agent 生命周期 (runAsyncAgentLifecycle)

```
runAsyncAgentLifecycle()
  │
  ├── createProgressTracker()
  ├── createActivityDescriptionResolver()
  │
  ├── for await (msg of makeStream()):
  │     agentMessages.push(msg)
  │     rootSetAppState → 更新 task.messages (UI 可见)
  │     updateProgressFromMessage()
  │     updateAsyncAgentProgress() → AppState.tasks[id].progress
  │     emitTaskProgress() → SDK task_progress 事件
  │
  ├── [成功路径]
  │     finalizeAgentTool()
  │     completeAsyncAgent() → 先设状态 (gh-20236: 不被 classify 阻塞)
  │     classifyHandoffIfNeeded() → 安全分类器 (可选)
  │     getWorktreeResult() → 清理 worktree
  │     enqueueAgentNotification() → 通知主线程
  │
  ├── [AbortError 路径]
  │     killAsyncAgent()
  │     extractPartialResult() → 提取部分结果
  │     enqueueAgentNotification({ status: 'killed' })
  │
  └── [其他错误路径]
        failAsyncAgent()
        enqueueAgentNotification({ status: 'failed', error })
  
  finally:
    clearInvokedSkillsForAgent()
    clearDumpState()
```

---

## 5. Fork Subagent 实验性架构

### 5.1 设计动机

Fork 是一种 **Prompt Cache 极致优化** 策略。传统子 Agent 拥有独立的 system prompt 和消息历史，意味着每次 API 调用都是全新的缓存 key。Fork 子 Agent 通过 **共享父级的完整 API 请求前缀** 实现缓存命中。

### 5.2 FORK_AGENT 合成定义

```typescript
const FORK_AGENT: AgentDefinition = {
  agentType: 'fork-worker',
  whenToUse: '(internal fork synthetic agent)',
  tools: ['*'],
  source: 'built-in',
  baseDir: 'built-in',
  permissionMode: 'bubble',  // 权限冒泡到父级
  model: 'inherit',           // 继承父级模型
  getSystemPrompt: () => ''   // 空 — 使用父级的 system prompt
}
```

### 5.3 Fork 消息构建 (buildForkedMessages)

```
父级 assistant 消息:
  ├── tool_use[0]: { name: "Agent", input: { prompt: "task A" } }
  ├── tool_use[1]: { name: "Agent", input: { prompt: "task B" } }  ← 当前 fork
  └── tool_use[2]: { name: "Bash", input: { command: "ls" } }

↓ buildForkedMessages("task B", assistantMessage) ↓

构建的消息数组:
  [0] assistantMessage (原样, 包含所有 tool_use blocks)
  [1] user: tool_result[0] = "(completed by a sibling)"
  [2] user: tool_result[1] = buildChildMessage("task B")  ← 实际指令
  [3] user: tool_result[2] = "(completed by a sibling)"
```

### 5.4 Fork 子级指令 (buildChildMessage)

```
<fork-worker-directive>

You are a fork worker — an isolated branch created to handle exactly one task
from the parent's multi-tool turn. Everything above is the shared parent context:
the system prompt, conversation history, and the assistant turn that triggered
your creation. Your task is carried in the tool_result for your tool_use block.

Rules:
1. Act ONLY on your designated task — ignore sibling tool_use calls.
2. Never apologize, recap the context, or narrate what you see in this preamble.
3. Produce a SHORT final text response (≤4 sentences) summarizing outcome.
4. Use tools to do real work; verification happens in the parent, not here.
5. If your task is impossible or nonsensical, say so in one sentence and stop.
6. Do NOT spawn sub-agents (Agent tool) — complete the task yourself.
7. Do NOT duplicate or redo work visible in the parent conversation.
8. Do NOT run interactive or blocking shell commands (e.g., `npm run dev`).
9. Finish as fast as possible — you are one concurrent piece of a larger plan.
10. If you find yourself waiting on a response or input, stop and report status.

</fork-worker-directive>

Actual task: {prompt}
```

### 5.5 Fork 特性门控

```typescript
function isForkSubagentEnabled(): boolean {
  // 在以下情况禁用:
  // 1. 协调者模式 (workers 通过 coordinator 调度)
  // 2. 非交互式会话 (SDK/API)
  // 3. GrowthBook flag 'tengu_fork_subagent' 为 false
  if (isCoordinatorMode()) return false
  if (isNonInteractiveSession()) return false
  return getFeatureValue('tengu_fork_subagent', false)
}
```

### 5.6 递归 Fork 防护

```typescript
// 1. querySource 检查 (压缩安全 — 不受 autocompact 影响)
if (toolUseContext.options.querySource === `agent:builtin:fork-worker`) {
  throw new Error('Fork is not available inside a forked worker.')
}

// 2. 消息扫描回退 (处理 querySource 未传递的边界情况)
if (isInForkChild(toolUseContext.messages)) {
  throw new Error('Fork is not available inside a forked worker.')
}
```

---

## 6. Tool 解析与权限继承

### 6.1 工具过滤层次

```
父级工具池
  │
  ├── filterToolsForAgent() — 第一层过滤
  │     ├── MCP 工具 (mcp__*) → 始终允许
  │     ├── ExitPlanMode → 仅 plan 模式允许
  │     ├── ALL_AGENT_DISALLOWED_TOOLS → 硬禁止列表
  │     │     (包含: AgentTool 自身, TodoWrite, WebSearch 等)
  │     ├── CUSTOM_AGENT_DISALLOWED_TOOLS → 自定义 Agent 额外禁止
  │     └── ASYNC_AGENT_ALLOWED_TOOLS → 异步 Agent 白名单
  │           (异步 Agent 只能使用白名单中的工具)
  │
  ├── disallowedTools 过滤 — 第二层过滤
  │     从 AgentDefinition.disallowedTools 剥离
  │
  └── resolveAgentTools() — 最终解析
        ├── hasWildcard (['*'] 或 undefined) → 返回全部已过滤工具
        └── 逐一匹配 → validTools + invalidTools + resolvedTools
```

### 6.2 权限模式继承

```
权限模式解析优先级:

1. agentDefinition.permissionMode (Agent 定义中的值)
2. 父级 bypassPermissions → 子级继承 bypassPermissions
3. 父级 acceptEdits → 子级继承 acceptEdits
4. 默认 → 'auto'

特殊规则:
- 异步 Agent: shouldAvoidPermissionPrompts = true
  → 在权限检查时避免弹出用户确认提示
  → 因为后台 Agent 无法与用户交互

- Fork Agent: permissionMode = 'bubble'
  → 权限请求冒泡到父级处理
```

### 6.3 Agent(type1, type2) 工具规格

Agent 定义的 `tools` 字段支持 `Agent(worker, researcher)` 语法：

```typescript
// 解析 "Agent(worker, researcher)"
const { toolName, ruleContent } = permissionRuleValueFromString(toolSpec)
// toolName = "Agent"
// ruleContent = "worker, researcher"

// 转换为 allowedAgentTypes
allowedAgentTypes = ruleContent.split(',').map(s => s.trim())
// → ["worker", "researcher"]

// 限制子Agent只能使用这些类型
```

这意味着一个 Agent 可以限制它的子 Agent 只能是某些类型。

### 6.4 主线程 vs 子Agent 工具池

```
主线程:
  工具池由 assembleToolPool() 根据当前权限模式组装
  filterToolsForAgent 不适用 (isMainThread = true)
  Agent 定义的 tools 列表解析 (包括 allowedAgentTypes)

子Agent:
  工具池由 workerTools = assembleToolPool(workerPermissionContext, mcp.tools) 独立组装
  filterToolsForAgent 适用 → 硬禁止列表生效
  
Fork 子Agent (特殊):
  直接使用父级的 tools (useExactTools = true)
  → 缓存一致性优先于工具限制
```

---

## 7. Prompt Cache 优化策略

### 7.1 Fork 路径的缓存共享

Fork 的核心价值：**多个并行子 Agent 共享同一个 API 请求前缀的缓存**。

```
Parent API 请求:
  system: [parent system prompt]
  messages: [
    ...conversation history...,
    assistant: { tool_use: [Agent("A"), Agent("B"), Agent("C")] }
  ]

Fork A API 请求:                  Fork B API 请求:
  system: [parent system prompt]    system: [parent system prompt]     ← 相同
  messages: [                       messages: [                        ← 相同前缀
    ...conversation history...,       ...conversation history...,
    assistant: { ... },               assistant: { ... },
    user: tool_results[A=任务,         user: tool_results[A=sibling,     ← 仅此处不同
           B=sibling, C=sibling]             B=任务, C=sibling]
  ]                                 ]
```

API 级别的 prompt caching 会在第一个 fork 请求时缓存前缀，后续 fork 命中缓存。

### 7.2 System Prompt 传递策略

```
Fork 路径:
  forkParentSystemPrompt = toolUseContext.renderedSystemPrompt
  → 直接传递父级已渲染的系统提示 (避免重新计算导致字节不同)
  → 如果 renderedSystemPrompt 不可用 → 降级重新计算 (可能缓存未命中)
  
Normal 路径:
  enhancedSystemPrompt = enhanceSystemPromptWithEnvDetails([agentPrompt], model)
  → 使用 Agent 自己的 prompt + 环境信息增强
  → 独立的缓存 key
```

### 7.3 Cache Eviction Hint

```typescript
// Agent 完成时发出缓存驱逐提示
if (lastRequestId) {
  logEvent('tengu_cache_eviction_hint', {
    scope: 'subagent_end',
    last_request_id: lastRequestId,
  })
}
// 告知推理服务可以清理该子 Agent 的缓存链
```

### 7.4 useExactTools 继承

Fork 路径中 `useExactTools = true` 意味着：
- 使用父级的精确工具数组（不重新组装）
- 继承父级的 `thinkingConfig`
- 继承父级的 `isNonInteractiveSession`
- 确保工具定义的序列化字节与父级完全一致 → 缓存命中

---

## 8. 错误恢复与容错机制

### 8.1 Prompt-Too-Long (413) 三级恢复

```
API 返回 413 / prompt_too_long:
  │
  ├── 级别1: Context Collapse Drain
  │     if (CONTEXT_COLLAPSE feature on):
  │       drain staged context → 使用更少的上下文消息重试
  │       state.transition = { reason: 'collapse_drain_retry' }
  │
  ├── 级别2: Reactive Compact
  │     if (reactiveCompact available && !hasAttemptedReactiveCompact):
  │       tryReactiveCompact() → 压缩消息历史
  │       hasAttemptedReactiveCompact = true
  │       state.transition = { reason: 'reactive_compact_retry' }
  │
  └── 级别3: 不可恢复
        yield lastMessage (展示错误)
        executeStopFailureHooks() (fire-and-forget)
        return { reason: 'prompt_too_long' }
```

**关键设计决策**: 不可恢复时 **不执行 stop hooks**，避免死循环：
> error → hook blocking → retry → error → hook injects more tokens → ...

### 8.2 Max Output Tokens 二级恢复

```
API 返回 max_output_tokens:
  │
  ├── 级别1: Escalation (8k → 64k)
  │     条件: capEnabled && 未设自定义上限 && 首次触发
  │     操作: maxOutputTokensOverride = ESCALATED_MAX_TOKENS
  │     特点: 重试同一请求, 无注入 meta message
  │     state.transition = { reason: 'max_output_tokens_escalate' }
  │
  └── 级别2: Multi-turn Recovery (最多3次)
        注入恢复消息:
        "Output token limit hit. Resume directly — no apology, no recap.
         Pick up mid-thought if that is where the cut happened.
         Break remaining work into smaller pieces."
        maxOutputTokensRecoveryCount++
        state.transition = { reason: 'max_output_tokens_recovery' }
```

### 8.3 Model Fallback

```
API 调用失败 (FallbackTriggeredError):
  → 切换到 fallbackModel (如 Sonnet → Haiku)
  → 同一消息重试
  → 记录 streaming_fallback 事件
```

### 8.4 API Error 保护

```
if (lastMessage.isApiErrorMessage):
  // 跳过 stop hooks — 模型从未产生有效响应
  // 运行 hooks 会创建死循环:
  // error → hook blocking → retry → error → ...
  executeStopFailureHooks() (fire-and-forget, 不阻塞)
  return { reason: 'completed' }
```

---

## 9. Stop Hooks 与安全退出

### 9.1 Stop Hooks 评估流程

```
handleStopHooks():
  │
  ├── 仅在模型返回纯文本 (无 tool_use) 时评估
  │
  ├── 评估结果:
  │     { preventContinuation: true }
  │       → return { reason: 'stop_hook_prevented' }
  │
  │     { blockingErrors: [...messages] }
  │       → 将错误消息注入，继续循环
  │       → state.stopHookActive = true
  │       → hasAttemptedReactiveCompact 保持不变
  │         (防止: compact → still too long → error →
  │          stop hook → compact → ... 无限循环)
  │
  │     { blockingErrors: [] }
  │       → 正常退出
  │
  └── 不评估的情况:
        - lastMessage.isApiErrorMessage
        - 已被 withheld 的 prompt-too-long
        - 已被 withheld 的 media error
```

### 9.2 工具执行期间的 Hook 检查

```
for await (update of toolUpdates):
  if (update.message.attachment.type === 'hook_stopped_continuation'):
    shouldPreventContinuation = true
    
// 工具执行完成后:
if (shouldPreventContinuation):
  return { reason: 'hook_stopped' }
```

---

## 10. Agent Memory 持久化

### 10.1 Memory Scope

| Scope | 路径 | 用途 |
|-------|------|------|
| `user` | `~/.claude/agent-memory/<agentType>/` | 跨项目通用知识 |
| `project` | `.claude/agent-memory/<agentType>/` | 项目特定，VCS追踪 |
| `local` | `.claude/agent-memory-local/<agentType>/` | 项目特定，不入VCS |

### 10.2 Memory 加载机制

```
Agent 启动时 (getSystemPrompt 闭包中):
  if (memory enabled):
    memoryPrompt = loadAgentMemoryPrompt(agentType, scope)
    return systemPrompt + '\n\n' + memoryPrompt

loadAgentMemoryPrompt():
  1. ensureMemoryDirExists() (fire-and-forget)
  2. buildMemoryPrompt({
       displayName: 'Persistent Agent Memory',
       memoryDir,
       extraGuidelines: [scopeNote]
     })
  3. 返回包含 MEMORY.md 内容的结构化 prompt
```

### 10.3 Memory + Tool 自动注入

```
if (memory enabled && tools !== undefined):
  // 自动注入文件操作工具，确保 Agent 可以读写 memory
  tools += [FILE_WRITE_TOOL_NAME, FILE_EDIT_TOOL_NAME, FILE_READ_TOOL_NAME]
```

### 10.4 Memory Snapshot

```
项目级快照机制:
  checkAgentMemorySnapshot(agentType, scope):
    ├── 'initialize' → 从项目快照初始化本地 memory
    ├── 'prompt-update' → 有更新的快照可用
    └── 'none' → 无操作
```

---

## 11. Agent Resume 恢复机制

### 11.1 Resume 流程

```
resumeAgentBackground():
  │
  ├── 1. 读取历史记录
  │     transcript = getAgentTranscript(agentId)
  │     metadata = readAgentMetadata(agentId)
  │
  ├── 2. 消息清洗
  │     filterWhitespaceOnlyAssistantMessages()
  │     filterOrphanedThinkingOnlyMessages()
  │     filterUnresolvedToolUses()
  │
  ├── 3. Content Replacement State 重建
  │     reconstructForSubagentResume()
  │
  ├── 4. Worktree 恢复
  │     if (metadata.worktreePath exists):
  │       验证目录存在 → 更新 mtime (防止清理脚本删除)
  │     else: fallback to parent cwd
  │
  ├── 5. Agent 类型解析
  │     fork-worker → FORK_AGENT
  │     其他 → 从 activeAgents 查找 → 默认 GENERAL_PURPOSE
  │
  ├── 6. 构建消息数组
  │     promptMessages = [...resumedMessages, createUserMessage(prompt)]
  │
  └── 7. 启动异步生命周期
        registerAsyncAgent()
        void runAsyncAgentLifecycle({
          makeStream: () => runAgent({ ...params, isAsync: true })
        })
```

### 11.2 Resume 与 Fork 的兼容

Fork 恢复需要重建父级的 system prompt:
```
if (isResumedFork):
  forkParentSystemPrompt = toolUseContext.renderedSystemPrompt
    ?? buildEffectiveSystemPrompt({ ... })
  
  // forkContextMessages = undefined ← 不重复注入
  // 原始 fork 的 transcript 已包含父级上下文
```

---

## 12. Multi-Agent Swarms (Teammates)

### 12.1 Teammate 生成

```
if (teamName && name):
  // 通过 spawnTeammate() 启动独立的 Agent 进程
  // 两种模式:
  │
  ├── Tmux Teammate: 独立终端 pane, 独立进程
  │     → 完全独立的 message 流
  │     → 通过 mailbox (SendMessage) 通信
  │
  └── In-Process Teammate: 同一进程内的异步 Agent
        → 共享事件循环
        → 不能 spawn background agents
        → 不能 spawn 其他 teammates (flat roster)
```

### 12.2 Teammate 限制

```
约束规则:
1. Teammate 不能 spawn 其他 Teammate (flat roster)
   → isTeammate() && teamName && name → Error
   
2. In-process Teammate 不能 spawn background agents
   → isInProcessTeammate() && run_in_background → Error
   
3. Agent 定义 background: true 也受限
   → isInProcessTeammate() && selectedAgent.background → Error
```

---

## 13. Worktree 隔离机制

### 13.1 创建与清理

```
创建:
  slug = `agent-${earlyAgentId.slice(0, 8)}`
  worktreeInfo = createAgentWorktree(slug)
  → { worktreePath, worktreeBranch, headCommit, gitRoot, hookBased }

清理 (cleanupWorktreeIfNeeded):
  if (hookBased):
    保留 (无法检测 VCS 变更)
  elif (headCommit):
    if (hasWorktreeChanges(path, headCommit)):
      保留 (有变更)
    else:
      removeAgentWorktree() → 清理
      writeAgentMetadata() → 清除 worktreePath (防止 resume 使用已删目录)
```

### 13.2 Fork + Worktree

```
if (isForkPath && worktreeInfo):
  注入 worktree notice:
  "你正在 git worktree 中工作。
   原始路径: {parentCwd}
   你的工作路径: {worktreePath}
   翻译所有文件路径到你的 worktree 路径。
   文件内容可能与父级不同，需要重新读取。"
```

### 13.3 CWD Override

```
// 显式 cwd 参数优先于 worktree
const cwdOverridePath = cwd ?? worktreeInfo?.worktreePath

const wrapWithCwd = (fn) =>
  cwdOverridePath ? runWithCwdOverride(cwdOverridePath, fn) : fn()

// 所有 Agent 执行都在 wrapWithCwd 内:
wrapWithCwd(() => runAgent({ ... }))
wrapWithCwd(() => runAsyncAgentLifecycle({ ... }))
```

---

## 14. Notification 与 Queue 系统

### 14.1 Agent 完成通知

```
enqueueAgentNotification({
  taskId,
  description,
  status: 'completed' | 'failed' | 'killed',
  setAppState,
  finalMessage,          // 最终文本结果
  usage: {
    totalTokens,
    toolUses,
    durationMs,
  },
  toolUseId,
  worktreePath?,
  worktreeBranch?,
})
```

### 14.2 Queue 作用域隔离

```
Queue 是进程级全局单例:
  │
  ├── 主线程 drain: cmd.agentId === undefined
  │     → 用户命令、通知都进入主线程
  │
  ├── 子Agent drain: cmd.mode === 'task-notification' && cmd.agentId === currentAgentId
  │     → 每个子Agent只看到发给自己的 task-notification
  │     → 永远不看到 user prompts
  │
  └── Slash commands 排除: 不在 mid-turn drain 中处理
        → 必须通过 processSlashCommand 在 turn 结束后处理
```

### 14.3 SDK Event 通知

```
// 前台 Agent 完成时发送 SDK 事件:
enqueueSdkEvent({
  type: 'system',
  subtype: 'task_notification',
  task_id,
  tool_use_id,
  status: 'completed' | 'failed' | 'stopped',
  summary: description,
  usage: { total_tokens, tool_uses, duration_ms }
})
```

### 14.4 Handoff 安全分类

```
classifyHandoffIfNeeded():
  if (TRANSCRIPT_CLASSIFIER && permissionMode === 'auto'):
    1. 构建 transcript: buildTranscriptForClassifier(agentMessages, tools)
    2. 分类: classifyYoloAction(agentMessages, handoffPrompt, tools, ...)
    3. 结果:
       - 'allowed' → null (无警告)
       - 'blocked' → "SECURITY WARNING: ..." 前缀
       - 'unavailable' → "Note: classifier unavailable..." 前缀
```

---

## 15. 完整数据流图

```
┌──────────────────────────────────────────────────────────────┐
│                        用户输入                               │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  QueryEngine.submit_message()                                │
│  ├── initialize() (parallel: git, CLAUDE.md, tools, env)     │
│  ├── push user message                                       │
│  └── yield* query(...)                                       │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  query() → queryLoop() [while(true) state machine]           │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Step 1: Prefetch (memory + skills, async non-blocking)  │ │
│  ├─────────────────────────────────────────────────────────┤ │
│  │ Step 2: Auto Compact (if token > threshold)             │ │
│  ├─────────────────────────────────────────────────────────┤ │
│  │ Step 3: Token blocking limit check                      │ │
│  ├─────────────────────────────────────────────────────────┤ │
│  │ Step 4: callModel() — streaming API call                │ │
│  │   ├── StreamingToolExecutor (parallel tool pre-exec)    │ │
│  │   ├── Backfill observable input                         │ │
│  │   └── Withhold recoverable errors                       │ │
│  ├─────────────────────────────────────────────────────────┤ │
│  │ Step 5: Model fallback (if FallbackTriggeredError)      │ │
│  ├─────────────────────────────────────────────────────────┤ │
│  │ Step 6: Post-sampling hooks                             │ │
│  ├─────────────────────────────────────────────────────────┤ │
│  │ Step 7: Tool use summary (async, non-blocking)          │ │
│  ├─────────────────────────────────────────────────────────┤ │
│  │ [NO TOOL USE] ─────────────────────┐                    │ │
│  │ Step 8: Terminal path              │                    │ │
│  │   ├── Recovery (413/max_tokens)    │                    │ │
│  │   ├── Stop hooks evaluation        │                    │ │
│  │   ├── Token budget check           │                    │ │
│  │   └── return { reason }            │                    │ │
│  ├────────────────────────────────────┘                    │ │
│  │ [HAS TOOL USE] ───────────────────┐                     │ │
│  │ Step 9: Tool execution            │                     │ │
│  │   ├── StreamingToolExecutor       │                     │ │
│  │   │   .getRemainingResults()      │                     │ │
│  │   ├── OR runTools() (serial)      │                     │ │
│  │   └── AgentTool.call() ───────────┼──┐                  │ │
│  ├───────────────────────────────────┘  │                  │ │
│  │ Step 10: Attachment injection        │                  │ │
│  │   ├── Queue commands                 │                  │ │
│  │   ├── Memory prefetch consume        │                  │ │
│  │   ├── Skill prefetch consume         │                  │ │
│  │   └── MCP tools refresh              │                  │ │
│  ├──────────────────────────────────────┤                  │ │
│  │ Step 11: Max turns check             │                  │ │
│  ├──────────────────────────────────────┤                  │ │
│  │ Step 12: state → next_turn           │                  │ │
│  │   messages += assistant + toolResults│                  │ │
│  │   turnCount++                        │                  │ │
│  │   continue // while(true)            │                  │ │
│  └──────────────────────────────────────┘                  │ │
└─────────────────────────────────────────────────────────────┘ │
                                                                │
┌───────────────────────────────────────────────────────────────┘
│
▼  AgentTool.call() — Agent 创建与调度
┌──────────────────────────────────────────────────────────────┐
│  Route Decision:                                             │
│  ├── [Teammate] → spawnTeammate()                            │
│  ├── [Fork]     → FORK_AGENT + buildForkedMessages()         │
│  ├── [Remote]   → teleportToRemote() (CCR)                   │
│  ├── [Async]    → registerAsyncAgent()                       │
│  │               → void runAsyncAgentLifecycle()             │
│  └── [Sync]     → registerAgentForeground()                  │
│                  → race(iterator, backgroundSignal)           │
│                                                              │
│  runAgent() — Agent 执行生成器                                │
│  ├── Permission mode resolution                              │
│  ├── Tool pool assembly (resolveAgentTools)                   │
│  ├── System prompt build                                     │
│  ├── AbortController isolation                               │
│  ├── MCP servers init                                        │
│  ├── Hooks registration                                      │
│  ├── Skills preload                                          │
│  └── yield* query() ← 递归! 相同的核心循环                    │
│                                                              │
│  Finalization (finalizeAgentTool):                            │
│  ├── Extract text content (fallback scan)                    │
│  ├── Count tool uses                                         │
│  ├── Calculate tokens & duration                             │
│  ├── Cache eviction hint                                     │
│  └── Return { agentId, content, usage, ... }                 │
└──────────────────────────────────────────────────────────────┘
```

---

## 16. Python 移植要点

### 16.1 核心数据结构

```python
from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field

class TransitionReason(StrEnum):
    NEXT_TURN = "next_turn"
    COLLAPSE_DRAIN_RETRY = "collapse_drain_retry"
    REACTIVE_COMPACT_RETRY = "reactive_compact_retry"
    MAX_OUTPUT_TOKENS_ESCALATE = "max_output_tokens_escalate"
    MAX_OUTPUT_TOKENS_RECOVERY = "max_output_tokens_recovery"
    STOP_HOOK_BLOCKING = "stop_hook_blocking"
    TOKEN_BUDGET_CONTINUATION = "token_budget_continuation"
    PROACTIVE_COMPACT = "proactive_compact"
    AUTO_COMPACT = "auto_compact"

class QueryState(BaseModel):
    """query loop 的不可变状态快照"""
    model_config = ConfigDict(frozen=True)
    
    messages: tuple[Message, ...]
    tool_use_context: ToolUseContext
    auto_compact_tracking: AutoCompactTracking | None = None
    max_output_tokens_recovery_count: int = 0
    has_attempted_reactive_compact: bool = False
    max_output_tokens_override: int | None = None
    pending_tool_use_summary: asyncio.Task | None = None
    stop_hook_active: bool = False
    turn_count: int = 0
    transition: TransitionInfo | None = None

class AgentSource(StrEnum):
    BUILT_IN = "built-in"
    PLUGIN = "plugin"
    USER_SETTINGS = "userSettings"
    PROJECT_SETTINGS = "projectSettings"
    POLICY_SETTINGS = "policySettings"
    FLAG_SETTINGS = "flagSettings"

class AgentMemoryScope(StrEnum):
    USER = "user"
    PROJECT = "project"
    LOCAL = "local"

class IsolationMode(StrEnum):
    WORKTREE = "worktree"
    REMOTE = "remote"
```

### 16.2 关键实现映射

| TypeScript | Python | 要点 |
|------------|--------|------|
| `async function* query()` | `async def query() -> AsyncGenerator` | Python async generator |
| `yield*` | `async for msg in sub_gen: yield msg` | 无 yield* 语法糖 |
| `Promise.race()` | `asyncio.wait(return_when=FIRST_COMPLETED)` | 前台→后台切换 |
| `AbortController` | `asyncio.Event` + cancel | 需手动实现 |
| `while(true) { state = next; continue }` | 同样的 while True 循环 | 直接映射 |
| `StreamingToolExecutor` | `asyncio.TaskGroup` | 并行工具执行 |
| `fire-and-forget (void promise)` | `asyncio.create_task()` | 异步后台任务 |
| `lazySchema(() => z.object(...))` | Pydantic `BaseModel` | 模式定义 |
| `AgentDefinition` union type | 基类 + 子类 | discriminated union |
| `feature()` (bundle-time)` | 运行时 config flags | 特性开关 |

### 16.3 关键难点

1. **StreamingToolExecutor 并行执行**: TypeScript 中利用流式响应的增量 tool_use 输入提前启动工具执行。Python 需要在 SSE 流处理中实现类似的增量解析和并发启动。

2. **前台→后台无缝切换**: TypeScript 使用 `Promise.race` + 迭代器协议。Python 中需要 `asyncio.wait` + async generator 的 `.athrow()` / `.aclose()` 机制。

3. **Fork 缓存共享**: 需要确保序列化后的 API 请求前缀字节完全一致。Python 的 JSON 序列化需要确定性排序。

4. **Context Isolation**: TypeScript 使用 `AsyncLocalStorage` 传播 agent 上下文。Python 可使用 `contextvars` 模块。

5. **Worktree 管理**: 需要封装 `git worktree add/remove` 命令，处理并发清理、stale 检测等。

6. **MCP 服务器增量加载**: Agent 启动时可能动态添加 MCP 服务器，需要热插拔到工具池中。

### 16.4 建议实现顺序

```
Phase 1: Core Query Loop
  ├── QueryState 状态机
  ├── query() async generator (基本循环)
  ├── callModel + 流式响应处理
  ├── 基本 tool 执行 (串行)
  └── 终止判断 + max_turns

Phase 2: Agent Tool (同步路径)
  ├── AgentDefinition 类型体系
  ├── loadAgentsDir (Markdown + JSON 解析)
  ├── resolveAgentTools
  ├── runAgent generator
  ├── AgentTool.call() (sync path)
  └── finalizeAgentTool

Phase 3: 错误恢复
  ├── Prompt-too-long recovery
  ├── Max output tokens recovery
  ├── Model fallback
  └── API error protection

Phase 4: Agent Tool (异步路径)
  ├── registerAsyncAgent / registerAgentForeground
  ├── runAsyncAgentLifecycle
  ├── 前台→后台切换
  ├── Notification queue
  └── Resume 机制

Phase 5: 高级特性
  ├── Fork subagent
  ├── Prompt cache 优化
  ├── StreamingToolExecutor (并行)
  ├── Agent memory 持久化
  ├── Worktree isolation
  ├── Stop hooks
  └── Handoff classifier
```

---

## 附录: 文件清单与行数

| 文件 | 行数 | 核心职责 |
|------|------|---------|
| `query.ts` | 1730 | query loop 状态机, 所有恢复路径 |
| `AgentTool.tsx` | 1398 | AgentTool 工具入口, 路由分发, 生命周期 |
| `runAgent.ts` | 974 | Agent 执行生成器, 权限/工具/prompt 解析 |
| `loadAgentsDir.ts` | 756 | Agent 定义加载, Markdown/JSON 解析 |
| `agentToolUtils.ts` | 687 | 工具解析, 结果收集, 异步生命周期 |
| `prompt.ts` | 288 | Agent 选择提示词生成 |
| `resumeAgent.ts` | 266 | Agent 恢复机制 |
| `forkSubagent.ts` | 211 | Fork 分叉架构 |
| `agentMemory.ts` | 178 | 持久化记忆系统 |
| `constants.ts` | ~50 | 常量定义 |
| `builtInAgents.ts` | 73 | 内置 Agent 注册 |
| **合计** | **~6600** | |

---

## 17. 源码文件完整索引

> 以下为 `claude-code/` 项目中与 Agent 编排相关的 **全部源码文件**，按功能模块分类，含行数统计。

### 17.1 AgentTool 核心（Agent 工具主入口）

| 文件路径 | 行数 | 说明 |
|----------|------|------|
| `tools/AgentTool/AgentTool.tsx` | 1397 | **Agent 工具主实现**，定义 Agent 调用接口 |
| `tools/AgentTool/runAgent.ts` | 973 | **Agent 运行逻辑**，执行子 Agent |
| `tools/AgentTool/UI.tsx` | 871 | Agent 工具的 UI 渲染 |
| `tools/AgentTool/loadAgentsDir.ts` | 755 | 从目录加载自定义 Agent 定义 |
| `tools/AgentTool/agentToolUtils.ts` | 686 | Agent 工具辅助函数 |
| `tools/AgentTool/prompt.ts` | 287 | Agent 工具的 prompt 模板 |
| `tools/AgentTool/resumeAgent.ts` | 265 | 恢复（resume）Agent |
| `tools/AgentTool/forkSubagent.ts` | 210 | **fork 子 Agent** 逻辑 |
| `tools/AgentTool/agentMemorySnapshot.ts` | 197 | Agent 记忆快照 |
| `tools/AgentTool/agentMemory.ts` | 177 | Agent 记忆管理 |
| `tools/AgentTool/agentDisplay.ts` | 104 | Agent 显示/格式化 |
| `tools/AgentTool/builtInAgents.ts` | 72 | 内置 Agent 注册表 |
| `tools/AgentTool/agentColorManager.ts` | 66 | Agent 颜色管理 |
| `tools/AgentTool/constants.ts` | 12 | 常量 |

#### 内置 Agent 定义

| 文件路径 | 行数 | 说明 |
|----------|------|------|
| `tools/AgentTool/built-in/claudeCodeGuideAgent.ts` | 205 | Claude Code 引导 Agent |
| `tools/AgentTool/built-in/verificationAgent.ts` | 152 | 验证 Agent |
| `tools/AgentTool/built-in/statuslineSetup.ts` | 144 | 状态栏设置 |
| `tools/AgentTool/built-in/planAgent.ts` | 92 | 规划 Agent |
| `tools/AgentTool/built-in/exploreAgent.ts` | 83 | 探索 Agent |
| `tools/AgentTool/built-in/generalPurposeAgent.ts` | 34 | 通用 Agent |

### 17.2 多 Agent Spawn（多 Agent 并行生成）

| 文件路径 | 行数 | 说明 |
|----------|------|------|
| `tools/shared/spawnMultiAgent.ts` | 1093 | **多 Agent 并行生成核心实现** |

### 17.3 Coordinator（协调器模式）

| 文件路径 | 行数 | 说明 |
|----------|------|------|
| `coordinator/coordinatorMode.ts` | 369 | **Agent 协调模式逻辑** |
| `hooks/toolPermission/handlers/coordinatorHandler.ts` | 65 | 协调器权限处理 |
| `components/CoordinatorAgentStatus.tsx` | 272 | 协调器状态 UI |

### 17.4 Swarm（Agent 集群/蜂群）

| 文件路径 | 行数 | 说明 |
|----------|------|------|
| `utils/swarm/inProcessRunner.ts` | 1552 | **进程内 Agent 运行器**（最大文件） |
| `utils/swarm/permissionSync.ts` | 928 | Swarm 权限同步 |
| `utils/swarm/teamHelpers.ts` | 683 | 团队辅助函数 |
| `utils/swarm/It2SetupPrompt.tsx` | 379 | iTerm2 setup prompt |
| `utils/swarm/spawnInProcess.ts` | 328 | 进程内生成 Agent |
| `utils/swarm/spawnUtils.ts` | 146 | 生成工具函数 |
| `utils/swarm/teammateInit.ts` | 129 | Teammate 初始化 |
| `utils/swarm/reconnection.ts` | 119 | 断线重连 |
| `utils/swarm/teammateLayoutManager.ts` | 107 | Teammate 布局管理 |
| `utils/swarm/leaderPermissionBridge.ts` | 54 | Leader 权限桥接 |
| `utils/swarm/constants.ts` | 33 | 常量 |
| `utils/swarm/teammatePromptAddendum.ts` | 18 | Teammate prompt 附加 |
| `utils/swarm/teammateModel.ts` | 10 | Teammate 模型配置 |

#### Swarm 后端（不同终端后端实现）

| 文件路径 | 行数 | 说明 |
|----------|------|------|
| `utils/swarm/backends/TmuxBackend.ts` | 764 | Tmux 后端 |
| `utils/swarm/backends/registry.ts` | 464 | 后端注册表 |
| `utils/swarm/backends/ITermBackend.ts` | 370 | iTerm 后端 |
| `utils/swarm/backends/PaneBackendExecutor.ts` | 354 | Pane 后端执行器 |
| `utils/swarm/backends/InProcessBackend.ts` | 339 | **进程内后端** |
| `utils/swarm/backends/types.ts` | 311 | 后端类型定义 |
| `utils/swarm/backends/it2Setup.ts` | 245 | iTerm2 设置 |
| `utils/swarm/backends/detection.ts` | 128 | 后端检测 |
| `utils/swarm/backends/teammateModeSnapshot.ts` | 87 | Teammate 模式快照 |

#### Swarm Hooks

| 文件路径 | 行数 | 说明 |
|----------|------|------|
| `hooks/useSwarmPermissionPoller.ts` | 330 | Swarm 权限轮询 |
| `hooks/useSwarmInitialization.ts` | 81 | Swarm 初始化 |
| `hooks/toolPermission/handlers/swarmWorkerHandler.ts` | 159 | Swarm Worker 权限处理 |
| `components/PromptInput/useSwarmBanner.ts` | 155 | Swarm Banner |

### 17.5 Teammate（队友/协作者）

| 文件路径 | 行数 | 说明 |
|----------|------|------|
| `utils/teammateMailbox.ts` | 1183 | **Teammate 消息邮箱**（Agent 间通信） |
| `utils/teammate.ts` | 292 | Teammate 核心工具 |
| `utils/inProcessTeammateHelpers.ts` | 102 | 进程内 Teammate 辅助 |
| `utils/teammateContext.ts` | 96 | Teammate 上下文 |
| `utils/collapseTeammateShutdowns.ts` | 55 | Teammate 关闭折叠 |
| `state/teammateViewHelpers.ts` | 141 | Teammate 视图状态 |
| `hooks/useTeammateViewAutoExit.ts` | 63 | Teammate 视图自动退出 |
| `hooks/notifs/useTeammateShutdownNotification.ts` | 78 | Teammate 关闭通知 |
| `components/TeammateViewHeader.tsx` | 81 | Teammate 视图头部 |
| `components/Spinner/TeammateSpinnerTree.tsx` | 271 | Teammate Spinner 树形 |
| `components/Spinner/TeammateSpinnerLine.tsx` | 232 | Teammate Spinner 行 |
| `components/Spinner/teammateSelectHint.ts` | 1 | 选择提示 |
| `components/messages/UserTeammateMessage.tsx` | 205 | 用户 Teammate 消息 |

### 17.6 Task 系统（任务管理 — Agent 工作单元）

#### 任务类型实现

| 文件路径 | 行数 | 说明 |
|----------|------|------|
| `tasks/RemoteAgentTask/RemoteAgentTask.tsx` | 855 | **远程 Agent 任务** |
| `tasks/LocalAgentTask/LocalAgentTask.tsx` | 682 | **本地 Agent 任务** |
| `tasks/LocalShellTask/LocalShellTask.tsx` | 522 | 本地 Shell 任务 |
| `tasks/DreamTask/DreamTask.ts` | 157 | Dream 任务 |
| `tasks/InProcessTeammateTask/InProcessTeammateTask.tsx` | 125 | **进程内 Teammate 任务** |
| `tasks/InProcessTeammateTask/types.ts` | 121 | 类型定义 |
| `tasks/LocalMainSessionTask.ts` | — | 本地主会话任务 |
| `Task.ts` | 125 | Task 基础定义 |
| `tasks.ts` | 39 | 任务注册 |
| `tasks/types.ts` | 46 | 任务类型 |
| `tasks/stopTask.ts` | 100 | 停止任务 |
| `tasks/pillLabel.ts` | 82 | 任务标签 |
| `tasks/LocalShellTask/killShellTasks.ts` | 76 | 杀死 Shell 任务 |
| `tasks/LocalShellTask/guards.ts` | 41 | Guard |

#### Task 工具（对 LLM 暴露的工具）

| 文件路径 | 说明 |
|----------|------|
| `tools/TaskCreateTool/TaskCreateTool.ts` | 创建任务 |
| `tools/TaskCreateTool/prompt.ts` | 创建任务 prompt |
| `tools/TaskGetTool/TaskGetTool.ts` | 获取任务信息 |
| `tools/TaskGetTool/prompt.ts` | 获取任务信息 prompt |
| `tools/TaskListTool/TaskListTool.ts` | 列出任务 |
| `tools/TaskListTool/prompt.ts` | 列出任务 prompt |
| `tools/TaskOutputTool/TaskOutputTool.tsx` | 获取任务输出 |
| `tools/TaskStopTool/TaskStopTool.ts` | 停止任务 |
| `tools/TaskStopTool/prompt.ts` | 停止任务 prompt |
| `tools/TaskUpdateTool/TaskUpdateTool.ts` | 更新任务 |
| `tools/TaskUpdateTool/prompt.ts` | 更新任务 prompt |

#### Task 基础设施

| 文件路径 | 行数 | 说明 |
|----------|------|------|
| `utils/task/diskOutput.ts` | 451 | 任务磁盘输出 |
| `utils/task/TaskOutput.ts` | 390 | 任务输出管理 |
| `utils/task/framework.ts` | 308 | **任务框架** |
| `utils/task/outputFormatting.ts` | 38 | 输出格式化 |
| `utils/task/sdkProgress.ts` | 36 | SDK 进度 |
| `utils/tasks.ts` | — | 任务工具函数 |
| `utils/cronTasks.ts` | — | 定时任务 |
| `utils/cronTasksLock.ts` | — | 定时任务锁 |
| `hooks/useTasksV2.ts` | — | 任务 Hook |
| `hooks/useBackgroundTaskNavigation.ts` | — | 后台任务导航 |
| `hooks/useScheduledTasks.ts` | — | 定时任务 Hook |

### 17.7 Team（团队管理）

| 文件路径 | 说明 |
|----------|------|
| `tools/TeamCreateTool/TeamCreateTool.ts` | 创建团队 |
| `tools/TeamCreateTool/prompt.ts` | 创建团队 prompt |
| `tools/TeamDeleteTool/TeamDeleteTool.ts` | 删除团队 |
| `tools/TeamDeleteTool/prompt.ts` | 删除团队 prompt |
| `utils/teamDiscovery.ts` | 团队发现 |
| `utils/teamMemoryOps.ts` | 团队记忆操作 |
| `components/teams/TeamStatus.tsx` | 团队状态 UI |
| `components/teams/TeamsDialog.tsx` | 团队对话框 |

#### Team Memory 同步

| 文件路径 | 说明 |
|----------|------|
| `services/teamMemorySync/index.ts` | 团队记忆同步入口 |
| `services/teamMemorySync/watcher.ts` | 文件监听器 |
| `services/teamMemorySync/secretScanner.ts` | 密钥扫描 |
| `services/teamMemorySync/teamMemSecretGuard.ts` | 密钥保护 |
| `services/teamMemorySync/types.ts` | 类型定义 |
| `memdir/teamMemPaths.ts` | 团队记忆路径 |
| `memdir/teamMemPrompts.ts` | 团队记忆 prompt |

### 17.8 Agent 辅助支撑

| 文件路径 | 说明 |
|----------|------|
| `services/tools/toolOrchestration.ts` | **工具编排服务** |
| `services/AgentSummary/agentSummary.ts` | Agent 摘要服务 |
| `utils/agentContext.ts` | Agent 上下文 |
| `utils/agentId.ts` | Agent ID 生成 |
| `utils/agentSwarmsEnabled.ts` | Swarm 开关 |
| `utils/forkedAgent.ts` | fork Agent 工具 |
| `utils/standaloneAgent.ts` | 独立 Agent 运行 |
| `utils/model/agent.ts` | Agent 模型选择 |
| `utils/hooks/execAgentHook.ts` | Agent Hook 执行 |
| `utils/plugins/loadPluginAgents.ts` | 插件 Agent 加载 |
| `tools/SendMessageTool/SendMessageTool.ts` | Agent 间发送消息 |
| `tools/SendMessageTool/prompt.ts` | 发送消息 prompt |
| `skills/bundled/scheduleRemoteAgents.ts` | 远程 Agent 调度技能 |
| `entrypoints/agentSdkTypes.ts` | Agent SDK 类型 |
| `cli/handlers/agents.ts` | CLI Agent 处理 |
| `commands/agents/agents.tsx` | `/agents` 命令 |
| `commands/agents/index.ts` | 命令入口 |
| `commands/tasks/tasks.tsx` | `/tasks` 命令 |
| `commands/tasks/index.ts` | 命令入口 |

### 17.9 UI 组件（Agent 编排可视化）

| 文件路径 | 行数 | 说明 |
|----------|------|------|
| `components/AgentProgressLine.tsx` | — | Agent 进度行 |
| `components/ResumeTask.tsx` | — | 恢复任务组件 |
| `components/TaskListV2.tsx` | — | 任务列表 V2 |
| `components/agents/AgentDetail.tsx` | — | Agent 详情页 |
| `components/agents/AgentEditor.tsx` | — | Agent 编辑器 |
| `components/agents/AgentsList.tsx` | — | Agent 列表 |
| `components/agents/AgentsMenu.tsx` | — | Agent 菜单 |
| `components/agents/AgentNavigationFooter.tsx` | — | Agent 导航页脚 |
| `components/agents/types.ts` | — | Agent 组件类型 |
| `components/agents/utils.ts` | — | Agent 组件工具 |
| `components/agents/validateAgent.ts` | — | Agent 校验 |
| `components/agents/agentFileUtils.ts` | — | Agent 文件工具 |
| `components/agents/generateAgent.ts` | — | 生成 Agent |
| `components/agents/ColorPicker.tsx` | — | 颜色选择器 |
| `components/agents/ModelSelector.tsx` | — | 模型选择器 |
| `components/agents/ToolSelector.tsx` | — | 工具选择器 |
| `components/agents/new-agent-creation/CreateAgentWizard.tsx` | — | 创建 Agent 向导 |
| `components/tasks/BackgroundTask.tsx` | — | 后台任务 |
| `components/tasks/BackgroundTaskStatus.tsx` | — | 后台任务状态 |
| `components/tasks/BackgroundTasksDialog.tsx` | — | 后台任务对话框 |
| `components/tasks/AsyncAgentDetailDialog.tsx` | — | 异步 Agent 详情 |
| `components/tasks/InProcessTeammateDetailDialog.tsx` | 265 | 进程内 Teammate 详情 |
| `components/tasks/RemoteSessionDetailDialog.tsx` | — | 远程会话详情 |
| `components/tasks/RemoteSessionProgress.tsx` | — | 远程会话进度 |
| `components/tasks/ShellDetailDialog.tsx` | — | Shell 详情 |
| `components/tasks/ShellProgress.tsx` | — | Shell 进度 |
| `components/tasks/DreamDetailDialog.tsx` | — | Dream 详情 |
| `components/tasks/renderToolActivity.tsx` | — | 工具活动渲染 |
| `components/tasks/taskStatusUtils.tsx` | — | 任务状态工具 |
| `components/messages/TaskAssignmentMessage.tsx` | — | 任务分配消息 |
| `components/messages/UserAgentNotificationMessage.tsx` | — | Agent 通知消息 |
| `components/messages/teamMemCollapsed.tsx` | — | 团队记忆折叠 |
| `components/messages/teamMemSaved.ts` | — | 团队记忆保存 |
| `components/mcp/MCPAgentServerMenu.tsx` | — | MCP Agent 服务菜单 |

> **总计约 120+ 个文件**，核心编排逻辑集中在：
> 1. **`tools/AgentTool/`** — Agent 工具定义、运行、fork
> 2. **`tools/shared/spawnMultiAgent.ts`** — 多 Agent 并行生成
> 3. **`coordinator/coordinatorMode.ts`** — 协调器模式
> 4. **`utils/swarm/`** — Agent 集群管理（进程内运行、后端适配、权限同步等）
> 5. **`utils/teammateMailbox.ts`** — Agent 间通信
> 6. **`tasks/`** — 各类任务类型（Local/Remote/InProcess Agent Task）
