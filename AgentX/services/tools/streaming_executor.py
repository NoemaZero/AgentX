"""Streaming Tool Executor — strict translation of StreamingToolExecutor from TypeScript.

Enables parallel tool execution while the model is still streaming.
Translation of services/tools/streamingToolExecutor.ts.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Optional

from AgentX.tools.base import BaseTool
from AgentX.data_types import ToolResultMessage, StreamEvent, StreamEventType
from AgentX.utils.text import truncate_content

logger = logging.getLogger(__name__)


class StreamingToolExecutor:
    """Executes tools in streaming mode (parallel where safe).

    Translation of StreamingToolExecutor class from TypeScript.
    Allows tools to execute while model is still streaming response.
    """

    def __init__(
        self,
        tools_by_name: dict[str, BaseTool],
        cwd: str = "",
        permission_checker: Any = None,
        hook_manager: Any = None,
        ask_callback: Any = None,
        engine: Any = None,
    ) -> None:
        self._tools_by_name = tools_by_name
        self._cwd = cwd
        self._permission_checker = permission_checker
        self._hook_manager = hook_manager
        self._ask_callback = ask_callback
        self._engine = engine
        self._tasks: dict[str, asyncio.Task[ToolResultMessage]] = {}
        self._results: dict[str, ToolResultMessage] = {}
        self._discarded = False

    async def execute_streaming(
        self,
        tool_calls: list[dict[str, Any]],
    ) -> AsyncIterator[StreamEvent]:
        """Execute tool calls in streaming mode.

        Yields tool execution events as they complete.
        Translation of execute() method in TypeScript.
        """
        if self._discarded:
            logger.warning("StreamingToolExecutor was discarded, skipping execution")
            return

        # Partition tools into concurrent vs sequential
        concurrent_calls = []
        sequential_calls = []

        for tc in tool_calls:
            func = tc.get("function", {})
            tool_name = func.get("name", "")
            tool = self._tools_by_name.get(tool_name)

            if tool and tool.is_read_only():
                concurrent_calls.append(tc)
            else:
                sequential_calls.append(tc)

        # Execute concurrent tools in parallel
        if concurrent_calls:
            concurrent_tasks = []
            for tc in concurrent_calls:
                task = asyncio.create_task(self._execute_single(tc))
                self._tasks[tc.get("id", "")] = task
                concurrent_tasks.append(task)

            # Yield results as they complete
            for coro in asyncio.as_completed(concurrent_tasks):
                result = await coro
                self._results[result.tool_call_id] = result
                yield StreamEvent(
                    type=StreamEventType.TOOL_RESULT,
                    data={
                        "tool_call_id": result.tool_call_id,
                        "name": result.name,
                        "content": truncate_content(result.content),
                    },
                )

        # Execute sequential tools one by one
        for tc in sequential_calls:
            result = await self._execute_single(tc)
            self._results[result.tool_call_id] = result
            yield StreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={
                    "tool_call_id": result.tool_call_id,
                    "name": result.name,
                    "content": result.content[:500] + "..." if len(result.content) > 500 else result.content,
                },
            )

    async def _execute_single(self, tc: dict[str, Any]) -> ToolResultMessage:
        """Execute a single tool call."""
        from AgentX.services.tools.orchestration import _parse_tool_call

        tc_id, tool_name, arguments_str = _parse_tool_call(tc)

        tool = self._tools_by_name.get(tool_name)
        if not tool:
            from AgentX.tools.base import ToolResultMessage

            return ToolResultMessage(
                tool_call_id=tc_id,
                name=tool_name,
                content=f"Error: Tool {tool_name} not found",
            )

        try:
            import json

            tool_input = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
        except (json.JSONDecodeError, TypeError):
            tool_input = {"input": arguments_str}

        # Permission check
        if self._permission_checker is not None:
            try:
                decision = await self._permission_checker.check(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool=tool,
                )
                if decision == "deny":
                    return ToolResultMessage(
                        tool_call_id=tc_id,
                        name=tool_name,
                        content="Tool execution denied by permission checker",
                    )
            except Exception as exc:
                logger.warning("Permission check error: %s", exc)

        # Execute tool
        try:
            import time

            start = time.time()
            result = await tool.execute(tool_input=tool_input, cwd=self._cwd)
            duration_ms = (time.time() - start) * 1000

            return ToolResultMessage(
                tool_call_id=tc_id,
                name=tool_name,
                content=result.data if hasattr(result, "data") else str(result),
                duration_ms=duration_ms,
            )
        except Exception as exc:
            return ToolResultMessage(
                tool_call_id=tc_id,
                name=tool_name,
                content=f"Error executing tool: {exc}",
            )

    def discard(self) -> None:
        """Discard executor and cancel pending tasks.

        Translation of discard() method in TypeScript.
        Called during fallback/error recovery to clean up.
        """
        self._discarded = True

        # Cancel all pending tasks
        for task_id, task in self._tasks.items():
            if not task.done():
                task.cancel()
                logger.debug("Cancelled task for tool call %s", task_id)

        self._tasks.clear()
        self._results.clear()
        logger.info("StreamingToolExecutor discarded")

    def get_result(self, tool_call_id: str) -> Optional[ToolResultMessage]:
        """Get result for a specific tool call."""
        return self._results.get(tool_call_id)

    def get_all_results(self) -> list[ToolResultMessage]:
        """Get all completed results."""
        return list(self._results.values())
