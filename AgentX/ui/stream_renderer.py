"""Stream-oriented terminal renderer for real-time event display.

This module provides a StreamRenderer class that processes StreamEvent objects
and renders them incrementally, with proper handling of content deltas,
tool execution indicators, errors, and thinking status.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from AgentX.data_types import StreamEvent, StreamEventType
from AgentX.ui.renderer import render_tool_use


class StreamRenderer:
    """Real-time renderer for streaming LLM events.

    This renderer displays events as they arrive, providing immediate feedback
    to the user while maintaining a clean terminal presentation.

    Attributes:
        console: Rich Console instance for output.
        _buffer: Accumulated text deltas for the current assistant turn.
        _in_tool_execution: Whether a tool is currently being executed.
        _thinking_active: Whether the model is currently in thinking mode.
    """

    def __init__(self, console: Console | None = None) -> None:
        """Initialize the stream renderer.

        Args:
            console: Rich Console to use for output. If None, a new default
                console will be created.
        """
        self.console = console or Console()
        self._buffer: str = ""
        self._in_tool_execution: bool = False
        self._has_streamed_content: bool = False
        self._thinking_active: bool = False
        self._thinking_buffer: str = ""
        self._thinking_char_count: int = 0

    async def render_event(self, event: StreamEvent) -> None:
        """Render a single stream event.

        This method dispatches to appropriate handlers based on the event type
        and maintains internal state for proper rendering of multi-part content.

        Args:
            event: The StreamEvent to render.
        """
        if event.type == StreamEventType.CONTENT_DELTA:
            await self._render_content_delta(str(event.data))
        elif event.type == StreamEventType.THINKING_DELTA:
            await self._render_thinking_delta(event.data)
        elif event.type == StreamEventType.THINKING_END:
            await self._render_thinking_end()
        elif event.type == StreamEventType.TOOL_USE:
            await self._render_tool_use(event.data)
        elif event.type == StreamEventType.TOOL_RESULT:
            await self._render_tool_result(event.data)
        elif event.type == StreamEventType.QUERY_ERROR:
            await self._render_error(event.data)
        elif event.type == StreamEventType.MAX_TURNS_REACHED:
            await self._render_max_turns_reached()
        elif event.type == StreamEventType.AUTO_COMPACT:
            await self._render_auto_compact(event.data)
        elif event.type == StreamEventType.QUERY_COMPLETE:
            await self._render_query_complete()
        elif event.type == StreamEventType.STREAM_END:
            await self._flush_buffer()
        # Other event types are ignored for now

    async def _render_thinking_delta(self, data: Any) -> None:
        """Print thinking content with elegant styling as it arrives."""
        text = str(data)
        if not self._thinking_active:
            self._thinking_active = True
            self.console.print(Text("💭 Thinking:", style="bold cyan"))
        self._thinking_char_count += len(text)
        self._thinking_buffer += text
        # Print completed lines (ending with newline)
        while "\n" in self._thinking_buffer:
            idx = self._thinking_buffer.index("\n")
            line = self._thinking_buffer[:idx]
            self._thinking_buffer = self._thinking_buffer[idx + 1:]
            if line.strip():
                self.console.print(Text(f"  {line}", style="italic cyan"))
            else:
                self.console.print()

    async def _render_thinking_end(self) -> None:
        """End thinking section with completion status."""
        if self._thinking_active:
            # Flush any remaining text in buffer
            if self._thinking_buffer.strip():
                self.console.print(Text(f"  {self._thinking_buffer}", style="italic cyan"))
            self._thinking_buffer = ""
            self.console.print(
                Text(f"  ✓ Thinking complete ({self._thinking_char_count:,} chars)", style="dim green")
            )
            self.console.print(Text("  " + "─" * 40, style="dim"))
            self._thinking_active = False
            self._thinking_char_count = 0

    async def render_error(self, message: str) -> None:
        """Render an error message directly (not from a stream event).

        Args:
            message: Error message to display.
        """
        await self._render_error(message)

    async def _render_error(self, data: Any) -> None:
        """Render an error event (stops thinking if active)."""
        if self._thinking_active:
            await self._render_thinking_end()
        message = self._sanitize(str(data))
        from rich.panel import Panel
        self.console.print(Panel(message, title="Error", border_style="red"))

    async def _render_content_delta(self, text: str) -> None:
        """Render a content delta (partial assistant response).

        Text is accumulated in a buffer and printed incrementally to provide
        a streaming effect. The buffer is flushed when the turn completes.

        Args:
            text: The text chunk to display.
        """
        self._buffer += text
        # Print immediately for streaming effect
        self.console.print(text, end="", soft_wrap=True)
        self._has_streamed_content = True

    async def _render_tool_use(self, data: Any) -> None:
        """Render a tool use indicator with parameters.

        Args:
            data: Tool use data, expected to be a dict with 'name', optional 'id', and 'arguments'.
        """
        await self._flush_buffer()
        tool_name = "?"
        tool_id = ""
        arguments = None
        if isinstance(data, dict):
            tool_name = data.get("name", "?")
            tool_id = data.get("id", "")
            arguments = data.get("arguments")
        render_tool_use(tool_name, tool_id, arguments)

    async def _render_tool_result(self, data: Any) -> None:
        """Render a tool result.

        Args:
            data: Tool result data, expected to be a dict with 'content',
                'name' (tool name), 'duration_ms' (execution time), and possibly an error indication.
        """
        content = ""
        tool_name = "tool"
        is_error = False
        duration_ms = 0.0
        if isinstance(data, dict):
            content = data.get("content", "")
            tool_name = data.get("name", "tool")
            duration_ms = data.get("duration_ms", 0.0)
            is_error = content.startswith("Error")

        # Sanitize and truncate for display
        content = self._sanitize(content)
        display = content[:2000] + "..." if len(content) > 2000 else content

        # Format duration
        if duration_ms >= 1000:
            duration_str = f"{duration_ms / 1000:.2f}s"
        else:
            duration_str = f"{duration_ms:.0f}ms"

        style = "red" if is_error else "dim"
        self.console.print(Text(f"  ← {tool_name} ", style="bold dim"), end="")
        self.console.print(Text(f"({duration_str})", style="dim cyan"), end="")
        self.console.print(Text(": ", style="bold dim"), end="")
        self.console.print(Text(display, style=style))

    async def _render_max_turns_reached(self) -> None:
        """Render a maximum turns reached notification."""
        await self._flush_buffer()
        self.console.print(Text("Maximum turns reached. Please continue the conversation.", style="yellow"))

    async def _render_auto_compact(self, data: Any) -> None:
        """Render an auto-compact notification.

        Args:
            data: Auto-compact data, expected to be a dict with 'before' and 'after' counts.
        """
        before = "?"
        after = "?"
        if isinstance(data, dict):
            before = data.get("before", "?")
            after = data.get("after", "?")
        self.console.print(Text(f"Auto-compact: {before} → {after} messages", style="dim"))

    async def _render_query_complete(self) -> None:
        """Render query completion (flushes any pending buffer)."""
        await self._flush_buffer()

    async def _flush_buffer(self) -> None:
        """Flush the accumulated text buffer.

        If content has been streamed already, only a newline is printed.
        Otherwise, the full buffer is rendered as markdown.
        """
        if not self._buffer:
            return

        if self._has_streamed_content:
            # Content was already streamed, just print a newline if needed
            # and clear the buffer
            self.console.print()
            self._buffer = ""
            self._has_streamed_content = False
            return

        # Content was not streamed (e.g., in non-interactive mode)
        # Render the complete response as markdown
        try:
            md = Markdown(self._buffer)
            self.console.print(md)
        except Exception:
            # Fallback to plain text if markdown rendering fails
            self.console.print(self._buffer)

        self._buffer = ""
        self._has_streamed_content = False

    @staticmethod
    def _sanitize(text: str) -> str:
        """Remove surrogate characters that cannot be encoded as UTF-8.

        Args:
            text: Input text to sanitize.

        Returns:
            Sanitized text safe for terminal display.
        """
        return text.encode("utf-8", errors="replace").decode("utf-8")
