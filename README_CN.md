<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="MIT License">
  <img src="https://img.shields.io/badge/OpenAI_Compatible-yes-brightgreen?logo=openai" alt="OpenAI Compatible">
  <img src="https://img.shields.io/badge/AsyncIO-native-purple" alt="AsyncIO">
</p>

<h1 align="center">AgentX</h1>

<p align="center">
  <strong>可自定义、高度抽象的生产级数字员工基础设施</strong>
  <br/>
  基于 AgentX 架构的严格 Python 翻译，接入任意 OpenAI 兼容 LLM 后端。
</p>

<p align="center">
  <a href="README.md">English</a> · <strong>简体中文</strong> · <a href="README_JA.md">日本語</a>
</p>

<p align="center">
  <img src="docs/hello.gif" alt="AgentX — 交互式 REPL" width="720">
</p>


---

## 愿景

**打造一个可自定义、高度抽象的生产级数字员工。**

AgentX 不仅仅是一个 AI 编程 CLI —— 它的目标是成为每个人、每个团队都能按需定制的**数字员工基础设施**。

| 维度 | 目标 |
|------|------|
| **可自定义** | 通过 `AGENTX_*` 环境变量、CLAUDE.md 规则体系、MCP 协议、自定义 Agent 定义，让每位用户构建专属的智能助手。品牌、行为、工具栈均可替换。 |
| **高度抽象** | 递归自相似 Agent 架构：主线程与子 Agent 共用同一套 `query()` 循环。Provider 模式彻底解耦 LLM 后端——接入任何 OpenAI 兼容 API 即可运行。Permission 系统提供 7 种权限模式逐级控制。 |
| **生产级** | 全异步 asyncio 引擎、流式响应、Pydantic 严格校验、自动压缩上下文、会话持久化、多 Agent 协同、错误恢复链、Stop Hooks 安全退出 —— 所有组件经过 TypeScript 原版验证后严格翻译。 |
| **数字员工** | 不是"对话机器人"，而是能读、写、执行、验证、自我纠错的 Agent。Fork 后台子 Agent、Verification Agent 独立验证、Teammates 多 Agent 协作 —— 像一位真正的工程师一样完成工作。 |

<p align="center">
  <em>"Don't just chat with AI. Hire it."</em>
</p>


---

## 为什么做这个项目？

智能编程 CLI 的范式已被证明能够显著提升开发效率，但现有实现往往与特定 SDK 和运行时紧密耦合。**AgentX** 打破了这种耦合。

**AgentX** 是对这一智能编程 CLI 概念在 Python 生态中的**严格架构级移植**。每一条提示词、工具名称、参数 Schema 和行为标志都仔细保留，同时运行时重构于 Python 的异步生态和 OpenAI 兼容供应商模式之上 —— 这意味着你可以接入**任意** LLM 后端（OpenAI、DeepSeek、Ollama、vLLM、LiteLLM 等）。

### 核心目标

- **严格保真** — 系统提示词、工具描述、Schema、权限模式均为逐字翻译
- **供应商无关** — 使用 OpenAI SDK 作为统一接口，通过 `--provider` / 环境变量切换后端
- **纯 Python，原生异步** — 基于 `asyncio`、`rich`、`prompt_toolkit`、`pydantic` 构建
- **完整工具对等** — 30+ 内置工具忠实翻译，支持 MCP 协议

---

## 架构

架构直接镜像自原版 TypeScript 源码。详见 [docs/IMPLEMENTATION_STEPS.md](docs/IMPLEMENTATION_STEPS.md) 获取完整翻译指南。

```
┌─────────────────────────────────────────────────────────┐
│                      CLI / REPL                         │
│              main.py  ·  ui/repl.py                     │
├───────────────────────┬─────────────────────────────────┤
│    查询引擎            │         状态管理                 │
│  engine/query_engine  │        state/app_state          │
│  engine/query.py      │        state/store              │
├───────────────────────┼─────────────────────────────────┤
│    LLM 服务            │       工具系统 (30+)            │
│  services/api/client  │  tools/bash_tool                │
│  services/llm/        │  tools/file_read_tool           │
│  services/compact/    │  tools/file_edit_tool           │
│                       │  tools/agent_tool  ...          │
├───────────────────────┼─────────────────────────────────┤
│  权限系统              │     高级功能                     │
│  permissions/checker  │  commands/   (斜杠命令)          │
│  permissions/classify │  memory/     (CLAUDE.md + 记忆)  │
│  permissions/modes    │  tasks/      (多代理)            │
│                       │  services/mcp/ (MCP 协议)       │
└───────────────────────┴─────────────────────────────────┘
```

### 技术栈映射

