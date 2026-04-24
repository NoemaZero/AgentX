"""Terminal renderer — translation of ink/renderer.ts using Rich."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

# Shared console instance
console = Console()

# Maximum length for a single parameter value before truncation
_MAX_PARAM_VALUE_LEN = 60
# Maximum number of parameters to display
_MAX_PARAMS = 10


def _sanitize(text: str) -> str:
    """Remove surrogate characters that cannot be encoded as UTF-8."""
    return text.encode("utf-8", errors="replace").decode("utf-8")


def _truncate(value: str, max_len: int = _MAX_PARAM_VALUE_LEN) -> str:
    """Truncate a string value with ellipsis if it exceeds max_len."""
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


def _parse_arguments(arguments: str | None) -> dict[str, Any]:
    """Parse tool arguments JSON string into a dict. Returns empty dict on failure."""
    if not arguments:
        return {}
    try:
        parsed = json.loads(arguments)
        if isinstance(parsed, dict):
            return parsed
        return {"_": parsed}
    except (json.JSONDecodeError, TypeError):
        return {}


def _format_value(value: Any) -> str:
    """Format a parameter value for display, truncating if necessary."""
    if isinstance(value, str):
        return _truncate(value)
    formatted = json.dumps(value, ensure_ascii=False, indent=None)
    return _truncate(formatted)


def render_assistant_text(text: str) -> None:
    """Render assistant markdown output."""
    try:
        md = Markdown(text)
        console.print(md)
    except Exception:
        console.print(text)


def render_tool_use(
    tool_name: str,
    tool_id: str = "",
    arguments: str | None = None,
) -> None:
    """Render a tool use with formatted parameters.

    Displays the tool name prominently with a lightning bolt icon,
    followed by input parameters in a clean indented layout. Long
    parameter values are automatically truncated with '...'.
    """
    params = _parse_arguments(arguments)

    # Header
    header = Text(f"  ⚡ {tool_name}", style="bold cyan")
    console.print(header, highlight=False)

    if not params:
        return

    # Parameters — indented, aligned, with truncation
    shown = 0
    for key, value in params.items():
        if shown >= _MAX_PARAMS:
            console.print(
                Text(f"     ... +{len(params) - _MAX_PARAMS} more", style="dim italic"),
                highlight=False,
            )
            break
        formatted = _format_value(value)
        console.print(
            Text(f"     {key}: ", style="dim") + Text(formatted, style="white"),
            highlight=False,
        )
        shown += 1


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
