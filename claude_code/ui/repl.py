"""REPL — interactive terminal loop, translation of screens/REPL.tsx."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console

from claude_code import __version__
from claude_code.commands.registry import CommandRegistry
from claude_code.config import Config
from claude_code.engine.query_engine import QueryEngine
from claude_code.ui.renderer import render_cost, render_error
from claude_code.ui.stream_renderer import StreamRenderer

console = Console()



async def run_repl(config: Config) -> None:
    """Run the interactive REPL loop — translation of screens/REPL.tsx."""
    # Render welcome banner with version and configuration
    from rich.panel import Panel

    # Format workdir for display, truncate if too long
    workdir_display = config.cwd
    if len(config.cwd) > 50:
        # Show last 47 characters with ellipsis
        workdir_display = "..." + config.cwd[-47:]

    banner_lines = [
        "",
        f"Model: {config.model}",
        f"Workdir:   {workdir_display}",
        f"Permission Mode:  {config.permission_mode}",
        "",
        "Type /help for commands",
        "Press Ctrl+C to interrupt or Ctrl+D to exit",
    ]

    banner_text = "\n".join(banner_lines)

    # Calculate appropriate width based on content
    title_text = f"🤖 Claude Code v{__version__}"
    content_lines = banner_lines + [title_text]
    max_line_length = max((len(line) for line in content_lines if line), default=40)
    # Add padding for borders and padding: 2 chars for borders + 2 chars for horizontal padding
    panel_width = max_line_length + 4
    # Limit maximum width to 80 characters for better readability
    panel_width = min(panel_width, 80)

    panel = Panel(
        banner_text,
        title=f"[bold green]🤖 Claude Code v{__version__}[/bold green]",
        border_style="green",
        padding=(0, 1),
        title_align="left",
        width=panel_width
    )
    console.print(panel)
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
    """Process a single user query through the engine with real-time streaming."""
    renderer = StreamRenderer(console)

    async for event in engine.submit_message(user_input):
        await renderer.render_event(event)

    # Ensure any buffered content is rendered
    await renderer._flush_buffer()

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


