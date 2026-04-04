"""CLI main entry point — translation of main.tsx + entrypoints/cli.tsx.

Usage:
    python -m claude_code                    # Interactive REPL
    python -m claude_code "fix the bug"      # Single query (non-interactive)
    python -m claude_code --model gpt-4o     # Specify model
"""

from __future__ import annotations

import asyncio
import os
import sys

# Ensure the package is importable when running as `python main.py` from inside
# the claude_code directory (i.e. cwd == <repo>/claude_code).
_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import click

from claude_code import __version__
from claude_code.config import load_config
from claude_code.data_types import PermissionMode, StreamEventType


@click.command()
@click.argument("query", required=False, default=None)
@click.option("--model", "-m", default=None, help="Model to use (e.g. gpt-4o, deepseek-chat)")
@click.option("--api-key", envvar="OPENAI_API_KEY", default=None, help="API key")
@click.option("--base-url", envvar="OPENAI_BASE_URL", default=None, help="API base URL")
@click.option("--provider", "-p", default=None, help="LLM provider (openai, deepseek, custom). Auto-detected if omitted.")
@click.option("--ssl-verify/--no-ssl-verify", default=None, help="Enable/disable SSL certificate verification (default: from env or True).")
@click.option("--max-tokens", default=None, type=int, help="Max output tokens")
@click.option("--max-turns", default=None, type=int, help="Max agentic turns")
@click.option("--cwd", default=None, help="Working directory")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
@click.option(
    "--permission-mode",
    type=click.Choice([mode.value for mode in PermissionMode]),
    default=PermissionMode.DEFAULT.value,
    help="Permission mode",
)
@click.option("--system-prompt", default=None, help="Custom system prompt")
@click.option("--append-system-prompt", default=None, help="Append to system prompt")
@click.option("--version", is_flag=True, help="Show version")
def main(
    query: str | None,
    model: str | None,
    api_key: str | None,
    base_url: str | None,
    provider: str | None,
    ssl_verify: bool,
    max_tokens: int | None,
    max_turns: int | None,
    cwd: str | None,
    verbose: bool,
    permission_mode: str,
    system_prompt: str | None,
    append_system_prompt: str | None,
    version: bool,
) -> None:
    """Claude Code Python — AI coding assistant with OpenAI provider pattern."""
    if version:
        click.echo(f"claude-code-py {__version__}")
        return

    # Build config
    config = load_config(
        model=model,
        api_key=api_key,
        base_url=base_url,
        provider=provider,
        ssl_verify=ssl_verify,
        max_tokens=max_tokens,
        max_turns=max_turns,
        cwd=cwd,
        verbose=verbose,
        permission_mode=PermissionMode(permission_mode),
        system_prompt=system_prompt,
        append_system_prompt=append_system_prompt,
    )

    # Validate API key
    if not config.api_key:
        click.echo(
            "Error: No API key configured.\n"
            "Set OPENAI_API_KEY environment variable or pass --api-key.",
            err=True,
        )
        sys.exit(1)

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s")

    if query:
        # Non-interactive: single query mode
        asyncio.run(_run_single_query(config, query))
    else:
        # Interactive REPL
        asyncio.run(_run_repl(config))


async def _run_repl(config) -> None:
    """Launch the interactive REPL."""
    from claude_code.ui.repl import run_repl
    await run_repl(config)


async def _run_single_query(config, query: str) -> None:
    """Run a single non-interactive query."""
    from rich.console import Console

    from claude_code.engine.query_engine import QueryEngine
    from claude_code.ui.renderer import render_assistant_text, render_error

    console = Console()
    engine = QueryEngine(config)
    await engine.initialize()

    text_parts: list[str] = []

    try:
        async for event in engine.submit_message(query):
            if event.type == StreamEventType.CONTENT_DELTA:
                text_parts.append(str(event.data))
            elif event.type == StreamEventType.TOOL_USE:
                data = event.data
                if isinstance(data, dict):
                    console.print(f"  ⚡ {data.get('name', '?')}", style="bold cyan")
            elif event.type == StreamEventType.QUERY_ERROR:
                render_error(str(event.data))
                return

        full_text = "".join(text_parts)
        if full_text.strip():
            render_assistant_text(full_text)

    except KeyboardInterrupt:
        console.print("\n[interrupted]", style="yellow")
    except Exception as e:
        render_error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
