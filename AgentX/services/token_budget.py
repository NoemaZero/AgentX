"""Token budget tracking — strict translation of tokenBudget.ts.

Tracks token usage across the session and enforces budget limits.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from AgentX.data_types import Usage
from AgentX.pydantic_models import FrozenModel, MutableModel

logger = logging.getLogger(__name__)


class TokenBudgetExceededError(Exception):
    """Raised when token budget is exceeded."""

    def __init__(self, used: int, budget: int):
        self.used = used
        self.budget = budget
        super().__init__(f"Token budget exceeded: {used} > {budget}")


class TokenBudgetConfig(FrozenModel):
    """Configuration for token budget tracking."""

    max_input_tokens: int = 100_000
    max_output_tokens: int = 50_000
    max_total_tokens: int = 150_000
    warn_at_percentage: float = 0.8  # Warn when 80% of budget used


class TokenBudget(MutableModel):
    """Tracks token usage against budget limits.

    Translation of TokenBudget class from tokenBudget.ts.
    """

    def __init__(
        self,
        config: Optional[TokenBudgetConfig] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._config = config or TokenBudgetConfig()
        self._input_tokens_used: int = 0
        self._output_tokens_used: int = 0
        self._warning_emitted: bool = False

    @property
    def input_tokens_used(self) -> int:
        return self._input_tokens_used

    @property
    def output_tokens_used(self) -> int:
        return self._output_tokens_used

    @property
    def total_tokens_used(self) -> int:
        return self._input_tokens_used + self._output_tokens_used

    @property
    def input_remaining(self) -> int:
        return max(0, self._config.max_input_tokens - self._input_tokens_used)

    @property
    def output_remaining(self) -> int:
        return max(0, self._config.max_output_tokens - self._output_tokens_used)

    @property
    def total_remaining(self) -> int:
        return max(0, self._config.max_total_tokens - self.total_tokens_used)

    @property
    def input_percentage(self) -> float:
        if self._config.max_input_tokens == 0:
            return 0.0
        return self._input_tokens_used / self._config.max_input_tokens

    @property
    def output_percentage(self) -> float:
        if self._config.max_output_tokens == 0:
            return 0.0
        return self._output_tokens_used / self._config.max_output_tokens

    @property
    def total_percentage(self) -> float:
        if self._config.max_total_tokens == 0:
            return 0.0
        return self.total_tokens_used / self._config.max_total_tokens

    def record_usage(self, usage: Usage) -> None:
        """Record token usage from an API call."""
        self._input_tokens_used += usage.input_tokens
        self._output_tokens_used += usage.output_tokens

        # Check for warning threshold
        if (
            not self._warning_emitted
            and self.total_percentage >= self._config.warn_at_percentage
        ):
            logger.warning(
                "Token budget warning: %.1f%% used (%d/%d tokens)",
                self.total_percentage * 100,
                self.total_tokens_used,
                self._config.max_total_tokens,
            )
            self._warning_emitted = True

    def check_budget(self) -> None:
        """Check if budget is exceeded. Raises TokenBudgetExceededError if so."""
        if self._input_tokens_used > self._config.max_input_tokens:
            raise TokenBudgetExceededError(
                self._input_tokens_used, self._config.max_input_tokens
            )

        if self._output_tokens_used > self._config.max_output_tokens:
            raise TokenBudgetExceededError(
                self._output_tokens_used, self._config.max_output_tokens
            )

        if self.total_tokens_used > self._config.max_total_tokens:
            raise TokenBudgetExceededError(
                self.total_tokens_used, self._config.max_total_tokens
            )

    def can_continue(self) -> bool:
        """Check if session can continue within budget."""
        try:
            self.check_budget()
            return True
        except TokenBudgetExceededError:
            return False

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of budget usage."""
        return {
            "input_tokens_used": self._input_tokens_used,
            "output_tokens_used": self._output_tokens_used,
            "total_tokens_used": self.total_tokens_used,
            "input_remaining": self.input_remaining,
            "output_remaining": self.output_remaining,
            "total_remaining": self.total_remaining,
            "input_percentage": self.input_percentage,
            "output_percentage": self.output_percentage,
            "total_percentage": self.total_percentage,
            "can_continue": self.can_continue(),
        }

    def reset(self) -> None:
        """Reset budget tracking (e.g., after compact)."""
        self._input_tokens_used = 0
        self._output_tokens_used = 0
        self._warning_emitted = False
        logger.debug("Token budget reset")


class TokenBudgetTracker:
    """High-level token budget tracker with session integration.

    Translation of token budget tracking in query.ts.
    """

    def __init__(
        self,
        max_budget_usd: Optional[float] = None,
        config: Optional[TokenBudgetConfig] = None,
    ) -> None:
        self._budget = TokenBudget(config=config)
        self._max_budget_usd = max_budget_usd
        self._total_cost: float = 0.0

    @property
    def budget(self) -> TokenBudget:
        return self._budget

    def track_usage(self, usage: Usage, cost_per_token: float = 0.0) -> None:
        """Track token usage and associated cost."""
        self._budget.record_usage(usage)

        # Estimate cost (simplified)
        if cost_per_token > 0:
            total_tokens = usage.input_tokens + usage.output_tokens
            self._total_cost += total_tokens * cost_per_token

    def check_continuation(self) -> tuple[bool, Optional[str]]:
        """Check if session can continue.

        Returns (can_continue, reason_if_not).
        """
        if not self._budget.can_continue():
            return False, "Token budget exceeded"

        if self._max_budget_usd and self._total_cost > self._max_budget_usd:
            return False, f"Budget ${self._max_budget_usd} exceeded (${self._total_cost:.2f})"

        return True, None

    def get_status(self) -> dict[str, Any]:
        """Get current budget status."""
        status = self._budget.get_summary()
        status["total_cost_usd"] = self._total_cost
        status["max_budget_usd"] = self._max_budget_usd
        return status