| 维度 | TypeScript 原版 | Python 翻译版 |
|------|----------------|--------------|
| 运行时 | Bun | Python 3.11+ / asyncio |
| AI SDK | `@anthropic-ai/sdk` | `openai`（供应商模式） |
| UI | React + Ink | `rich` + `prompt_toolkit` |
| Schema | Zod | `pydantic` |
| HTTP | fetch (Bun) | `httpx`（异步） |
| 类型 | TypeScript 接口 | `dataclass(frozen=True)` + `typing` |
| MCP | `@modelcontextprotocol/sdk` | `mcp` Python SDK |
| CLI | Commander.js | `click` |

---

## 快速开始

### 前置要求

- Python 3.11+
- 任意 OpenAI 兼容供应商的 API 密钥

### 安装

```bash
# 克隆仓库
git clone https://github.com/NoemaZero/AgentX.git
cd agentx-py

# 创建虚拟环境
python -m venv env
source env/bin/activate  # Windows: env\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 或以可编辑模式安装
pip install -e .
```

### 配置

通过环境变量设置 API 凭证：

```bash
# OpenAI
export OPENAI_API_KEY="sk-..."

# 或 DeepSeek
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.deepseek.com"

# 或任意 OpenAI 兼容端点（Ollama、vLLM、LiteLLM...）
export OPENAI_API_KEY="your-key"
export OPENAI_BASE_URL="http://localhost:11434/v1"
```

### 使用

```bash
# 交互式 REPL
python -m AgentX

# 单次查询（非交互）
python -m AgentX "修复 main.py 中的 bug"

# 指定模型 / 供应商
python -m AgentX --model gpt-4o
python -m AgentX --provider deepseek --model deepseek-chat

# 权限模式
python -m AgentX --permission-mode auto
python -m AgentX --permission-mode plan

# 详细输出
python -m AgentX -v
```

或使用安装后的 CLI：

```bash
agentx "解释这个代码库"
agentx --model gpt-4o --max-turns 10
```

---

## 工具系统

所有 30+ 工具均为原版 AgentX 工具定义的严格翻译，保留了完全一致的名称、描述和参数 Schema：

| 工具 | 名称 | 说明 |
|------|------|------|
| **Bash** | `Bash` | 在沙箱中执行 Shell 命令 |
| **Read** | `Read` | 读取本地文件系统中的文件 |
| **Write** | `Write` | 向本地文件系统写入文件 |
| **Edit** | `Edit` | 查找并替换编辑文件 |
| **Glob** | `Glob` | 快速文件模式匹配 |
| **Grep** | `Grep` | 基于 ripgrep 的强大搜索 |
| **Agent** | `Agent` | 启动子代理处理复杂任务 |
| **WebFetch** | `WebFetch` | 抓取和提取网页内容 |
| **WebSearch** | `WebSearch` | 搜索网络 |
| **TodoWrite** | `TodoWrite` | 用清单追踪会话进度 |
| **NotebookEdit** | `NotebookEdit` | 编辑 Jupyter Notebook 单元格 |
| **Skill** | `Skill` | 执行已学习的技能 |
| **EnterPlanMode** | `EnterPlanMode` | 切换到计划模式 |
| **ToolSearch** | `ToolSearch` | 搜索延迟加载的工具 |
| **MCP 工具** | 多种 | Model Context Protocol 集成 |
| ... | ... | 共计 30+ 工具 |

---

## LLM 供应商支持

AgentX 采用 **OpenAI 兼容供应商模式**，可与几乎任何 LLM 后端配合使用：

| 供应商 | 配置 |
|--------|------|
| **OpenAI** | `--provider openai --model gpt-4o` |
| **DeepSeek** | `--provider deepseek --model deepseek-chat` |
| **Custom** | `--provider custom --base-url http://localhost:11434/v1` |
| **Ollama** | `--provider custom --base-url http://localhost:11434/v1 --model llama3` |
| **vLLM** | `--provider custom --base-url http://localhost:8000/v1` |
| **LiteLLM** | `--provider custom --base-url http://localhost:4000/v1` |

---

## 演示：五子棋

[`example/gomoku/`](example/gomoku/) 目录展示了一个完整的五子棋游戏 —— **完全由 AgentX 生成**，从棋盘渲染到 AI 对手逻辑，一气呵成。

<p align="center">
  <img src="docs/gomoku.jpg" alt="五子棋游戏演示 — AgentX 生成" width="640">
</p>

### 生成游戏的功能亮点：
- 🎮 **人人对战 & 人机对战** — 与真人或 AI 对手对弈
- 🤖 **4 档 AI 难度** — 简单、中等、困难、专家
- ✨ **粒子背景特效** — CSS 动画粒子效果
- ↩️ **悔棋支持** — 可以撤销操作
- 📱 **响应式设计** — 兼容桌面和移动端

### 立即体验：

```bash
# 在浏览器中打开游戏
open example/gomoku/index.html
```

这个演示展示了完整的智能体工作流：AgentX 读取需求、规划架构、编写 HTML/CSS/JS 文件并迭代完善 —— 全部通过工具系统完成。

