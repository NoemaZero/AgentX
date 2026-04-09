<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="MIT License">
  <img src="https://img.shields.io/badge/OpenAI_Compatible-yes-brightgreen?logo=openai" alt="OpenAI Compatible">
  <img src="https://img.shields.io/badge/AsyncIO-native-purple" alt="AsyncIO">
</p>

<h1 align="center">Claude Code Python</h1>

<p align="center">
  <strong>A faithful Python port of Anthropic's <a href="https://github.com/anthropics/claude-code">Claude Code</a> — the official AI coding CLI.</strong>
  <br/>
  Strict 1:1 architecture translation from TypeScript to Python, powered by any OpenAI-compatible provider.
</p>

<p align="center">
  <strong>English</strong> · <a href="README_CN.md">简体中文</a> · <a href="README_JA.md">日本語</a>
</p>

<p align="center">
  <img src="docs/hello.gif" alt="Claude Code Python — Interactive REPL" width="720">
</p>


---

## Why This Project?

[Claude Code](https://github.com/anthropics/claude-code) is Anthropic's official agentic coding CLI — a terminal-native AI assistant that can read, write, and execute code with human-in-the-loop permission control. However, the original is written in TypeScript and tightly coupled to the Anthropic SDK.

**Claude Code Python** is a **strict, line-by-line translation** of the original TypeScript codebase into idiomatic Python. Every prompt, tool name, parameter schema, and behavioral flag is preserved exactly as-is from the source, while the runtime is rebuilt on Python's async ecosystem and the OpenAI-compatible provider pattern — so you can plug in **any** LLM backend (OpenAI, DeepSeek, Ollama, vLLM, LiteLLM, etc.).

### Key Goals

- **Strict fidelity** — System prompts, tool descriptions, schemas, and permission modes are character-for-character translations of the original.
- **Provider agnostic** — Uses the OpenAI SDK as a universal interface. Swap backends via `--provider` / env vars.
- **Pure Python, async-native** — Built on `asyncio`, `rich`, `prompt_toolkit`, and `pydantic`.
- **Full tool parity** — All 30+ built-in tools faithfully translated, plus MCP protocol support.

---

## Architecture

The architecture is a direct mirror of the original TypeScript source. See [docs/IMPLEMENTATION_STEPS.md](docs/IMPLEMENTATION_STEPS.md) for the full translation guide.

```
┌─────────────────────────────────────────────────────────┐
│                      CLI / REPL                         │
│              main.py  ·  ui/repl.py                     │
├───────────────────────┬─────────────────────────────────┤
│    QueryEngine        │         State Management        │
│  engine/query_engine  │        state/app_state          │
│  engine/query.py      │        state/store              │
├───────────────────────┼─────────────────────────────────┤
│    LLM Service        │       Tool System (30+)         │
│  services/api/client  │  tools/bash_tool                │
│  services/llm/        │  tools/file_read_tool           │
│  services/compact/    │  tools/file_edit_tool           │
│                       │  tools/agent_tool  ...          │
├───────────────────────┼─────────────────────────────────┤
│  Permission System    │     Advanced Features           │
│  permissions/checker  │  commands/   (slash commands)   │
│  permissions/classify │  memory/     (CLAUDE.md + mem)  │
│  permissions/modes    │  tasks/      (multi-agent)      │
│                       │  services/mcp/ (MCP protocol)   │
└───────────────────────┴─────────────────────────────────┘
```

### Tech Stack Mapping

| Dimension | TypeScript Original | Python Port |
|-----------|-------------------|-------------|
| Runtime | Bun | Python 3.11+ / asyncio |
| AI SDK | `@anthropic-ai/sdk` | `openai` (provider pattern) |
| UI | React + Ink | `rich` + `prompt_toolkit` |
| Schema | Zod | `pydantic` |
| HTTP | fetch (Bun) | `httpx` (async) |
| Types | TypeScript interfaces | `dataclass(frozen=True)` + `typing` |
| MCP | `@modelcontextprotocol/sdk` | `mcp` Python SDK |
| CLI | Commander.js | `click` |

---

## Quick Start

### Prerequisites

- Python 3.11+
- An API key for any OpenAI-compatible provider

### Installation

```bash
# Clone the repo
git clone https://github.com/NoemaZero/claude-code-py.git
cd claude-code-py

# Create virtual environment
python -m venv env
source env/bin/activate  # or `env\Scripts\activate` on Windows

# Install dependencies
pip install -r requirements.txt

# Or install as editable package
pip install -e .
```

### Configuration

Set your API credentials via environment variables:

```bash
# OpenAI
export OPENAI_API_KEY="sk-..."

# Or DeepSeek
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.deepseek.com"

# Or any OpenAI-compatible endpoint (Ollama, vLLM, LiteLLM...)
export OPENAI_API_KEY="your-key"
export OPENAI_BASE_URL="http://localhost:11434/v1"
```

### Usage

```bash
# Interactive REPL
python -m claude_code

# Single query (non-interactive)
python -m claude_code "fix the bug in main.py"

# Specify model / provider
python -m claude_code --model gpt-4o
python -m claude_code --provider deepseek --model deepseek-chat

# Permission modes
python -m claude_code --permission-mode auto
python -m claude_code --permission-mode plan

# Verbose output
python -m claude_code -v
```

Or use the installed CLI:

```bash
claude-code "explain this codebase"
claude-code --model gpt-4o --max-turns 10
```

---

## Tool System

All 30+ tools are strict translations of the original Claude Code tool definitions, preserving exact names, descriptions, and parameter schemas:

| Tool | Name | Description |
|------|------|-------------|
| **Bash** | `Bash` | Execute shell commands with sandbox support |
| **Read** | `Read` | Read files from the local filesystem |
| **Write** | `Write` | Write files to the local filesystem |
| **Edit** | `Edit` | Edit files with find-and-replace |
| **Glob** | `Glob` | Fast file pattern matching |
| **Grep** | `Grep` | Powerful search built on ripgrep |
| **Agent** | `Agent` | Launch sub-agents for complex tasks |
| **WebFetch** | `WebFetch` | Fetch and extract web content |
| **WebSearch** | `WebSearch` | Search the web |
| **TodoWrite** | `TodoWrite` | Track session progress with checklists |
| **NotebookEdit** | `NotebookEdit` | Edit Jupyter notebook cells |
| **Skill** | `Skill` | Execute learned skills |
| **EnterPlanMode** | `EnterPlanMode` | Switch to plan mode |
| **ToolSearch** | `ToolSearch` | Search for deferred tools |
| **MCP Tools** | Various | Model Context Protocol integration |
| ... | ... | 30+ tools total |

---

## LLM Provider Support

Claude Code Python uses the **OpenAI-compatible provider pattern**, making it work with virtually any LLM backend:

| Provider | Config |
|----------|--------|
| **OpenAI** | `--provider openai --model gpt-4o` |
| **DeepSeek** | `--provider deepseek --model deepseek-chat` |
| **Custom** | `--provider custom --base-url http://localhost:11434/v1` |
| **Ollama** | `--provider custom --base-url http://localhost:11434/v1 --model llama3` |
| **vLLM** | `--provider custom --base-url http://localhost:8000/v1` |
| **LiteLLM** | `--provider custom --base-url http://localhost:4000/v1` |

---

## Demo: Gomoku (Five-in-a-Row)

The [`example/gomoku/`](example/gomoku/) directory showcases a complete Gomoku game **generated entirely by Claude Code Python** — from the game board rendering to the AI opponent logic.

<p align="center">
  <img src="docs/gomoku.jpg" alt="Gomoku game demo — generated by Claude Code Python" width="640">
</p>

### Features of the generated game:
- 🎮 **PvP & PvE modes** — Play against another human or an AI opponent
- 🤖 **4 AI difficulty levels** — Easy, Medium, Hard, Expert
- ✨ **Particle background effects** — Animated CSS particles
- ↩️ **Undo support** — Take back moves
- 📱 **Responsive design** — Works on desktop and mobile

### Try it yourself:

```bash
# Open the game in your browser
open example/gomoku/index.html
```

This demo illustrates the full agentic workflow: Claude Code Python reads the requirements, plans the architecture, writes HTML/CSS/JS files, and iterates until the game is complete — all through the tool system.

---

## Permission System

Strict translation of the original permission modes:

| Mode | Behavior |
|------|----------|
| `default` | Ask for permission on each action |
| `acceptEdits` | Auto-approve file edits, ask for others |
| `auto` | Auto-approve most actions |
| `plan` | Plan mode — design before executing |
| `bypassPermissions` | Skip all permission checks |
| `dontAsk` | Never ask, deny if not pre-approved |
| `bubble` | Bubble permission requests to parent agent |

---

## Project Structure

```
claude_code/
├── main.py                 # CLI entry point (→ main.tsx)
├── config.py               # Configuration (→ entrypoints/init.ts)
├── data_types.py           # Core types (→ Tool.ts + types/)
│
├── constants/              # Prompts & constants (strict translation)
│   ├── prompts.py          # System prompts (→ constants/prompts.ts)
│   └── cyber_risk.py       # Security instruction (→ cyberRiskInstruction.ts)
│
├── engine/                 # Core engine
│   ├── query_engine.py     # QueryEngine (→ QueryEngine.ts)
│   ├── query.py            # Query loop (→ query.ts)
│   └── context.py          # Context builder (→ context.ts)
│
├── services/               # Service layer
│   ├── api/                # LLM API client (→ services/api/)
│   ├── llm/                # Provider pattern (OpenAI/DeepSeek/Custom)
│   ├── compact/            # Context compaction (→ services/compact/)
│   ├── mcp/                # MCP protocol (→ services/mcp/)
│   └── tools/              # Tool orchestration (→ services/tools/)
│
├── tools/                  # 30+ built-in tools (→ tools/)
│   ├── base.py             # Tool base class (→ Tool.ts)
│   ├── bash_tool.py        # Shell execution (→ BashTool/)
│   ├── file_read_tool.py   # File reading (→ FileReadTool/)
│   ├── file_edit_tool.py   # File editing (→ FileEditTool/)
│   ├── agent_tool.py       # Sub-agents (→ AgentTool/)
│   └── ...                 # All other tools
│
├── permissions/            # Permission system (→ utils/permissions/)
├── commands/               # Slash commands (→ commands.ts)
├── tasks/                  # Multi-agent tasks (→ tasks/)
├── memory/                 # Memory system (→ memdir/)
├── state/                  # State management (→ state/)
├── ui/                     # Terminal UI (→ screens/ + ink/)
└── utils/                  # Utilities (→ utils/)
```

---

## Translation Principles

### Strictly Preserved (Character-for-Character)

| Category | What's Preserved |
|----------|-----------------|
| **System Prompts** | Every word, every line |
| **Tool Names** | `'Bash'`, `'Read'`, `'Edit'`, etc. |
| **Tool Descriptions** | Full `getSimplePrompt()` / `getDescription()` text |
| **Parameter Schemas** | Field names, types, `.describe()` strings |
| **Tool Behavior Flags** | `isReadOnly`, `isConcurrencySafe`, `shouldDefer` |
| **Permission Modes** | Enum values match original TypeScript |
| **Context Formats** | Git status, CLAUDE.md output format |

### Adapted for Python

| Category | Adaptation |
|----------|-----------|
| **API Layer** | Anthropic SDK → OpenAI SDK (provider pattern) |
| **UI Framework** | React + Ink → Rich + prompt_toolkit |
| **Concurrency** | JS Promises → Python asyncio |
| **Process Mgmt** | Bun.spawn → asyncio.create_subprocess_exec |
| **Type System** | TS interfaces → dataclass(frozen=True) + typing |
| **Schema** | Zod → Pydantic |

---

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check claude_code/

# Type check
mypy claude_code/
```

---

## Documentation

- [IMPLEMENTATION_STEPS.md](docs/IMPLEMENTATION_STEPS.md) — Full 8-phase implementation guide with strict translation rules
- [SOURCE_EXTRACTION.md](docs/SOURCE_EXTRACTION.md) — Exact strings extracted from the original TypeScript source for translation reference

---

## Contributing

Contributions are welcome! When contributing, please follow the translation principles:

1. **Prompts and descriptions** must be character-for-character translations from the TypeScript original
2. **Tool names and schemas** must match the original exactly
3. **Python adaptations** should be idiomatic (asyncio, dataclass, pydantic)
4. **Tests required** — maintain 80%+ coverage

---

## License

[MIT](LICENSE) © 2026 NoemaZero

---

<p align="center">
  <sub>Strictly translated from <a href="https://github.com/anthropics/claude-code">Anthropic's Claude Code</a> TypeScript source — reimagined for the Python ecosystem.</sub>
</p>
