# Claude Code Python 严格翻译实现步骤

> **Python + asyncio + OpenAI 供应商模式** — 严格翻译自 TypeScript 原版
>
> 基于 [claude-code/ARCHITECTURE.md](../claude-code/ARCHITECTURE.md) 及源码逐行分析 | 2026-04-02
>
> **原则：所有 prompt、tool description、参数 schema 必须与原版 TypeScript 严格对齐，不可自由发挥**

---

## 翻译原则

### 必须严格对齐的内容

| 类别 | 说明 | 原版位置 |
|------|------|---------|
| **系统提示词** | 逐字翻译，不可改写、删减、重排 | `constants/prompts.ts` |
| **工具名称** | 使用原版 name 常量，如 `'Bash'`、`'Read'`、`'Edit'` | `tools/*/toolName.ts` |
| **工具描述 (description/prompt)** | 逐段对齐原版 `prompt.ts` 中的 `getSimplePrompt()`/`getDescription()` | `tools/*/prompt.ts` |
| **参数 Schema** | 字段名、类型、describe() 文本严格一致 | `tools/*/inputSchema` (Zod→JSON Schema) |
| **工具行为属性** | `isReadOnly`、`isConcurrencySafe`、`shouldDefer` 等必须与原版一致 | `Tool.ts` + 各工具定义 |
| **命令名称** | 原版命令名不可改动 | `commands.ts` |
| **权限模式** | 保持原版枚举值 | `types/permissions.ts` |
| **上下文格式** | Git Status / CLAUDE.md 的输出格式严格对齐 | `context.ts` |

### 允许适配的内容

| 类别 | 说明 |
|------|------|
| **API 调用格式** | Anthropic SDK → OpenAI SDK 消息格式（role/content/tool_calls） |
| **UI 框架** | React+Ink → Rich+prompt_toolkit |
| **并发模型** | JS Promise → Python asyncio |
| **进程管理** | Bun.spawn → asyncio.create_subprocess_exec |
| **类型系统** | TypeScript type → Python dataclass(frozen=True) + typing |

---

## 技术栈对照

| 维度 | TypeScript 原版 | Python 翻译版 |
|------|-----------------|---------------|
| 运行时 | Bun | Python 3.12+ / asyncio |
| 语言 | TypeScript (.ts/.tsx) | Python (.py)，dataclass(frozen=True) + typing |
| AI 接口 | `@anthropic-ai/sdk` (Anthropic) | `openai` SDK (OpenAI 兼容供应商模式) |
| UI 框架 | React + Ink | `rich` + `prompt_toolkit` |
| MCP 协议 | `@modelcontextprotocol/sdk` | `mcp` Python SDK |
| CLI 解析 | Commander.js | `argparse` / `click` |
| Schema 验证 | Zod | `pydantic` (BaseModel) |
| HTTP 客户端 | fetch (Bun 内置) | `httpx` (异步) |
| 状态管理 | 自研 Store + React Context | dataclass(frozen=True) Store |

---

## 目录

