"""Microcompact — strict translation of microcompact.ts.

Implements micro-compaction using cached microcompact summaries.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from AgentX.data_types import Message, UserMessage
from AgentX.pydantic_models import FrozenModel, MutableModel

logger = logging.getLogger(__name__)


class MicrocompactConfig(FrozenModel):
    """Configuration for microcompact."""

    max_cached_summaries: int = 5  # Max cached microcompact summaries
    max_messages_per_summary: int = 20  # Max messages per summary
    trigger_threshold: int = 50  # Trigger when message count exceeds this


class MicrocompactSummary(UserMessage):
    """A microcompact summary message.

    Translation of microcompact summary in TS.
    """

    def __init__(self, summary: str, message_count: int, start_idx: int, end_idx: int):
        content = f"[Microcompact: {message_count} messages {start_idx}-{end_idx}]\n{summary}"
        super().__init__(content=content)
        self.summary = summary
        self.message_count = message_count
        self.start_idx = start_idx
        self.end_idx = end_idx


class MicrocompactTracker(MutableModel):
    """Tracks and manages microcompact summaries.

    Translation of MicrocompactTracker in TS.
    """

    def __init__(
        self,
        config: Optional[MicrocompactConfig] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._config = config or MicrocompactConfig()
        self._cached_summaries: list[MicrocompactSummary] = []
        self._microcompact_count: int = 0

    @property
    def microcompact_count(self) -> int:
        return self._microcompact_count

    @property
    def cached_count(self) -> int:
        return len(self._cached_summaries)

    def should_microcompact(self, messages: list[Message]) -> bool:
        """Check if microcompact should be triggered."""
        return len(messages) > self._config.trigger_threshold

    def create_summary(
        self,
        messages: list[Message],
        start_idx: int,
        end_idx: int,
        summarize_fn: Optional[Any] = None,
    ) -> MicrocompactSummary:
        """Create a microcompact summary for a range of messages."""
        if end_idx > len(messages):
            end_idx = len(messages)

        subset = messages[start_idx:end_idx]

        if summarize_fn is not None:
            summary_text = summarize_fn(subset)
        else:
            summary_text = self._basic_summary(subset)

        summary = MicrocompactSummary(
            summary=summary_text,
            message_count=len(subset),
            start_idx=start_idx,
            end_idx=end_idx,
        )

        # Cache it
        self._cached_summaries.append(summary)
        if len(self._cached_summaries) > self._config.max_cached_summaries:
            self._cached_summaries.pop(0)  # Remove oldest

        self._microcompact_count += 1
        logger.info(
            "Microcompact created: %d messages [%d-%d] (total: %d)",
            len(subset),
            start_idx,
            end_idx,
            self._microcompact_count,
        )

        return summary

    def _basic_summary(self, messages: list[Message]) -> str:
        """Create basic summary without LLM."""
        parts: list[str] = []
        for msg in messages[:10]:  # Limit for basic summary
            if hasattr(msg, "content") and msg.content:
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                parts.append(content[:100])
        return "\n".join(parts)

    def get_cached_summary(self, index: int) -> Optional[MicrocompactSummary]:
        """Get cached summary by index."""
        if 0 <= index < len(self._cached_summaries):
            return self._cached_summaries[index]
        return None

    def clear_cache(self) -> None:
        """Clear cached summaries (e.g., after full compact)."""
        self._cached_summaries.clear()
        logger.debug("Microcompact cache cleared")

    def get_stats(self) -> dict[str, Any]:
        """Get microcompact statistics."""
        return {
            "microcompact_count": self._microcompact_count,
            "cached_summaries": len(self._cached_summaries),
            "max_cached": self._config.max_cached_summaries,
            "trigger_threshold": self._config.trigger_threshold,
        }


def try_microcompact(
    messages: list[Message],
    tracker: Optional[MicrocompactTracker] = None,
    summarize_fn: Optional[Any] = None,
) -> tuple[list[Message], bool]:
    """Conditionally apply microcompact if needed.

    Translation of tryMicrocompact() in TS.
    Returns (new_messages, was_microcompacted).
    """
    if tracker is None:
        tracker = MicrocompactTracker()

    if not tracker.should_microcompact(messages):
        return messages, False

    # Find a range to summarize (oldest N messages)
    summarize_count = min(tracker._config.max_messages_per_summary, len(messages) // 2)
    start_idx = 0
    end_idx = summarize_count

    summary = tracker.create_summary(
        messages, start_idx, end_idx, summarize_fn
    )

    # Replace summarized messages with summary
    new_messages = [summary] + messages[end_idx:]

    logger.info(
        "Microcompact applied: %d messages -> %d messages",
        len(messages),
        len(new_messages),
    )

    return new_messages, True
