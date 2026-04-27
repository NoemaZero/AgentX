"""Context Collapse — strict translation of contextCollapse/index.ts.

Implements context collapse for reducing conversation length while preserving key info.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from AgentX.data_types import Message, UserMessage, AssistantMessage, ToolResultMessage
from AgentX.pydantic_models import FrozenModel, MutableModel

logger = logging.getLogger(__name__)


class CollapseConfig(FrozenModel):
    """Configuration for context collapse."""

    max_messages: int = 50  # Max messages to keep after collapse
    preserve_recent: int = 10  # Always preserve N most recent messages
    collapse_threshold: int = 100  # Trigger collapse when message count exceeds this


class CollapsedMessage(UserMessage):
    """A collapsed summary message replacing multiple messages.

    Translation of collapsed boundary message in TS.
    """

    collapsed_count: int = 0
    summary: str = ""

    @classmethod
    def create(cls, summary: str, collapsed_count: int) -> "CollapsedMessage":
        """Factory method to create a CollapsedMessage."""
        return cls(
            content=f"[Context collapsed: {collapsed_count} messages]\n{summary}",
            collapsed_count=collapsed_count,
            summary=summary,
        )


class ContextCollapseTracker(MutableModel):
    """Tracks context and triggers collapse when threshold is reached.

    Translation of context collapse tracking in TS.
    """

    def __init__(
        self,
        config: Optional[CollapseConfig] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._config = config or CollapseConfig()
        self._collapse_count: int = 0

    @property
    def collapse_count(self) -> int:
        return self._collapse_count

    def should_collapse(self, messages: list[Message]) -> bool:
        """Check if context should be collapsed."""
        return len(messages) > self._config.collapse_threshold

    def collapse(
        self,
        messages: list[Message],
        summarize_fn: Optional[Any] = None,
    ) -> list[Message]:
        """Collapse context by summarizing old messages.

        Translation of collapseContext() in TS.
        Returns new message list with collapsed summary.
        """
        if len(messages) <= self._config.collapse_threshold:
            return messages

        # Keep most recent messages intact
        preserve_count = min(self._config.preserve_recent, len(messages))
        recent = messages[-preserve_count:]
        old = messages[:-preserve_count]

        if not old:
            return messages

        # Create summary of old messages
        if summarize_fn is not None:
            summary = summarize_fn(old)
        else:
            summary = self._basic_collapse_summary(old)

        # Create collapsed message
        collapsed_msg = CollapsedMessage(
            summary=summary,
            collapsed_count=len(old),
        )

        self._collapse_count += 1
        logger.info(
            "Context collapsed: %d messages -> 1 summary (total collapses: %d)",
            len(old),
            self._collapse_count,
        )

        return [collapsed_msg] + recent

    def _basic_collapse_summary(self, messages: list[Message]) -> str:
        """Create basic summary without LLM."""
        parts: list[str] = []
        for msg in messages[:20]:  # Limit to first 20 for basic summary
            if isinstance(msg, UserMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                parts.append(f"User: {content[:100]}")
            elif isinstance(msg, AssistantMessage):
                if msg.content:
                    parts.append(f"Assistant: {msg.content[:100]}")
                if msg.tool_calls:
                    parts.append(f"[Tool calls: {len(msg.tool_calls)}]")
            elif isinstance(msg, ToolResultMessage):
                parts.append(f"Tool result: {msg.content[:100]}")

        return "\n".join(parts)

    def get_stats(self) -> dict[str, Any]:
        """Get collapse statistics."""
        return {
            "collapse_count": self._collapse_count,
            "config": {
                "max_messages": self._config.max_messages,
                "preserve_recent": self._config.preserve_recent,
                "collapse_threshold": self._config.collapse_threshold,
            },
        }


def maybe_collapse_context(
    messages: list[Message],
    tracker: Optional[ContextCollapseTracker] = None,
    summarize_fn: Optional[Any] = None,
) -> tuple[list[Message], bool]:
    """Conditionally collapse context if needed.

    Translation of maybeCollapseContext() in TS.
    Returns (new_messages, was_collapsed).
    """
    if tracker is None:
        tracker = ContextCollapseTracker()

    if not tracker.should_collapse(messages):
        return messages, False

    new_messages = tracker.collapse(messages, summarize_fn)
    return new_messages, True
