"""Auto-compact integration — monitors context usage and triggers compaction."""

from __future__ import annotations

import logging
from typing import Any, Callable

from claude_code.services.compact.compact import (
    compact_messages,
    should_auto_compact,
)
from claude_code.data_types import Message

logger = logging.getLogger(__name__)


class AutoCompactTracker:
    """Track context usage and trigger auto-compact when threshold is reached.

    Translation of autoCompactTracking from query loop state.
    """

    def __init__(
        self,
        max_context_tokens: int = 128000,
        summarize_fn: Any = None,
    ) -> None:
        self._max_context_tokens = max_context_tokens
        self._summarize_fn = summarize_fn
        self._has_attempted_reactive_compact = False

    @property
    def has_attempted_reactive_compact(self) -> bool:
        return self._has_attempted_reactive_compact

    def check_should_compact(self, messages: list[Message]) -> bool:
        """Check if messages should be compacted."""
        return should_auto_compact(
            messages,
            max_context_tokens=self._max_context_tokens,
        )

    async def maybe_compact(
        self,
        messages: list[Message],
        system_prompt: str,
    ) -> list[Message] | None:
        """Compact messages if threshold is reached.

        Returns the compacted messages, or None if no compaction needed.
        """
        if not self.check_should_compact(messages):
            return None

        logger.info(
            "Auto-compact triggered: %d messages, estimated near context limit",
            len(messages),
        )

        compacted = await compact_messages(
            messages=messages,
            system_prompt=system_prompt,
            summarize_fn=self._summarize_fn,
            max_context_tokens=self._max_context_tokens,
        )

        if len(compacted) < len(messages):
            logger.info(
                "Compacted %d messages -> %d messages",
                len(messages),
                len(compacted),
            )
            return compacted

        return None

    async def reactive_compact(
        self,
        messages: list[Message],
        system_prompt: str,
    ) -> list[Message] | None:
        """Attempt reactive compaction (after max_output_tokens error).

        Only attempts once per session.
        """
        if self._has_attempted_reactive_compact:
            return None

        self._has_attempted_reactive_compact = True
        logger.info("Attempting reactive compact after max_output_tokens error")

        return await compact_messages(
            messages=messages,
            system_prompt=system_prompt,
            summarize_fn=self._summarize_fn,
            max_context_tokens=self._max_context_tokens,
        )
