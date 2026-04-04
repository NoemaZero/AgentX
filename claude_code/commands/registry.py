"""Slash command registry — translation of commands.ts."""

from __future__ import annotations

import os
import sys
from typing import Any

from claude_code.pydantic_models import FrozenModel, model_to_dict


class Command(FrozenModel):
    """A slash command definition."""

    name: str
    description: str
    is_hidden: bool = False
    aliases: list[str] | None = None

    async def execute(self, args: str, **kwargs: Any) -> str:
        raise NotImplementedError


# ── Built-in Commands ──


class HelpCommand(Command):
    name: str = "help"
    description: str = "Show available commands"

    async def execute(self, args: str, **kwargs: Any) -> str:
        registry: CommandRegistry = kwargs.get("registry")  # type: ignore[assignment]
        if registry is None:
            return "No command registry available"
        lines = ["Available commands:"]
        for cmd in registry.commands:
            if not cmd.is_hidden:
                lines.append(f"  /{cmd.name} — {cmd.description}")
        return "\n".join(lines)


class ClearCommand(Command):
    name: str = "clear"
    description: str = "Clear conversation history"

    async def execute(self, args: str, **kwargs: Any) -> str:
        return "__CLEAR__"


class ExitCommand(Command):
    name: str = "exit"
    description: str = "Exit the application"

    async def execute(self, args: str, **kwargs: Any) -> str:
        return "__EXIT__"


