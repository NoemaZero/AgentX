"""Plan mode tools — strict translation of EnterPlanModeTool + ExitPlanModeV2Tool."""

from __future__ import annotations

from typing import Any

from AgentX.tools.base import BaseTool, ToolParameter
from AgentX.tools.tool_names import ENTER_PLAN_MODE_TOOL_NAME, EXIT_PLAN_MODE_TOOL_NAME
from AgentX.data_types import ToolResult


class EnterPlanModeTool(BaseTool):
    """Enter plan mode for complex tasks requiring exploration and design."""

    name = ENTER_PLAN_MODE_TOOL_NAME
    is_read_only = True
    is_concurrency_safe = True
    should_defer = True
    search_hint = "switch to plan mode to design an approach before coding"

    def get_description(self) -> str:
        return "Requests permission to enter plan mode for complex tasks requiring exploration and design"

    def get_parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        # Set plan mode in app state if available
        app_state_store = kwargs.get("app_state_store")
        if app_state_store is not None:
            app_state_store.update(lambda s: s.set_plan_mode(True))
        return ToolResult(data="Entered plan mode. Read-only tools are available. Use ExitPlanMode when ready to implement.")


class ExitPlanModeTool(BaseTool):
    """Exit plan mode and return to implementation."""

    name = EXIT_PLAN_MODE_TOOL_NAME

    def get_description(self) -> str:
        return "Exit plan mode and begin implementing the plan"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="allowedPrompts",
                type="array",
                description="Prompt-based permissions needed to implement the plan",
                required=False,
                items={"type": "object"},
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        app_state_store = kwargs.get("app_state_store")
        if app_state_store is not None:
            app_state_store.update(lambda s: s.set_plan_mode(False))
        return ToolResult(data="Exited plan mode. All tools are now available for implementation.")
