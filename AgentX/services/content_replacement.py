"""Content replacement — translation of recordContentReplacement in query.ts.

Stores tool result content replacements for session-level deduplication.
"""

from __future__ import annotations

import logging
from typing import Optional

from AgentX.pydantic_models import FrozenModel

logger = logging.getLogger(__name__)


class ReplacementEntry(FrozenModel):
    """A single content replacement entry."""

    original: str
    replacement: str
    tool_name: str
    timestamp: float


class ContentReplacementStore:
    """Session-level storage for content replacements.

    Translation of session storage for content replacement in TS.
    """

    def __init__(self) -> None:
        self._replacements: list[ReplacementEntry] = []

    def record(
        self,
        original: str,
        replacement: str,
        tool_name: str,
        timestamp: float,
    ) -> None:
        """Record a content replacement (translation of recordContentReplacement)."""
        entry = ReplacementEntry(
            original=original,
            replacement=replacement,
            tool_name=tool_name,
            timestamp=timestamp,
        )
        self._replacements.append(entry)
        logger.debug("Recorded content replacement for tool %s", tool_name)

    def get_replacements_for_tool(self, tool_name: str) -> list[ReplacementEntry]:
        """Get all replacements for a specific tool."""
        return [r for r in self._replacements if r.tool_name == tool_name]

    def get_latest_replacement(self, tool_name: str) -> Optional[ReplacementEntry]:
        """Get the most recent replacement for a tool."""
        replacements = self.get_replacements_for_tool(tool_name)
        return replacements[-1] if replacements else None

    def apply_replacements(self, content: str, tool_name: str) -> str:
        """Apply all recorded replacements to content."""
        result = content
        for entry in self.get_replacements_for_tool(tool_name):
            if entry.original in result:
                result = result.replace(entry.original, entry.replacement)
        return result

    def clear(self) -> None:
        """Clear all replacements (e.g., after compact)."""
        self._replacements.clear()
        logger.debug("Cleared all content replacements")

    @property
    def count(self) -> int:
        """Number of recorded replacements."""
        return len(self._replacements)
