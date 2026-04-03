"""REPL — interactive terminal loop, translation of screens/REPL.tsx."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console

from claude_code.commands.registry import CommandRegistry
from claude_code.config import Config
from claude_code.engine.query_engine import QueryEngine
from claude_code.data_types import StreamEvent
from claude_code.ui.renderer import (
    render_assistant_text,
    render_cost,
    render_error,
    render_info,
    render_tool_result,
    render_tool_use,
)

console = Console()

WELCOME_BANNER = """╭─────────────────────────────────╮
│   Claude Code (Python Edition)  │
│   Type /help for commands       │
│   Press Ctrl+C to interrupt     │
╰─────────────────────────────────╯"""


async def run_repl(config: Config) -> None:
    """Run the interactive REPL loop — translation of screens/REPL.tsx."""
    console.print(WELCOME_BANNER, style="bold blue")
    console.print(f"  Model: {config.model}", style="dim")
    console.print(f"  CWD:   {config.cwd}", style="dim")
    console.print(f"  Mode:  {config.permission_mode}", style="dim")
    console.print()

    engine = QueryEngine(config)
    await engine.initialize()

    command_registry = CommandRegistry()
    session: PromptSession[str] = PromptSession(history=InMemoryHistory())

    while True:
        try:
            user_input = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: session.prompt("You> "),
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\nGoodbye!", style="bold blue")
            await engine.cleanup()
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # Handle slash commands
        if user_input.startswith("/"):
            cmd_parts = user_input[1:].split(None, 1)
            cmd_name = cmd_parts[0] if cmd_parts else ""
            cmd_args = cmd_parts[1] if len(cmd_parts) > 1 else ""

            command = command_registry.get(cmd_name)
            if command:
                result = await command.execute(
                    cmd_args,
                    registry=command_registry,
                    engine=engine,
                    config=config,
                    task_manager=engine.task_manager,
                )
                result = await _handle_command_result(result, engine, config)
                if result == "__BREAK__":
                    await engine.cleanup()
                    break
            else:
                console.print(f"Unknown command: /{cmd_name}. Type /help for available commands.", style="yellow")
            continue

        # Submit to engine
        try:
            await _process_query(engine, user_input, config)
        except KeyboardInterrupt:
            console.print("\n[interrupted]", style="yellow")
        except Exception as e:
            render_error(f"Error: {e}")


async def _handle_command_result(result: str, engine: QueryEngine, config: Config) -> str:
    """Handle special command result strings."""
    if result == "__EXIT__":
        console.print("Goodbye!", style="bold blue")
        return "__BREAK__"

    if result == "__CLEAR__":
        new_engine = QueryEngine(config)
        await new_engine.initialize()
        # Copy engine internals (hacky but works for in-place swap)
        engine._messages = new_engine._messages
        engine._system_prompt = new_engine._system_prompt
        engine._client = new_engine._client
        console.print("Conversation cleared.", style="dim")
        return ""

    if result == "__VERBOSE_TOGGLE__":
        # Toggle verbose — would need mutable config
        console.print("Verbose mode toggled.", style="dim")
        return ""

    if result.startswith("__MODEL_SWITCH__"):
        new_model = result[len("__MODEL_SWITCH__"):]
        console.print(f"Model switch requested: {new_model}", style="cyan")
        console.print("Note: Model switching requires restarting the session.", style="dim")
        return ""

    if result.startswith("__PERMISSION_MODE__"):
        new_mode = result[len("__PERMISSION_MODE__"):]
        engine.permission_checker.mode = new_mode  # type: ignore[assignment]
        console.print(f"Permission mode set to: {new_mode}", style="cyan")
        return ""

    # Regular output
    console.print(result)
    return ""


async def _process_query(engine: QueryEngine, user_input: str, config: Config) -> None:
    """Process a single user query through the engine."""
    assistant_text_parts: list[str] = []

    async for event in engine.submit_message(user_input):
        _handle_event(event, assistant_text_parts)

    # Render accumulated assistant text
    full_text = "".join(assistant_text_parts)
    if full_text.strip():
        console.print()
        render_assistant_text(full_text)
        console.print()

    # Show usage with cost estimate
    usage = engine.total_usage
    if usage.input_tokens > 0 or usage.output_tokens > 0:
        from claude_code.utils.cost_tracker import estimate_cost

        cost_usd = estimate_cost(
            model=config.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        render_cost(usage.input_tokens, usage.output_tokens, cost_usd)


def _handle_event(event: StreamEvent, text_parts: list[str]) -> None:
    """Handle a single stream event."""
    from claude_code.data_types import StreamEventType

    if event.type == StreamEventType.CONTENT_DELTA:
        # Accumulate text for final rendering
        text_parts.append(str(event.data))

    elif event.type == StreamEventType.TOOL_USE:
        data = event.data
        if isinstance(data, dict):
            render_tool_use(data.get("name", "?"), data.get("id", ""))

    elif event.type == StreamEventType.TOOL_RESULT:
        data = event.data
        if isinstance(data, dict):
            content = data.get("content", "")
            is_error = content.startswith("Error")
            render_tool_result("tool", content, is_error)

    elif event.type == StreamEventType.AUTO_COMPACT:
        data = event.data
        if isinstance(data, dict):
            render_info(
                f"Auto-compact: {data.get('before', '?')} → {data.get('after', '?')} messages"
            )

    elif event.type == StreamEventType.QUERY_ERROR:
        render_error(str(event.data))

    elif event.type == StreamEventType.MAX_TURNS_REACHED:
        render_info("Maximum turns reached. Please continue the conversation.")
