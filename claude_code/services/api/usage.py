"""Usage tracking — aggregate token usage across queries."""

from __future__ import annotations

from typing import Any

from claude_code.data_types import Usage
from claude_code.pydantic_models import FrozenModel


class AggregateUsage(FrozenModel):
    """Immutable aggregate usage across all queries in a session."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_creation_tokens: int = 0
    total_cache_read_tokens: int = 0
    request_count: int = 0

    def add(self, usage: Usage) -> "AggregateUsage":
        """Return a new AggregateUsage with the given usage added."""
        return AggregateUsage(
            total_input_tokens=self.total_input_tokens + usage.input_tokens,
            total_output_tokens=self.total_output_tokens + usage.output_tokens,
            total_cache_creation_tokens=self.total_cache_creation_tokens + usage.cache_creation_input_tokens,
            total_cache_read_tokens=self.total_cache_read_tokens + usage.cache_read_input_tokens,
            request_count=self.request_count + 1,
        )

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cache_creation_tokens": self.total_cache_creation_tokens,
            "total_cache_read_tokens": self.total_cache_read_tokens,
            "total_tokens": self.total_tokens,
            "request_count": self.request_count,
        }


class UsageTracker:
    """Mutable usage tracker wrapping immutable AggregateUsage."""

    def __init__(self) -> None:
        self._usage = AggregateUsage()

    @property
    def usage(self) -> AggregateUsage:
        return self._usage

    def record(self, usage: Usage) -> None:
        """Record a new usage entry."""
        self._usage = self._usage.add(usage)

    def reset(self) -> None:
        """Reset all usage counters."""
        self._usage = AggregateUsage()