---

## 权限系统

严格翻译自原版权限模式：

| 模式 | 行为 |
|------|------|
| `default` | 每次操作前请求权限 |
| `acceptEdits` | 自动批准文件编辑，其他操作需请求 |
| `auto` | 自动批准大多数操作 |
| `plan` | 计划模式 — 先设计再执行 |
| `bypassPermissions` | 跳过所有权限检查 |
| `dontAsk` | 从不询问，未预授权则拒绝 |
| `bubble` | 将权限请求冒泡到父代理 |

---

## 项目结构

```
AgentX/
├── main.py                 # CLI 入口 (→ main.tsx)
├── config.py               # 配置管理 (→ entrypoints/init.ts)
├── data_types.py           # 核心类型 (→ Tool.ts + types/)
│
├── constants/              # 提示词与常量（严格翻译）
│   ├── prompts.py          # 系统提示词 (→ constants/prompts.ts)
│   └── cyber_risk.py       # 安全指令 (→ cyberRiskInstruction.ts)
│
├── engine/                 # 核心引擎
│   ├── query_engine.py     # 查询引擎 (→ QueryEngine.ts)
│   ├── query.py            # 查询循环 (→ query.ts)
│   └── context.py          # 上下文构建 (→ context.ts)
│
├── services/               # 服务层
│   ├── api/                # LLM API 客户端 (→ services/api/)
│   ├── llm/                # 供应商模式 (OpenAI/DeepSeek/Custom)
│   ├── compact/            # 上下文压缩 (→ services/compact/)
│   ├── mcp/                # MCP 协议 (→ services/mcp/)
│   └── tools/              # 工具编排 (→ services/tools/)
│
├── tools/                  # 30+ 内置工具 (→ tools/)
│   ├── base.py             # 工具基类 (→ Tool.ts)
│   ├── bash_tool.py        # Shell 执行 (→ BashTool/)
│   ├── file_read_tool.py   # 文件读取 (→ FileReadTool/)
│   ├── file_edit_tool.py   # 文件编辑 (→ FileEditTool/)
│   ├── agent_tool.py       # 子代理 (→ AgentTool/)
│   └── ...                 # 其他工具
│
├── permissions/            # 权限系统 (→ utils/permissions/)
├── commands/               # 斜杠命令 (→ commands.ts)
├── tasks/                  # 多代理任务 (→ tasks/)
├── memory/                 # 记忆系统 (→ memdir/)
├── state/                  # 状态管理 (→ state/)
├── ui/                     # 终端 UI (→ screens/ + ink/)
└── utils/                  # 工具函数 (→ utils/)
```

---

## 翻译原则

### 严格保留（逐字对齐）

| 类别 | 保留内容 |
|------|---------|
| **系统提示词** | 每一个词、每一行 |
| **工具名称** | `'Bash'`、`'Read'`、`'Edit'` 等 |
| **工具描述** | 完整的 `getSimplePrompt()` / `getDescription()` 文本 |
| **参数 Schema** | 字段名、类型、`.describe()` 文本 |
| **工具行为标志** | `isReadOnly`、`isConcurrencySafe`、`shouldDefer` |
| **权限模式** | 枚举值与原版 TypeScript 一致 |
| **上下文格式** | Git Status、CLAUDE.md 输出格式 |

### Python 适配

| 类别 | 适配方式 |
|------|---------|
| **API 层** | Anthropic SDK → OpenAI SDK（供应商模式） |
| **UI 框架** | React + Ink → Rich + prompt_toolkit |
| **并发模型** | JS Promises → Python asyncio |
| **进程管理** | Bun.spawn → asyncio.create_subprocess_exec |
| **类型系统** | TS 接口 → dataclass(frozen=True) + typing |
| **Schema** | Zod → Pydantic |

---

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest

# 代码检查
ruff check AgentX/

# 类型检查
mypy AgentX/
```

---

## 文档

- [IMPLEMENTATION_STEPS.md](docs/IMPLEMENTATION_STEPS.md) — 完整的 8 阶段实现指南，包含严格翻译规则
- [SOURCE_EXTRACTION.md](docs/SOURCE_EXTRACTION.md) — 从原版 TypeScript 源码中提取的精确字符串，供翻译参考

---

## 贡献

欢迎贡献！贡献时请遵循翻译原则：

1. **提示词和描述**必须是 TypeScript 原版的逐字翻译
2. **工具名称和 Schema** 必须与原版完全匹配
3. **Python 适配**应使用地道写法（asyncio、dataclass、pydantic）
4. **测试必需** — 保持 80%+ 覆盖率

---

## 许可证

[MIT](LICENSE) © 2026 NoemaZero

---

<p align="center">
  <sub>为 Python 生态构建 — 开源、供应商无关。</sub>
</p>
