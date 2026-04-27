"""Snip Compaction — strict translation of snipCompact.ts.

Implements snip compaction for aggressive context reduction.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from AgentX.data_types import Message, UserMessage, AssistantMessage
from AgentX.pydantic_models import FrozenModel


logger = logging.getLogger(__name__)


class SnipCompactConfig(FrozenModel):
    """Configuration for snip compaction."""

    max_messages_after_snip: int = 30  # Max messages after snip
    preserve_recent: int = 5  # Always preserve N most recent messages
    snip_threshold: int = 80  # Trigger snip when message count exceeds this


class SnipCompactionTracker:
    """Tracks and manages snip compaction.

    Translation of snip compaction logic in TS.
    """

    def __init__(
        self,
        config: Optional[SnipCompactConfig] = None,
    ) -> None:
        self._config = config or SnipCompactConfig()
        self._snip_count: int = 0

    @property
    def snip_count(self) -> int:
        return self._snip_count

    def should_snip(self, messages: list[Message]) -> bool:
        """Check if snip compaction should be triggered."""
        return len(messages) > self._config.snip_threshold

    def snip(
        self,
        messages: list[Message],
        summarize_fn: Optional[Any] = None,
    ) -> list[Message]:
        """Aggressively reduce context by snipping old messages.

        Translation of snipCompact() in TS.
        Returns new message list with snipped summary.
        """
        if len(messages) <= self._config.snip_threshold:
            return messages

        # Preserve most recent messages
        preserve_count = min(self._config.preserve_recent, len(messages))
        recent = messages[-preserve_count:]
        old = messages[:-preserve_count]

        if not old:
            return messages

        # Create aggressive summary of old messages
        if summarize_fn is not None:
            summary_text = summarize_fn(old)
        else:
            summary_text = self._aggressive_summary(old)

        # Create snip summary message
        snip_msg = UserMessage(
            content=f"[Snip compaction: {len(old)} messages snipped]\n{summary_text}"
        )

        self._snip_count += 1
        logger.info(
            "Snip compaction applied: %d messages -> 1 summary (total snips: %d)",
            len(old),
            self._snip_count,
        )

        return [snip_msg] + recent

    def _aggressive_summary(self, messages: list[Message]) -> str:
        """Create aggressive summary without LLM."""
        # For snip compaction, we're very aggressive - just keep key points
        parts: list[str] = []
        for msg in messages:
            if isinstance(msg, UserMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                # Only keep first 50 chars for aggressive summary
                parts.append(f"U: {content[:50]}...")
            elif isinstance(msg, AssistantMessage):
                if msg.content:
                    parts.append(f"A: {msg.content[:50]}...")
                if msg.tool_calls:
                    parts.append(f"[Tools: {len(msg.tool_calls)}]")

        return "\n".join(parts[:10])  # Only first 10 items

    def get_stats(self) -> dict[str, Any]:
        """Get snip compaction statistics."""
        return {
            "snip_count": self._snip_count,
            "config": {
                "max_messages_after_snip": self._config.max_messages_after_snip,
                "preserve_recent": self._config.preserve_recent,
                "snip_threshold": self._config.snip_threshold,
            },
        }


def try_snip_compact(
    messages: list[Message],
    tracker: Optional[SnipCompactionTracker] = None,
    summarize_fn: Optional[Any] = None,
) -> tuple[list[Message], bool]:
    """Conditionally apply snip compaction if needed.

    Translation of trySnipCompact() in TS.
    Returns (new_messages, was_snipped).
    """
    if tracker is None:
        tracker = SnipCompactionTracker()

    if not tracker.should_snip(messages):
        return messages, False

    new_messages = tracker.snip(messages, summarize_fn)
    return new_messages, True
