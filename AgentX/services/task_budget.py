"""Task budget tracking — translation of taskBudget.ts.

Tracks task-level budget (remaining budget for current task).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from AgentX.pydantic_models import FrozenModel, MutableModel

logger = logging.getLogger(__name__)


class TaskBudgetExceededError(Exception):
    """Raised when task budget is exceeded."""

    def __init__(self, used: float, budget: float):
        self.used = used
        self.budget = budget
        super().__init__(f"Task budget exceeded: ${used:.2f} > ${budget:.2f}")


class TaskBudgetConfig(FrozenModel):
    """Configuration for task budget tracking."""

    max_budget_usd: float = 5.0  # Max USD for a single task
    warn_at_percentage: float = 0.8  # Warn when 80% of budget used
    cost_per_1k_input_tokens: float = 0.001  # Cost per 1K input tokens
    cost_per_1k_output_tokens: float = 0.002  # Cost per 1K output tokens


class TaskBudget(MutableModel):
    """Tracks budget for a specific task.

    Translation of TaskBudget class from taskBudget.ts.
    """

    def __init__(
        self,
        config: Optional[TaskBudgetConfig] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._config = config or TaskBudgetConfig()
        self._spent: float = 0.0
        self._input_tokens: int = 0
        self._output_tokens: int = 0
        self._warning_emitted: bool = False

    @property
    def spent(self) -> float:
        return self._spent

    @property
    def remaining(self) -> float:
        return max(0.0, self._config.max_budget_usd - self._spent)

    @property
    def percentage_used(self) -> float:
        if self._config.max_budget_usd == 0:
            return 0.0
        return self._spent / self._config.max_budget_usd

    @property
    def input_tokens(self) -> int:
        return self._input_tokens

    @property
    def output_tokens(self) -> int:
        return self._output_tokens

    def record_usage(
        self,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Record token usage and return cost incurred."""
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens

        # Calculate cost
        input_cost = (input_tokens / 1000) * self._config.cost_per_1k_input_tokens
        output_cost = (output_tokens / 1000) * self._config.cost_per_1k_output_tokens
        cost = input_cost + output_cost

        self._spent += cost

        # Check for warning threshold
        if (
            not self._warning_emitted
            and self.percentage_used >= self._config.warn_at_percentage
        ):
            logger.warning(
                "Task budget warning: %.1f%% used ($%.2f/$%.2f)",
                self.percentage_used * 100,
                self._spent,
                self._config.max_budget_usd,
            )
            self._warning_emitted = True

        return cost

    def check_budget(self) -> None:
        """Check if budget is exceeded. Raises TaskBudgetExceededError if so."""
        if self._spent >= self._config.max_budget_usd:
            raise TaskBudgetExceededError(self._spent, self._config.max_budget_usd)

    def can_continue(self) -> bool:
        """Check if task can continue within budget."""
        return self.remaining > 0

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of budget usage."""
        return {
            "spent_usd": self._spent,
            "remaining_usd": self.remaining,
            "percentage_used": self.percentage_used,
            "max_budget_usd": self._config.max_budget_usd,
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "can_continue": self.can_continue(),
        }

    def reset(self) -> None:
        """Reset budget tracking (e.g., for new task)."""
        self._spent = 0.0
        self._input_tokens = 0
        self._output_tokens = 0
        self._warning_emitted = False
        logger.debug("Task budget reset")


class TaskBudgetTracker:
    """High-level task budget tracker with session integration.

    Translation of task budget tracking in query.ts.
    """

    def __init__(
        self,
        max_budget_usd: Optional[float] = None,
        config: Optional[TaskBudgetConfig] = None,
    ) -> None:
        self._config = config or TaskBudgetConfig()
        if max_budget_usd is not None:
            self._config.max_budget_usd = max_budget_usd
        self._current_budget: Optional[TaskBudget] = None

    @property
    def current_budget(self) -> Optional[TaskBudget]:
        return self._current_budget

    @property
    def is_active(self) -> bool:
        return self._current_budget is not None

    def start_task(self, task_name: str = "default") -> None:
        """Start tracking a new task."""
        self._current_budget = TaskBudget(config=self._config)
        logger.info("Started task budget tracking for: %s", task_name)

    def end_task(self) -> Optional[dict[str, Any]]:
        """End current task and return summary."""
        if self._current_budget is None:
            return None

        summary = self._current_budget.get_summary()
        logger.info(
            "Ended task. Spent: $%.2f/%.2f",
            summary["spent_usd"],
            summary["max_budget_usd"],
        )
        self._current_budget = None
        return summary

    def record_usage(
        self,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Record usage for current task."""
        if self._current_budget is None:
            return 0.0

        cost = self._current_budget.record_usage(input_tokens, output_tokens)

        try:
            self._current_budget.check_budget()
        except TaskBudgetExceededError as exc:
            logger.error("Task budget exceeded: %s", exc)
            raise

        return cost

    def get_status(self) -> dict[str, Any]:
        """Get current budget status."""
        if self._current_budget is None:
            return {"active": False}

        status = self._current_budget.get_summary()
        status["active"] = True
        return status