1. [阶段一：项目基础设施搭建](#阶段一项目基础设施搭建)
2. [阶段二：核心引擎层实现](#阶段二核心引擎层实现)
3. [阶段三：工具系统实现](#阶段三工具系统实现)
4. [阶段四：状态管理与终端 UI](#阶段四状态管理与终端-ui)
5. [阶段五：权限系统与安全](#阶段五权限系统与安全)
6. [阶段六：高级功能系统](#阶段六高级功能系统)
7. [阶段七：多代理与任务系统](#阶段七多代理与任务系统)
8. [阶段八：入口集成与辅助模块](#阶段八入口集成与辅助模块)
9. [总结](#总结实现路径依赖图)

---

## 阶段一：项目基础设施搭建

### 1.1 项目初始化与依赖

**状态**: ⬜ 待实现

- 初始化 Python 项目，配置 `pyproject.toml`
- 核心依赖：

| 依赖 | 用途 | 对应原版 |
|------|------|---------|
| `openai>=1.30.0` | LLM API（OpenAI 兼容供应商模式） | `@anthropic-ai/sdk` |
| `rich>=13.0.0` | 终端富文本渲染 | `ink` + React |
| `prompt_toolkit>=3.0.0` | 终端输入 | Ink TextInput |
| `httpx>=0.27.0` | 异步 HTTP | fetch (Bun) |
| `pydantic>=2.0` | Schema 验证 | `zod` |
| `mcp>=1.0.0` | MCP 协议 | `@modelcontextprotocol/sdk` |

- 开发依赖：`pytest`, `pytest-asyncio`, `ruff`, `mypy`

- 目录结构（严格对应原版）：

```
claude-code-py/
  ├── pyproject.toml
  ├── requirements.txt
  └── claude_code/
      ├── __init__.py
      ├── __main__.py           # python -m claude_code 入口
      ├── main.py               # CLI 主程序（对应 main.tsx）
      ├── config.py             # 配置管理（对应 entrypoints/init.ts）
      ├── types.py              # 核心类型（对应 Tool.ts + types/）
      │
      ├── constants/            # 严格翻译原版 constants/
      │   ├── __init__.py
      │   ├── prompts.py        # 系统提示词（逐字翻译 constants/prompts.ts）
      │   └── cyber_risk.py     # CYBER_RISK_INSTRUCTION（逐字翻译）
      │
      ├── engine/               # 核心引擎（对应 QueryEngine.ts + query.ts）
      │   ├── __init__.py
      │   ├── query_engine.py   # QueryEngine 类
      │   ├── query.py          # query() 循环
      │   └── context.py        # 上下文构建（对应 context.ts）
      │
      ├── services/             # 服务层
      │   ├── api/              # LLM API
      │   │   ├── client.py     # 对应 services/api/claude.ts
      │   │   ├── retry.py      # 对应 services/api/withRetry.ts
      │   │   └── usage.py      # 对应 services/api/usage.ts
      │   ├── compact/          # 上下文压缩
      │   │   ├── compact.py    # 对应 compact.ts
      │   │   └── auto.py       # 对应 autoCompact.ts
      │   ├── mcp/              # MCP 客户端
      │   │   ├── client.py     # 对应 services/mcp/client.ts
      │   │   └── types.py      # 对应 services/mcp/types.ts
      │   └── tools/            # 工具编排
      │       ├── orchestration.py  # 对应 toolOrchestration.ts
      │       └── executor.py       # 对应 StreamingToolExecutor.ts
      │
      ├── tools/                # 内置工具（对应 tools/）
      │   ├── __init__.py       # 工具注册（对应 tools.ts）
      │   ├── base.py           # 工具基类（对应 Tool.ts）
      │   ├── tool_names.py     # 工具名称常量（对应各 toolName.ts）
      │   ├── bash_tool.py      # 对应 tools/BashTool/
      │   ├── file_read_tool.py # 对应 tools/FileReadTool/
      │   ├── file_edit_tool.py # 对应 tools/FileEditTool/
      │   ├── file_write_tool.py# 对应 tools/FileWriteTool/
      │   ├── glob_tool.py      # 对应 tools/GlobTool/
      │   ├── grep_tool.py      # 对应 tools/GrepTool/
      │   ├── agent_tool.py     # 对应 tools/AgentTool/
      │   ├── web_fetch_tool.py # 对应 tools/WebFetchTool/
      │   ├── web_search_tool.py# 对应 tools/WebSearchTool/
      │   ├── todo_write_tool.py# 对应 tools/TodoWriteTool/
      │   ├── notebook_edit_tool.py # 对应 tools/NotebookEditTool/
      │   ├── ask_user_question_tool.py # 对应 tools/AskUserQuestionTool/
      │   ├── task_output_tool.py   # 对应 tools/TaskOutputTool/
      │   ├── task_stop_tool.py     # 对应 tools/TaskStopTool/
      │   ├── task_create_tool.py   # 对应 tools/TaskCreateTool/
      │   ├── task_get_tool.py      # 对应 tools/TaskGetTool/
      │   ├── task_update_tool.py   # 对应 tools/TaskUpdateTool/
      │   ├── task_list_tool.py     # 对应 tools/TaskListTool/
      │   ├── skill_tool.py         # 对应 tools/SkillTool/
      │   ├── plan_mode_tool.py     # 对应 EnterPlanModeTool + ExitPlanModeV2Tool
      │   ├── tool_search_tool.py   # 对应 tools/ToolSearchTool/
      │   ├── send_message_tool.py  # 对应 tools/SendMessageTool/
      │   ├── config_tool.py        # 对应 tools/ConfigTool/
      │   ├── brief_tool.py         # 对应 tools/BriefTool/
      │   ├── sleep_tool.py         # 对应 tools/SleepTool/
      │   ├── worktree_tool.py      # 对应 EnterWorktreeTool + ExitWorktreeTool
      │   ├── mcp_tool.py           # 对应 tools/MCPTool/
      │   ├── list_mcp_resources_tool.py  # 对应 ListMcpResourcesTool
      │   └── read_mcp_resource_tool.py   # 对应 ReadMcpResourceTool
      │
      ├── state/                # 状态管理
      │   ├── store.py          # 对应 state/store.ts
      │   └── app_state.py      # 对应 state/AppStateStore.ts
      │
      ├── ui/                   # 终端 UI
      │   ├── repl.py           # 对应 screens/REPL.tsx
      │   ├── renderer.py       # 对应 ink/renderer.ts
      │   ├── prompt.py         # 对应 PromptInput/
      │   └── components/       # UI 组件
      │
      ├── permissions/          # 权限系统（对应 utils/permissions/）
      │   ├── modes.py          # 对应 PermissionMode.ts
      │   ├── checker.py        # 对应 permissions.ts
      │   ├── rules.py          # 对应 PermissionRule.ts
      │   ├── classifier.py     # 对应 bashClassifier.ts
      │   └── path_validator.py # 对应 pathValidation.ts
      │
      ├── commands/             # 斜杠命令（对应 commands/）
      │   ├── registry.py       # 命令注册
      │   └── ...               # 各命令实现
      │
      ├── tasks/                # 任务系统（对应 tasks/）
      │   ├── task.py           # 对应 Task.ts
      │   ├── manager.py        # 任务管理
      │   └── local_agent.py    # 对应 LocalAgentTask/
      │
      ├── memory/               # 记忆系统（对应 memdir/）
      │   ├── memdir.py         # 对应 memdir.ts
      │   └── retrieval.py      # 对应 findRelevantMemories.ts
      │
      └── utils/                # 工具函数（对应 utils/）
          ├── git.py            # 对应 utils/git/
          ├── claudemd.py       # 对应 utils/claudemd.ts（getClaudeMds 逻辑）
          ├── cost_tracker.py   # 对应 cost-tracker.ts
          └── hooks.py          # 对应 utils/hooks/
```

### 1.2 核心类型定义（`types.py`）

**状态**: ⬜ 待实现

严格对应原版 `Tool.ts` + `types/` 目录。权限类型值必须与原版 `types/permissions.ts` 一致：

```python
# 原版: type PermissionMode = 'acceptEdits' | 'bypassPermissions' | 'default' | 'dontAsk' | 'plan' | 'auto' | 'bubble'
PermissionMode = Literal[
    "acceptEdits", "bypassPermissions", "default", "dontAsk", "plan", "auto", "bubble"
]

# 原版: type PermissionBehavior = 'allow' | 'deny' | 'ask'
PermissionBehavior = Literal["allow", "deny", "ask"]

# 原版: type PermissionRuleSource = ...
PermissionRuleSource = Literal[
    "userSettings", "projectSettings", "localSettings",
    "flagSettings", "policySettings", "cliArg", "command", "session"
]

# 原版 branded types
SessionId = NewType("SessionId", str)  # string & { __brand: 'SessionId' }
AgentId = NewType("AgentId", str)      # string & { __brand: 'AgentId' }
```

### 1.3 系统提示词常量（`constants/prompts.py`）

**状态**: ⬜ 待实现

**必须逐字翻译**原版 `constants/prompts.ts` 和 `constants/cyberRiskInstruction.ts`。以下为原版关键原文（翻译时必须完整保留）：

```python
# ---------- 逐字翻译自 constants/cyberRiskInstruction.ts ----------
CYBER_RISK_INSTRUCTION = (
    "IMPORTANT: Assist with authorized security testing, defensive security, "
    "CTF challenges, and educational contexts. Refuse requests for destructive "
    "techniques, DoS attacks, mass targeting, supply chain compromise, or "
    "detection evasion for malicious purposes. Dual-use security tools "
    "(C2 frameworks, credential testing, exploit development) require clear "
    "authorization context: pentesting engagements, CTF competitions, "
    "security research, or defensive use cases."
)

SYSTEM_PROMPT_DYNAMIC_BOUNDARY = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"

DEFAULT_AGENT_PROMPT = (
    "You are an agent for Claude Code, Anthropic's official CLI for Claude. "
    "Given the user's message, you should use the tools available to complete "
    "the task. Complete the task fully—don't gold-plate, but don't leave it "
    "half-done. When you complete the task, respond with a concise report "
    "covering what was done and any key findings — the caller will relay this "
    "to the user, so it only needs the essentials."
)
```

系统提示词函数结构（每个函数的完整文本见 `SOURCE_EXTRACTION.md`，翻译时逐字保留）：

```python
def get_simple_intro_section() -> str:
    """翻译自 getSimpleIntroSection() — constants/prompts.ts:175"""

def get_simple_system_section() -> str:
    """翻译自 getSimpleSystemSection() — constants/prompts.ts:186"""

def get_simple_doing_tasks_section() -> str:
    """翻译自 getSimpleDoingTasksSection() — constants/prompts.ts:199"""

def get_actions_section() -> str:
    """翻译自 getActionsSection() — constants/prompts.ts:255"""

def get_using_your_tools_section(enabled_tools: set[str]) -> str:
    """翻译自 getUsingYourToolsSection() — constants/prompts.ts:269"""

def get_simple_tone_and_style_section() -> str:
    """翻译自 getSimpleToneAndStyleSection() — constants/prompts.ts:430"""

def get_output_efficiency_section() -> str:
    """翻译自 getOutputEfficiencySection() — constants/prompts.ts:403"""

def get_system_prompt(tools: list, model: str) -> list[str]:
    """翻译自 getSystemPrompt() — 段顺序不可改动:
    1. get_simple_intro_section()
    2. get_simple_system_section()
    3. get_simple_doing_tasks_section()
    4. get_actions_section()
    5. get_using_your_tools_section(enabled_tools)
    6. get_simple_tone_and_style_section()
    7. get_output_efficiency_section()
    8. SYSTEM_PROMPT_DYNAMIC_BOUNDARY
    9+. 动态段 (session_guidance, memory, env_info, language, mcp_instructions...)
    """
```

### 1.4 配置管理（`config.py`）

**状态**: ⬜ 待实现

对应原版 `entrypoints/init.ts` + `QueryEngineConfig`。

---

## 阶段二：核心引擎层实现

### 2.1 OpenAI 兼容 API 客户端（`services/api/client.py`）

**状态**: ⬜ 待实现

对应原版 `services/api/claude.ts`（~3420 行）。消息规范化和流式解析逻辑必须严格对齐。

### 2.2 重试策略（`services/api/retry.py`）

**状态**: ⬜ 待实现

对应原版 `services/api/withRetry.ts`。

```python
MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3  # 严格对齐原版常量
```

### 2.3 Context 构建（`engine/context.py`）

**状态**: ⬜ 待实现

严格翻译 `context.ts`，**Git Status 输出格式必须与原版完全一致**：

```python
async def get_git_status(cwd: str) -> str | None:
    """严格翻译 context.ts getGitStatus()。

    原版输出格式（不可改动）:
    '''
    This is the git status at the start of the conversation. Note that this status is a snapshot in time, and will not update during the conversation.

    Current branch: {branch}

    Main branch (you will usually use this for PRs): {main_branch}

    Git user: {user_name}

    Status:
    {status or '(clean)'}

    Recent commits:
    {log}
    '''

    原版实现要点:
    - 并行执行 5 个 git 命令: branch, defaultBranch, status --short, log --oneline -n 5, config user.name
    - status 超过 MAX_STATUS_CHARS(2000) 时截断并附加提示文本
    """


async def get_user_context(cwd: str) -> dict[str, str]:
    """严格翻译 context.ts getUserContext()。

    返回:
    - claudeMd: getClaudeMds() 结果
    - currentDate: "Today's date is {ISO date}."
    """


async def get_system_context(cwd: str) -> dict[str, str]:
    """严格翻译 context.ts getSystemContext()。

    返回:
    - gitStatus: get_git_status() 结果
    """
```

### 2.4 CLAUDE.md 加载（`utils/claudemd.py`）

**状态**: ⬜ 待实现

严格翻译 `utils/claudemd.ts` 中 `getClaudeMds()` 逻辑：

```python
# 原版 memory type → description 映射（逐字保留，不可改动）:
MEMORY_TYPE_DESCRIPTIONS = {
    "Project": " (project instructions, checked into the codebase)",
    "Local": " (user's private project instructions, not checked in)",
    "TeamMem": " (shared team memory, synced across the organization)",
    "AutoMem": " (user's auto-memory, persists across conversations)",
    "User": " (user's private global instructions for all projects)",
}

# 原版输出格式: "Contents of {path}{description}:\n\n{content}"
```

### 2.5 Query Loop（`engine/query.py`）

**状态**: ⬜ 待实现

严格翻译 `query.ts`（~1730 行）中的 `queryLoop()` 结构：

```python
@dataclass
class QueryState:
    """严格翻译 query.ts 内部 State 类型。"""
    messages: list[Message]
    auto_compact_tracking: Any = None
    max_output_tokens_recovery_count: int = 0
    has_attempted_reactive_compact: bool = False
    turn_count: int = 0
    pending_tool_use_summary: Any = None
    max_output_tokens_override: int | None = None
    stop_hook_active: bool | None = None


async def query(params: QueryParams) -> AsyncIterator[StreamEvent]:
    """核心查询循环 — 严格翻译 query.ts queryLoop()。

    循环结构（不可改动）:
    while True:
        1. 技能发现预取
        2. yield stream_request_start
        3. 调用 streaming API
        4. 运行工具编排 runTools()
        5. 应用工具结果预算
        6. 处理 auto-compact
        7. 增加 turn_count
        8. 检查 max_turns
        9. 无工具调用 → break
    """
```

### 2.6 QueryEngine（`engine/query_engine.py`）

**状态**: ⬜ 待实现

严格翻译 `QueryEngine.ts`（~1296 行）。

---

## 阶段三：工具系统实现

### 3.1 工具基类（`tools/base.py`）

**状态**: ⬜ 待实现

严格翻译 `Tool.ts` 接口（~793 行）。必须实现所有原版字段和方法。

### 3.2 工具名称常量（`tools/tool_names.py`）

**状态**: ⬜ 待实现

逐字翻译自原版各工具的 `toolName.ts`：

```python
BASH_TOOL_NAME = "Bash"
FILE_READ_TOOL_NAME = "Read"
FILE_WRITE_TOOL_NAME = "Write"
FILE_EDIT_TOOL_NAME = "Edit"
GLOB_TOOL_NAME = "Glob"
GREP_TOOL_NAME = "Grep"
AGENT_TOOL_NAME = "Agent"
LEGACY_AGENT_TOOL_NAME = "Task"
TASK_OUTPUT_TOOL_NAME = "TaskOutput"
TASK_STOP_TOOL_NAME = "TaskStop"
TASK_CREATE_TOOL_NAME = "TaskCreate"
TASK_GET_TOOL_NAME = "TaskGet"
TASK_UPDATE_TOOL_NAME = "TaskUpdate"
TASK_LIST_TOOL_NAME = "TaskList"
WEB_FETCH_TOOL_NAME = "WebFetch"
WEB_SEARCH_TOOL_NAME = "WebSearch"
TODO_WRITE_TOOL_NAME = "TodoWrite"
NOTEBOOK_EDIT_TOOL_NAME = "NotebookEdit"
ASK_USER_QUESTION_TOOL_NAME = "AskUserQuestion"
SKILL_TOOL_NAME = "Skill"
ENTER_PLAN_MODE_TOOL_NAME = "EnterPlanMode"
EXIT_PLAN_MODE_TOOL_NAME = "ExitPlanMode"
TOOL_SEARCH_TOOL_NAME = "ToolSearch"
SEND_MESSAGE_TOOL_NAME = "SendMessage"
CONFIG_TOOL_NAME = "Config"
ENTER_WORKTREE_TOOL_NAME = "EnterWorktree"
EXIT_WORKTREE_TOOL_NAME = "ExitWorktree"
LSP_TOOL_NAME = "LSP"
LIST_MCP_RESOURCES_TOOL_NAME = "ListMcpResourcesTool"
READ_MCP_RESOURCE_TOOL_NAME = "ReadMcpResourceTool"
SYNTHETIC_OUTPUT_TOOL_NAME = "StructuredOutput"
TEAM_CREATE_TOOL_NAME = "TeamCreate"
TEAM_DELETE_TOOL_NAME = "TeamDelete"
BRIEF_TOOL_NAME = "Brief"
SLEEP_TOOL_NAME = "Sleep"
```

### 3.3 各工具严格翻译

**所有工具状态**: ⬜ 待实现

以下为每个工具的严格对齐规范。**description/prompt 文本和 parameters 的 describe 文本直接取自原版源码，翻译时必须完整保留**。

#### 3.3.1 BashTool（`tools/bash_tool.py`）

- **原版位置**: `tools/BashTool/prompt.ts` + `toolName.ts` + `inputSchema.ts`
- **name**: `"Bash"`
- **isReadOnly**: 动态判定（原版 `readOnlyValidation.ts`）
- **isConcurrencySafe**: `false`（原版默认）
- **shouldDefer**: `false`
- **description**: 必须完整翻译 `getSimplePrompt()` 返回的全部文本，包含：
  - 开头 `'Executes a given bash command and returns its output.'`
  - 工具偏好指导（Read/Edit/Write/Glob/Grep 优于 Bash）
  - 全部 Instructions 段（ls 验证、引号路径、cwd 保持、timeout、多命令规则、git 规则、sleep 规则）
  - sandbox 段
- **parameters** (原版 Zod → JSON Schema):
  - `command`: string — `"The command to execute"`
  - `timeout`: number, optional — `"Optional timeout in milliseconds (max {MAX_TIMEOUT_MS})"`
  - `description`: string, optional — 完整 describe 文本（含示例，见 SOURCE_EXTRACTION §2.1）
  - `run_in_background`: boolean, optional — `"Set to true to run this command in the background. Use Read to read the output later."`

#### 3.3.2 FileReadTool（`tools/file_read_tool.py`）

- **原版位置**: `tools/FileReadTool/FileReadTool.ts`
- **name**: `"Read"`
- **description**: `"Read a file from the local filesystem."`
- **isReadOnly**: `true`
- **isConcurrencySafe**: `true`
- **parameters**:
  - `file_path`: string — `"Absolute path to the file to read"`
  - `offset`: number, optional
  - `limit`: number, optional

#### 3.3.3 FileWriteTool（`tools/file_write_tool.py`）

- **原版位置**: `tools/FileWriteTool/inputSchema.ts` + `prompt.ts`
- **name**: `"Write"`
- **description**: `"Write a file to the local filesystem."`
- **isReadOnly**: `false`
- **isConcurrencySafe**: `false`
- **strict**: `true`
- **searchHint**: `"create or overwrite files"`
- **parameters**:
  - `file_path`: string — `"The absolute path to the file to write (must be absolute, not relative)"`
  - `content`: string — `"The content to write to the file"`

#### 3.3.4 FileEditTool（`tools/file_edit_tool.py`）

- **原版位置**: `tools/FileEditTool/inputSchema.ts` + `prompt.ts`
- **name**: `"Edit"`
- **description**: `"A tool for editing files"`
- **isReadOnly**: `false`
- **isConcurrencySafe**: `false`
- **strict**: `true`
- **searchHint**: `"modify file contents in place"`
- **parameters**:
  - `file_path`: string — `"The absolute path to the file to modify"`
  - `old_string`: string — `"The text to replace"`
  - `new_string`: string — `"The text to replace it with (must be different from old_string)"`
  - `replace_all`: boolean, optional — `"Replace all occurrences of old_string (default false)"`

#### 3.3.5 GlobTool（`tools/glob_tool.py`）

- **原版位置**: `tools/GlobTool/prompt.ts` + `inputSchema.ts`
- **name**: `"Glob"`
- **isReadOnly**: `true`
- **isConcurrencySafe**: `true`
- **searchHint**: `"find files by name pattern or wildcard"`
- **description**（多行，必须完整保留）:
  ```
  - Fast file pattern matching tool that works with any codebase size
  - Supports glob patterns like "**/*.js" or "src/**/*.ts"
  - Returns matching file paths sorted by modification time
  - Use this tool when you need to find files by name patterns
  - When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the Agent tool instead
  ```
- **parameters**:
  - `pattern`: string — `"The glob pattern to match files against"`
  - `path`: string, optional — `"The directory to search in. If not specified, the current working directory will be used. IMPORTANT: Omit this field to use the default directory. DO NOT enter \"undefined\" or \"null\" - simply omit it for the default behavior. Must be a valid directory path if provided."`

#### 3.3.6 GrepTool（`tools/grep_tool.py`）

- **原版位置**: `tools/GrepTool/prompt.ts` + `inputSchema.ts`
- **name**: `"Grep"`
- **isReadOnly**: `true`
- **isConcurrencySafe**: `true`
- **description**: 必须完整翻译 `getDescription()` 全文（见 SOURCE_EXTRACTION §6）
- **parameters**（字段多，describe 文本全部逐字保留）:
  - `pattern`: string — `"The regular expression pattern to search for in file contents"`
  - `path`: string, optional — `"File or directory to search in (rg PATH). Defaults to current working directory."`
  - `glob`: string, optional — `'Glob pattern to filter files (e.g. "*.js", "*.{ts,tsx}") - maps to rg --glob'`
  - `output_mode`: enum — 完整描述（见 SOURCE_EXTRACTION §2.6）
  - `-i`, `type`, `head_limit`, `multiline`, `-B`, `-A`, `-C`, `context`, `-n`, `offset` — 每个字段的 describe 原文见源码

#### 3.3.7 AgentTool（`tools/agent_tool.py`）

- **原版位置**: `tools/AgentTool/prompt.ts` + `inputSchema.ts` + `constants.ts`
- **name**: `"Agent"`
- **aliases**: `["Task"]`
- **isReadOnly**: `false`
- **isConcurrencySafe**: `false`
- **shouldDefer**: 动态
- **description**: 必须翻译 `getPrompt()` 全部文本（见 SOURCE_EXTRACTION §9）
- **parameters**:
  - `description`: string — `"A short (3-5 word) description of the task"`
  - `prompt`: string — `"The task for the agent to perform"`
  - `subagent_type`: string, optional — `"The type of specialized agent to use for this task"`
  - `model`: enum, optional — `"Optional model override for this agent..."`
  - `run_in_background`: boolean, optional — `"Set to true to run this agent in the background..."`

#### 3.3.8 WebFetchTool（`tools/web_fetch_tool.py`）

- **原版位置**: `tools/WebFetchTool/prompt.ts`
- **name**: `"WebFetch"`
- **userFacingName**: `"Fetch"`
- **isReadOnly**: `true`
- **isConcurrencySafe**: `true`
- **shouldDefer**: `true`
- **searchHint**: `"fetch and extract content from a URL"`
- **description**: 完整翻译 `DESCRIPTION` 常量（见 SOURCE_EXTRACTION §7）
- **parameters**:
  - `url`: string — `"The URL to fetch content from"`
  - `prompt`: string — `"The prompt to run on the fetched content"`

#### 3.3.9 WebSearchTool（`tools/web_search_tool.py`）

- **原版位置**: `tools/WebSearchTool/prompt.ts`
- **name**: `"WebSearch"`
- **isReadOnly**: `true`
- **isConcurrencySafe**: `true`
- **shouldDefer**: `true`
- **description**: 完整翻译 `getWebSearchPrompt()` 全文（见 SOURCE_EXTRACTION §8），包含 CRITICAL REQUIREMENT 段和 IMPORTANT 年份段
- **parameters**:
  - `query`: string — `"The search query to use"`
  - `allowed_domains`: array[string], optional — `"Only include search results from these domains"`
  - `blocked_domains`: array[string], optional — `"Never include search results from these domains"`

#### 3.3.10 TodoWriteTool（`tools/todo_write_tool.py`）

- **原版位置**: `tools/TodoWriteTool/prompt.ts`
- **name**: `"TodoWrite"`
- **shouldDefer**: `true`
- **strict**: `true`
- **searchHint**: `"manage the session task checklist"`
- **description**: `"Update the todo list for the current session. To be used proactively and often to track progress and pending tasks. Make sure that at least one task is in_progress at all times. Always provide both content (imperative) and activeForm (present continuous) for each task."`

#### 3.3.11–3.3.30 其他工具属性速查

| # | 工具 | name | isReadOnly | isConcurrencySafe | shouldDefer | searchHint | 原版位置 |
|---|------|------|------------|-------------------|-------------|------------|---------|
| 11 | NotebookEditTool | `"NotebookEdit"` | false | false | true | `"edit Jupyter notebook cells (.ipynb)"` | `tools/NotebookEditTool/` |
| 12 | AskUserQuestionTool | `"AskUserQuestion"` | true | true | true | `"prompt the user with a multiple-choice question"` | `tools/AskUserQuestionTool/` |
| 13 | TaskOutputTool | `"TaskOutput"` | false | false | false | — | `tools/TaskOutputTool/` |
| 14 | TaskStopTool | `"TaskStop"` (aliases: `["KillShell"]`) | false | true | true | `"kill a running background task"` | `tools/TaskStopTool/` |
| 15 | TaskCreateTool | `"TaskCreate"` | false | false | false | — | `tools/TaskCreateTool/` |
| 16 | TaskGetTool | `"TaskGet"` | true | true | false | — | `tools/TaskGetTool/` |
| 17 | TaskUpdateTool | `"TaskUpdate"` | false | false | false | — | `tools/TaskUpdateTool/` |
| 18 | TaskListTool | `"TaskList"` | true | true | false | — | `tools/TaskListTool/` |
| 19 | SkillTool | `"Skill"` | false | false | false | — | `tools/SkillTool/` |
| 20 | EnterPlanModeTool | `"EnterPlanMode"` | true | true | true | `"switch to plan mode to design an approach before coding"` | `tools/EnterPlanModeTool/` |
| 21 | ExitPlanModeTool | `"ExitPlanMode"` | false | false | false | — | `tools/ExitPlanModeV2Tool/` |
| 22 | ToolSearchTool | `"ToolSearch"` | true | true | false | — | `tools/ToolSearchTool/` |
| 23 | SendMessageTool | `"SendMessage"` | false | false | false | — | `tools/SendMessageTool/` |
| 24 | ConfigTool | `"Config"` | false | false | false | — | `tools/ConfigTool/` |
| 25 | BriefTool | `"Brief"` | false | false | false | — | `tools/BriefTool/` |
| 26 | SleepTool | `"Sleep"` | false | false | false | — | `tools/SleepTool/` |
| 27 | EnterWorktreeTool | `"EnterWorktree"` | false | false | false | — | `tools/EnterWorktreeTool/` |
| 28 | ExitWorktreeTool | `"ExitWorktree"` | false | false | false | — | `tools/ExitWorktreeTool/` |
| 29 | ListMcpResourcesTool | `"ListMcpResourcesTool"` | true | true | true | — | `tools/MCPTool/` |
| 30 | ReadMcpResourceTool | `"ReadMcpResourceTool"` | true | true | true | — | `tools/MCPTool/` |

### 3.4 工具注册（`tools/__init__.py`）

**状态**: ⬜ 待实现

严格翻译 `tools.ts getAllBaseTools()` 的注册顺序（不可改动）：

```python
def get_all_base_tools() -> list[BaseTool]:
    """原版注册顺序:
    1.  AgentTool
    2.  TaskOutputTool
    3.  BashTool
    4.  GlobTool
    5.  GrepTool
    6.  ExitPlanModeTool
    7.  FileReadTool
    8.  FileEditTool
    9.  FileWriteTool
    10. NotebookEditTool
    11. WebFetchTool
    12. TodoWriteTool
    13. WebSearchTool
    14. TaskStopTool
    15. AskUserQuestionTool
    16. SkillTool
    17. EnterPlanModeTool
    18. TaskCreateTool, TaskGetTool, TaskUpdateTool, TaskListTool
    19. SendMessageTool
    20. BriefTool
    21. ListMcpResourcesTool
    22. ReadMcpResourceTool
    23. ToolSearchTool
    """
```

工具集合常量（严格翻译原版）：

```python
ALL_AGENT_DISALLOWED_TOOLS = frozenset([
    "TaskOutput", "ExitPlanMode", "EnterPlanMode", "AskUserQuestion", "TaskStop",
])

ASYNC_AGENT_ALLOWED_TOOLS = frozenset([
    "Read", "WebSearch", "TodoWrite", "Grep", "WebFetch", "Glob",
    "Bash", "Edit", "Write", "NotebookEdit", "Skill", "StructuredOutput",
    "ToolSearch", "EnterWorktree", "ExitWorktree",
])

COORDINATOR_MODE_ALLOWED_TOOLS = frozenset([
    "Agent", "TaskStop", "SendMessage", "StructuredOutput",
])
```

### 3.5 工具编排（`services/tools/orchestration.py`）

**状态**: ⬜ 待实现

严格翻译 `services/tools/toolOrchestration.ts`。

### 3.6 流式工具执行器（`services/tools/executor.py`）

**状态**: ⬜ 待实现

严格翻译 `services/tools/StreamingToolExecutor.ts`。

---

## 阶段四：状态管理与终端 UI

### 4.1 不可变 Store（`state/store.py`）

**状态**: ⬜ 待实现

翻译 `state/store.ts`。

### 4.2 AppState（`state/app_state.py`）

**状态**: ⬜ 待实现

翻译 `state/AppStateStore.ts`（~570 行），保留原版字段名映射。

### 4.3 终端 UI（`ui/`）

**状态**: ⬜ 待实现

| 文件 | 对应原版 |
|------|---------|
| `ui/repl.py` | `screens/REPL.tsx` |
| `ui/renderer.py` | `ink/renderer.ts` |
| `ui/prompt.py` | `PromptInput/` |
| `ui/components/` | `components/` |

---

## 阶段五：权限系统与安全

### 5.1 权限模式（`permissions/modes.py`）

**状态**: ⬜ 待实现

严格翻译 `types/permissions.ts`。

### 5.2 权限检查（`permissions/checker.py`）

**状态**: ⬜ 待实现

翻译 `utils/permissions/permissions.ts`。

### 5.3 Bash 命令安全分类（`permissions/classifier.py`）

**状态**: ⬜ 待实现

翻译 `utils/permissions/bashClassifier.ts` + `dangerousPatterns.ts`。

### 5.4 路径验证（`permissions/path_validator.py`）

**状态**: ⬜ 待实现

翻译 `utils/permissions/pathValidation.ts`。

---

## 阶段六：高级功能系统

### 6.1 命令系统（`commands/`）

**状态**: ⬜ 待实现

严格翻译 `commands.ts`（~755 行）+ `types/command.ts`。原版内置命令列表（不可自由增减）：

```
clear, compact, config, cost, commit, commit-push-pr, diff, doctor,
exit, export, help, init, init-verifiers, login, logout, mcp, memory,
model, onboarding, permissions, pr_comments, resume, review, session,
share, skills, stats, status, tasks, theme, usage, vim, ...
```

### 6.2 Compact 上下文压缩（`services/compact/`）

**状态**: ⬜ 待实现

翻译 `services/compact/compact.ts`（~1706 行）+ `autoCompact.ts`。

### 6.3 MCP 协议（`services/mcp/`）

**状态**: ⬜ 待实现

翻译 `services/mcp/client.ts`（~3349 行）。

### 6.4 记忆系统（`memory/`）

**状态**: ⬜ 待实现

翻译 `memdir/memdir.ts`（~508 行）。常量不可改动：

```python
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25 * 1024  # 25KB
```

---

## 阶段七：多代理与任务系统

### 7.1 任务类型（`tasks/task.py`）

**状态**: ⬜ 待实现

严格翻译 `Task.ts` 枚举值：

```python
TaskType = Literal[
    "local_bash", "local_agent", "remote_agent",
    "in_process_teammate", "local_workflow", "monitor_mcp", "dream"
]
TaskStatus = Literal["pending", "running", "completed", "failed", "killed"]
```

### 7.2 任务管理（`tasks/manager.py`）

**状态**: ⬜ 待实现

### 7.3 子代理（`tasks/local_agent.py`）

**状态**: ⬜ 待实现

子代理系统提示词必须使用 `DEFAULT_AGENT_PROMPT`（见 §1.3）。

### 7.4 多代理协作

**状态**: ⬜ 待实现

翻译 `utils/swarm/` + `coordinator/`。

---

## 阶段八：入口集成与辅助模块

### 8.1 CLI 主程序（`main.py`）

**状态**: ⬜ 待实现

翻译 `main.tsx`（~4684 行）+ `entrypoints/cli.tsx`。

### 8.2 初始化流程

**状态**: ⬜ 待实现

翻译 `entrypoints/init.ts` 初始化链。

### 8.3 辅助模块

**状态**: 全部 ⬜ 待实现

| 模块 | 对应原版 |
|------|---------|
| `utils/git.py` | `utils/git/` |
| `utils/claudemd.py` | `utils/claudemd.ts` |
| `utils/cost_tracker.py` | `cost-tracker.ts` |
| `utils/hooks.py` | `utils/hooks/` |
| `utils/settings.py` | `utils/settings/` |

---

## 总结：实现路径依赖图

```
阶段一 基础设施 ───→ 阶段二 核心引擎 ───→ 阶段三 工具系统
 (types.py             (LLMClient            (BaseTool
  constants/prompts.py   query.py              30+ 工具严格翻译
  config.py)             query_engine.py       orchestration.py
                         context.py)           executor.py)
       │                      │                      │
       ▼                      ▼                      ▼
 阶段四 状态+UI  ←──── 阶段五 权限系统
  (Store                  (PermissionMode
   Rich 渲染                BashClassifier
   REPL 循环)               路径验证)
       │
       ▼
 阶段六 高级功能
  (命令 / Compact / MCP / 记忆)
       │
       ▼
 阶段七 多代理+任务
  (Task / Agent / Swarm)
       │
       ▼
 阶段八 入口集成
  (main.py / CLI / init)
```

---

## 实现进度追踪

| # | 模块 | 状态 | 对应原版文件 |
|---|------|------|-------------|
| 1 | `pyproject.toml` | ⬜ | `package.json` |
| 2 | `types.py` | ⬜ | `Tool.ts` + `types/` |
| 3 | `constants/prompts.py` | ⬜ | `constants/prompts.ts` |
| 4 | `constants/cyber_risk.py` | ⬜ | `constants/cyberRiskInstruction.ts` |
| 5 | `config.py` | ⬜ | `entrypoints/init.ts` |
| 6 | `tools/tool_names.py` | ⬜ | 各 `toolName.ts` |
| 7 | `tools/base.py` | ⬜ | `Tool.ts` |
| 8 | `tools/bash_tool.py` | ⬜ | `tools/BashTool/` |
| 9 | `tools/file_read_tool.py` | ⬜ | `tools/FileReadTool/` |
| 10 | `tools/file_edit_tool.py` | ⬜ | `tools/FileEditTool/` |
| 11 | `tools/file_write_tool.py` | ⬜ | `tools/FileWriteTool/` |
| 12 | `tools/glob_tool.py` | ⬜ | `tools/GlobTool/` |
| 13 | `tools/grep_tool.py` | ⬜ | `tools/GrepTool/` |
| 14 | `tools/agent_tool.py` | ⬜ | `tools/AgentTool/` |
| 15 | `tools/web_fetch_tool.py` | ⬜ | `tools/WebFetchTool/` |
| 16 | `tools/web_search_tool.py` | ⬜ | `tools/WebSearchTool/` |
| 17 | `tools/todo_write_tool.py` | ⬜ | `tools/TodoWriteTool/` |
| 18 | `tools/notebook_edit_tool.py` | ⬜ | `tools/NotebookEditTool/` |
| 19 | `tools/ask_user_question_tool.py` | ⬜ | `tools/AskUserQuestionTool/` |
| 20 | `tools/task_output_tool.py` | ⬜ | `tools/TaskOutputTool/` |
| 21 | `tools/task_stop_tool.py` | ⬜ | `tools/TaskStopTool/` |
| 22 | `tools/task_create_tool.py` | ⬜ | `tools/TaskCreateTool/` |
| 23 | `tools/task_get_tool.py` | ⬜ | `tools/TaskGetTool/` |
| 24 | `tools/task_update_tool.py` | ⬜ | `tools/TaskUpdateTool/` |
| 25 | `tools/task_list_tool.py` | ⬜ | `tools/TaskListTool/` |
| 26 | `tools/skill_tool.py` | ⬜ | `tools/SkillTool/` |
| 27 | `tools/plan_mode_tool.py` | ⬜ | `EnterPlanModeTool` + `ExitPlanModeV2Tool` |
| 28 | `tools/tool_search_tool.py` | ⬜ | `tools/ToolSearchTool/` |
| 29 | `tools/send_message_tool.py` | ⬜ | `tools/SendMessageTool/` |
| 30 | `tools/config_tool.py` | ⬜ | `tools/ConfigTool/` |
| 31 | `tools/brief_tool.py` | ⬜ | `tools/BriefTool/` |
| 32 | `tools/sleep_tool.py` | ⬜ | `tools/SleepTool/` |
| 33 | `tools/worktree_tool.py` | ⬜ | `EnterWorktreeTool` + `ExitWorktreeTool` |
| 34 | `tools/mcp_tool.py` | ⬜ | `tools/MCPTool/` |
| 35 | `tools/list_mcp_resources_tool.py` | ⬜ | `ListMcpResourcesTool` |
| 36 | `tools/read_mcp_resource_tool.py` | ⬜ | `ReadMcpResourceTool` |
| 37 | `tools/__init__.py` 注册 | ⬜ | `tools.ts` |
| 38 | `services/api/client.py` | ⬜ | `services/api/claude.ts` |
| 39 | `services/api/retry.py` | ⬜ | `services/api/withRetry.ts` |
| 40 | `services/api/usage.py` | ⬜ | `services/api/usage.ts` |
| 41 | `engine/context.py` | ⬜ | `context.ts` |
| 42 | `utils/claudemd.py` | ⬜ | `utils/claudemd.ts` |
| 43 | `engine/query.py` | ⬜ | `query.ts` |
| 44 | `engine/query_engine.py` | ⬜ | `QueryEngine.ts` |
| 45 | `services/tools/orchestration.py` | ⬜ | `toolOrchestration.ts` |
| 46 | `services/tools/executor.py` | ⬜ | `StreamingToolExecutor.ts` |
| 47 | `state/store.py` | ⬜ | `state/store.ts` |
| 48 | `state/app_state.py` | ⬜ | `state/AppStateStore.ts` |
| 49 | `ui/repl.py` | ⬜ | `screens/REPL.tsx` |
| 50 | `ui/renderer.py` | ⬜ | `ink/renderer.ts` |
| 51 | `ui/prompt.py` | ⬜ | `PromptInput/` |
| 52 | `permissions/modes.py` | ⬜ | `PermissionMode.ts` |
| 53 | `permissions/checker.py` | ⬜ | `permissions.ts` |
| 54 | `permissions/classifier.py` | ⬜ | `bashClassifier.ts` |
| 55 | `permissions/path_validator.py` | ⬜ | `pathValidation.ts` |
| 56 | `commands/registry.py` | ⬜ | `commands.ts` |
| 57 | `services/compact/` | ⬜ | `services/compact/` |
| 58 | `services/mcp/` | ⬜ | `services/mcp/` |
| 59 | `memory/` | ⬜ | `memdir/` |
| 60 | `tasks/` | ⬜ | `tasks/` + `Task.ts` |
| 61 | `main.py` | ⬜ | `main.tsx` + `cli.tsx` |
| 62 | `utils/git.py` | ⬜ | `utils/git/` |
| 63 | `utils/cost_tracker.py` | ⬜ | `cost-tracker.ts` |
| 64 | `utils/hooks.py` | ⬜ | `utils/hooks/` |

---

## 参考源文件索引

翻译时直接查阅原版源码：

| Python 目标 | TypeScript 原版 | 关键内容 |
|-------------|----------------|---------|
| `constants/prompts.py` | `constants/prompts.ts` | 7 个系统提示词段函数（逐字翻译） |
| `constants/cyber_risk.py` | `constants/cyberRiskInstruction.ts` | CYBER_RISK_INSTRUCTION |
| `tools/bash_tool.py` | `tools/BashTool/prompt.ts:getSimplePrompt()` | description 完整文本 |
| `tools/grep_tool.py` | `tools/GrepTool/prompt.ts:getDescription()` | description 完整文本 |
| `tools/web_fetch_tool.py` | `tools/WebFetchTool/prompt.ts:DESCRIPTION` | description 完整文本 |
| `tools/web_search_tool.py` | `tools/WebSearchTool/prompt.ts:getWebSearchPrompt()` | description 完整文本 |
| `tools/agent_tool.py` | `tools/AgentTool/prompt.ts:getPrompt()` | description 完整文本 |
| `tools/todo_write_tool.py` | `tools/TodoWriteTool/prompt.ts:DESCRIPTION+PROMPT` | description + prompt 文本 |
| `engine/context.py` | `context.ts` | getGitStatus/getUserContext/getSystemContext |
| `utils/claudemd.py` | `utils/claudemd.ts:getClaudeMds()` | 记忆文件格式化 |
| `engine/query.py` | `query.ts` | query() 循环结构 |
| `engine/query_engine.py` | `QueryEngine.ts` | QueryEngine 类 |
| `tools/__init__.py` | `tools.ts:getAllBaseTools()` | 注册顺序 + 工具集合常量 |
| `services/tools/orchestration.py` | `services/tools/toolOrchestration.ts` | runTools() |
| 详细源码提取 | `SOURCE_EXTRACTION.md` | 所有工具的原版 name/description/schema 原文 |