class CostCommand(Command):
    name: str = "cost"
    description: str = "Show token usage and estimated cost"

    async def execute(self, args: str, **kwargs: Any) -> str:
        engine = kwargs.get("engine")
        if engine is None:
            return "No engine available"
        usage = engine.total_usage
        from claude_code.utils.cost_tracker import estimate_cost

        cost_usd = estimate_cost(
            model=kwargs.get("config", object).__dict__.get("model", ""),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        cost_str = f"\n  Estimated cost: ${cost_usd:.4f}" if cost_usd > 0 else ""
        return (
            f"Token usage:\n"
            f"  Input:  {usage.input_tokens:,}\n"
            f"  Output: {usage.output_tokens:,}\n"
            f"  Total:  {usage.input_tokens + usage.output_tokens:,}"
            f"{cost_str}"
        )


class CompactCommand(Command):
    name: str = "compact"
    description: str = "Compact conversation history"

    async def execute(self, args: str, **kwargs: Any) -> str:
        engine = kwargs.get("engine")
        if engine is None:
            return "No engine available"

        from claude_code.services.compact.compact import compact_messages

        messages = engine.messages
        if len(messages) < 6:
            return "Not enough messages to compact."

        compacted = await compact_messages(
            messages=messages,
            system_prompt="",
            summarize_fn=None,
        )
        # Replace engine's messages with compacted version
        engine._messages[:] = compacted
        return f"Compacted {len(messages)} messages → {len(compacted)} messages."


class ModelCommand(Command):
    name: str = "model"
    description: str = "Show or change the current model"

    async def execute(self, args: str, **kwargs: Any) -> str:
        config = kwargs.get("config")
        if config is None:
            return "No config available"
        new_model = args.strip()
        if new_model:
            # Allow runtime model switching — returns instruction for caller
            return f"__MODEL_SWITCH__{new_model}"
        return f"Current model: {config.model}"


class ContextCommand(Command):
    name: str = "context"
    description: str = "Show current context information"

    async def execute(self, args: str, **kwargs: Any) -> str:
        engine = kwargs.get("engine")
        config = kwargs.get("config")
        lines = ["Context:"]
        if config:
            lines.append(f"  Model: {config.model}")
            lines.append(f"  CWD: {config.cwd}")
            lines.append(f"  Max turns: {config.max_turns}")
        if engine:
            lines.append(f"  Messages: {len(engine.messages)}")
            lines.append(f"  Tools: {len(engine._tools)}")
        return "\n".join(lines)


class DiffCommand(Command):
    name: str = "diff"
    description: str = "Show git diff of changes"

    async def execute(self, args: str, **kwargs: Any) -> str:
        from claude_code.utils.git import run_git_command

        config = kwargs.get("config")
        cwd = config.cwd if config else os.getcwd()
        diff_args = args.strip().split() if args.strip() else ["--stat"]
        result = await run_git_command(cwd, "diff", *diff_args)
        return result if result else "(no changes)"


class StatusCommand(Command):
    name: str = "status"
    description: str = "Show session status"

    async def execute(self, args: str, **kwargs: Any) -> str:
        engine = kwargs.get("engine")
        config = kwargs.get("config")
        lines = ["Session status:"]
        if config:
            lines.append(f"  Model: {config.model}")
            lines.append(f"  Permission mode: {config.permission_mode}")
        if engine:
            usage = engine.total_usage
            lines.append(f"  Messages: {len(engine.messages)}")
            lines.append(f"  Total tokens: {usage.input_tokens + usage.output_tokens:,}")
        return "\n".join(lines)


class MemoryCommand(Command):
    name: str = "memory"
    description: str = "Show loaded CLAUDE.md files"

    async def execute(self, args: str, **kwargs: Any) -> str:
        config = kwargs.get("config")
        cwd = config.cwd if config else os.getcwd()

        from claude_code.utils.claudemd import get_claude_mds

        mds = await get_claude_mds(cwd)
        if not mds:
            return "No CLAUDE.md files found."
        lines = ["Loaded CLAUDE.md files:"]
        for md in mds:
            lines.append(f"  [{md['type']}] {md['path']} ({len(md['content'])} chars)")
        return "\n".join(lines)


class VersionCommand(Command):
    name: str = "version"
    description: str = "Show version information"

    async def execute(self, args: str, **kwargs: Any) -> str:
        from claude_code import __version__

        return f"claude-code-py {__version__}"


class PermissionsCommand(Command):
    name: str = "permissions"
    description: str = "Show or modify permission mode"

    async def execute(self, args: str, **kwargs: Any) -> str:
        config = kwargs.get("config")
        if config is None:
            return "No config available"
        new_mode = args.strip()
        if new_mode:
            from claude_code.data_types import PermissionMode, maybe_coerce_str_enum

            resolved_mode = maybe_coerce_str_enum(PermissionMode, new_mode)
            valid_modes = [mode.value for mode in PermissionMode]
            if resolved_mode is not None:
                return f"__PERMISSION_MODE__{resolved_mode.value}"
            return f"Invalid mode: {new_mode}. Valid: {', '.join(valid_modes)}"
        return f"Current permission mode: {config.permission_mode}"


class TasksCommand(Command):
    name: str = "tasks"
    description: str = "List background tasks"

    async def execute(self, args: str, **kwargs: Any) -> str:
        task_manager = kwargs.get("task_manager")
        if task_manager is None:
            return "Task manager not available"
        tasks = task_manager.list_tasks()
        if not tasks:
            return "No tasks running."
        lines = ["Tasks:"]
        for t in tasks:
            lines.append(f"  [{t.status}] {t.task_id}: {t.description}")
        return "\n".join(lines)


class PlanCommand(Command):
    name: str = "plan"
    description: str = "Toggle plan mode"

    async def execute(self, args: str, **kwargs: Any) -> str:
        config = kwargs.get("config")
        if config is None:
            return "No config available"
        from claude_code.data_types import PermissionMode

        current = config.permission_mode
        if current == PermissionMode.PLAN:
            return f"__PERMISSION_MODE__{PermissionMode.DEFAULT.value}"
        return f"__PERMISSION_MODE__{PermissionMode.PLAN.value}"


class ToolsCommand(Command):
    name: str = "tools"
    description: str = "List available tools"

    async def execute(self, args: str, **kwargs: Any) -> str:
        engine = kwargs.get("engine")
        if engine is None:
            return "No engine available"
        tools = engine._tools
        lines = [f"Available tools ({len(tools)}):"]
        for t in tools:
            ro = " [read-only]" if t.is_read_only else ""
            cs = " [concurrent]" if t.is_concurrency_safe else ""
            lines.append(f"  {t.name}{ro}{cs}")
        return "\n".join(lines)


class ConfigCommand(Command):
    name: str = "config"
    description: str = "Show configuration"

    async def execute(self, args: str, **kwargs: Any) -> str:
        config = kwargs.get("config")
        if config is None:
            return "No config available"

        items = model_to_dict(config)
        if "api_key" in items:
            items["api_key"] = "***" if items["api_key"] else "(not set)"
        return "\n".join(f"  {k}: {v}" for k, v in sorted(items.items()))


class VerboseCommand(Command):
    name: str = "verbose"
    description: str = "Toggle verbose mode"
    is_hidden: bool = True

    async def execute(self, args: str, **kwargs: Any) -> str:
        return "__VERBOSE_TOGGLE__"


class ResumeCommand(Command):
    name: str = "resume"
    description: str = "Resume the last session"

    async def execute(self, args: str, **kwargs: Any) -> str:
        config = kwargs.get("config")
        cwd = config.cwd if config else os.getcwd()

        from claude_code.utils.history import list_sessions, resume_session

        session_id = args.strip()
        if session_id:
            storage = resume_session(cwd, session_id)
            if storage is None:
                return f"Session not found: {session_id}"
            messages = storage.rebuild_messages()
            return f"__RESUME__{session_id}|{len(messages)} messages"

        # List available sessions
        sessions = list_sessions(cwd)
        if not sessions:
            return "No previous sessions found."

        lines = ["Recent sessions:"]
        for s in sessions[:10]:
            lines.append(f"  {s['session_id'][:8]} — {s['title']}")
        lines.append("\nUse /resume <session-id> to resume a session.")
        return "\n".join(lines)


class CommitCommand(Command):
    name: str = "commit"
    description: str = "Create a git commit with AI-generated message"

    async def execute(self, args: str, **kwargs: Any) -> str:
        return "__INJECT__Please review the current changes with `git diff --staged` (or `git diff` if nothing is staged), then create a well-crafted commit message following conventional commits format and commit the changes."


class InitCommand(Command):
    name: str = "init"
    description: str = "Initialize CLAUDE.md for this project"

    async def execute(self, args: str, **kwargs: Any) -> str:
        config = kwargs.get("config")
        cwd = config.cwd if config else os.getcwd()
        claude_md_path = os.path.join(cwd, "CLAUDE.md")
        if os.path.exists(claude_md_path):
            return f"CLAUDE.md already exists at {claude_md_path}"
        return "__INJECT__Please analyze this project's structure, tech stack, and conventions, then generate a comprehensive CLAUDE.md file that documents: project overview, tech stack, common commands (build, test, lint), code style guidelines, and project-specific patterns."


class DoctorCommand(Command):
    name: str = "doctor"
    description: str = "Check environment setup"

    async def execute(self, args: str, **kwargs: Any) -> str:
        from claude_code.utils.setup import check_environment

        warnings = check_environment()
        config = kwargs.get("config")

        lines = ["Environment check:"]
        # Python
        lines.append(f"  Python: {sys.version.split()[0]}")
        # API key
        if os.environ.get("ANTHROPIC_API_KEY"):
            lines.append("  ANTHROPIC_API_KEY: ✓ set")
        elif os.environ.get("OPENAI_API_KEY"):
            lines.append("  OPENAI_API_KEY: ✓ set")
        else:
            lines.append("  API key: ✗ not set")
        # Git
        import shutil

        lines.append(f"  git: {'✓' if shutil.which('git') else '✗'} found")
        # CWD
        if config:
            lines.append(f"  CWD: {config.cwd}")
            lines.append(f"  Model: {config.model}")

        if warnings:
            lines.append("\nWarnings:")
            for w in warnings:
                lines.append(f"  ⚠ {w}")
        else:
            lines.append("\n  All checks passed ✓")

        return "\n".join(lines)


class LoginCommand(Command):
    name: str = "login"
    description: str = "Configure API key"

    async def execute(self, args: str, **kwargs: Any) -> str:
        key = args.strip()
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key
            return "API key set for this session."
        return (
            "Usage: /login <api-key>\n"
            "Or set the ANTHROPIC_API_KEY environment variable.\n"
            "The key will only persist for this session."
        )


class LogoutCommand(Command):
    name: str = "logout"
    description: str = "Clear configured API key"

    async def execute(self, args: str, **kwargs: Any) -> str:
        if "ANTHROPIC_API_KEY" in os.environ:
            del os.environ["ANTHROPIC_API_KEY"]
            return "API key cleared for this session."
        return "No API key was set."


class BugCommand(Command):
    name: str = "bug"
    description: str = "Report a bug or issue"
    is_hidden: bool = True

    async def execute(self, args: str, **kwargs: Any) -> str:
        return (
            "To report a bug, please visit:\n"
            "  https://github.com/anthropics/claude-code/issues\n"
            "Include: Python version, OS, model, and steps to reproduce."
        )


class PRCommand(Command):
    name: str = "pr"
    description: str = "Create a pull request"

    async def execute(self, args: str, **kwargs: Any) -> str:
        return (
            "__INJECT__Please create a pull request for the current branch. "
            "First check `git log origin/main..HEAD` and `git diff origin/main...HEAD` "
            "to understand all changes, then draft a comprehensive PR description "
            "including: what changed, why, and a test plan. Use `gh pr create` to create it."
        )


class UndoCommand(Command):
    name: str = "undo"
    description: str = "Undo the last file change"

    async def execute(self, args: str, **kwargs: Any) -> str:
        return "__INJECT__Please undo the last file modification. Check `git diff` to see what changed, then `git checkout -- <file>` for the most recently modified file."


# ── Registry ──


class CommandRegistry:
    """Registry for all slash commands."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        # Register all built-in commands
        builtin: list[Command] = [
            HelpCommand(name="help", description="Show available commands"),
            ClearCommand(name="clear", description="Clear conversation history"),
            ExitCommand(name="exit", description="Exit the application", aliases=["quit", "q"]),
            CostCommand(name="cost", description="Show token usage and estimated cost"),
            CompactCommand(name="compact", description="Compact conversation history"),
            ModelCommand(name="model", description="Show or change the current model"),
            ContextCommand(name="context", description="Show current context information", aliases=["ctx"]),
            DiffCommand(name="diff", description="Show git diff of changes"),
            StatusCommand(name="status", description="Show session status"),
            MemoryCommand(name="memory", description="Show loaded CLAUDE.md files"),
            VersionCommand(name="version", description="Show version information"),
            PermissionsCommand(name="permissions", description="Show or modify permission mode"),
            TasksCommand(name="tasks", description="List background tasks"),
            PlanCommand(name="plan", description="Toggle plan mode"),
            ToolsCommand(name="tools", description="List available tools"),
            ConfigCommand(name="config", description="Show configuration"),
            VerboseCommand(name="verbose", description="Toggle verbose mode"),
            ResumeCommand(name="resume", description="Resume the last session"),
            CommitCommand(name="commit", description="Create a git commit with AI-generated message"),
            InitCommand(name="init", description="Initialize CLAUDE.md for this project"),
            DoctorCommand(name="doctor", description="Check environment setup"),
            LoginCommand(name="login", description="Configure API key"),
            LogoutCommand(name="logout", description="Clear configured API key"),
            BugCommand(name="bug", description="Report a bug or issue"),
            PRCommand(name="pr", description="Create a pull request"),
            UndoCommand(name="undo", description="Undo the last file change"),
        ]
        for cmd in builtin:
            self.register(cmd)

    def register(self, command: Command) -> None:
        self._commands[command.name] = command
        if command.aliases:
            for alias in command.aliases:
                self._commands[alias] = command

    @property
    def commands(self) -> list[Command]:
        seen: set[str] = set()
        result: list[Command] = []
        for cmd in self._commands.values():
            if cmd.name not in seen:
                seen.add(cmd.name)
                result.append(cmd)
        return result

    def get(self, name: str) -> Command | None:
        return self._commands.get(name)
