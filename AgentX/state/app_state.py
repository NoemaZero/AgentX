"""Application state — translation of state/AppStateStore.ts."""

from __future__ import annotations

from typing import Any, Callable

from pydantic import Field

from AgentX.data_types import PermissionMode
from AgentX.pydantic_models import FrozenModel


class AppState(FrozenModel):
    """Immutable application state.

    All mutations return a new instance (frozen=True).
    """

    cwd: str = ""
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    is_busy: bool = False
    plan_mode: bool = False
    todos: list[dict[str, Any]] = Field(default_factory=list)
    turn_count: int = 0
    total_cost_usd: float = 0.0
    active_task_count: int = 0

    def set_busy(self, busy: bool) -> AppState:
        return self.model_copy(update={"is_busy": busy})

    def set_plan_mode(self, plan_mode: bool) -> AppState:
        return self.model_copy(update={"plan_mode": plan_mode})

    def set_todos(self, todos: list[dict[str, Any]]) -> AppState:
        return self.model_copy(update={"todos": list(todos)})

    def increment_turn(self) -> AppState:
        return self.model_copy(update={"turn_count": self.turn_count + 1})

    def add_cost(self, cost: float) -> AppState:
        return self.model_copy(update={"total_cost_usd": self.total_cost_usd + cost})

    def set_active_tasks(self, count: int) -> AppState:
        return self.model_copy(update={"active_task_count": count})


class AppStateStore:
    """Mutable container holding immutable AppState — translation of store.ts."""

    def __init__(self, initial: AppState | None = None) -> None:
        self._state = initial or AppState()
        self._listeners: list[Callable[[AppState], None]] = []

    @property
    def state(self) -> AppState:
        return self._state

    def update(self, updater: Callable[[AppState], AppState]) -> None:
        """Update state via a function: (AppState) -> AppState."""
        new_state = updater(self._state)
        if new_state is not self._state:
            self._state = new_state
            for listener in self._listeners:
                listener(new_state)

    def set_todos(self, todos: list[dict[str, Any]]) -> None:
        self.update(lambda s: s.set_todos(todos))

    def subscribe(self, listener: Callable[[AppState], None]) -> Callable[[], None]:
        """Subscribe to state changes. Returns an unsubscribe function."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)
