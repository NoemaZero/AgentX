"""Terminal renderer — translation of ink/renderer.ts using Rich."""

from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

# Shared console instance
console = Console()


def _sanitize(text: str) -> str:
    """Remove surrogate characters that cannot be encoded as UTF-8."""
    return text.encode("utf-8", errors="replace").decode("utf-8")


def render_assistant_text(text: str) -> None:
    """Render assistant markdown output."""
    try:
        md = Markdown(text)
        console.print(md)
    except Exception:
        console.print(text)


def render_tool_use(tool_name: str, tool_id: str = "") -> None:
    """Render a tool use indicator."""
    console.print(
        Text(f"  ⚡ {tool_name}", style="bold cyan"),
        highlight=False,
    )


def render_tool_result(tool_name: str, content: str, is_error: bool = False) -> None:
    """Render a tool result."""
    content = _sanitize(content)
    style = "red" if is_error else "dim"
    # Truncate long results for display
    display = content[:2000] + "..." if len(content) > 2000 else content
    console.print(Text(f"  ← {tool_name}: ", style="bold dim"), end="")
    console.print(Text(display, style=style))


def render_error(message: str) -> None:
    """Render an error message."""
    message = _sanitize(message)
    console.print(Panel(message, title="Error", border_style="red"))


def render_info(message: str) -> None:
    """Render an info message."""
    console.print(Text(message, style="dim"))


def render_cost(input_tokens: int, output_tokens: int, cost_usd: float = 0.0) -> None:
    """Render token usage with optional cost."""
    total = input_tokens + output_tokens
    cost_str = f" (${cost_usd:.4f})" if cost_usd > 0 else ""
    console.print(
        Text(f"  tokens: {total:,} (in: {input_tokens:,}, out: {output_tokens:,}){cost_str}", style="dim"),
    )
