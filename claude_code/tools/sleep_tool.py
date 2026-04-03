"""Sleep tool — strict translation of SleepTool."""

from __future__ import annotations

import asyncio
from typing import Any

from claude_code.tools.base import BaseTool, ToolParameter
from claude_code.tools.tool_names import SLEEP_TOOL_NAME
from claude_code.data_types import ToolResult

MAX_SLEEP_MS = 300_000  # 5 minutes


class SleepTool(BaseTool):
    """Pause execution for a specified duration."""

    name = SLEEP_TOOL_NAME
    is_read_only = True
    is_concurrency_safe = True

    def get_description(self) -> str:
        return "Pause execution for a specified number of milliseconds."

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="duration_ms",
                type="integer",
                description=f"Duration to sleep in milliseconds (max {MAX_SLEEP_MS})",
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        duration_ms = tool_input.get("duration_ms", 0)
        if not isinstance(duration_ms, (int, float)):
            return ToolResult(data="Error: duration_ms must be a number")

        duration_ms = min(max(0, int(duration_ms)), MAX_SLEEP_MS)
        duration_s = duration_ms / 1000.0

        await asyncio.sleep(duration_s)
        return ToolResult(data=f"Slept for {duration_ms}ms")
