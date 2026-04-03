"""Tool orchestration — strict translation of services/tools/toolOrchestration.ts.

Handles executing tool calls from assistant messages, including concurrent
execution of read-only/concurrency-safe tools, with permission checking.

Key features (matching TS):
- Batch partitioning: concurrent vs sequential
- Async generator streaming: yield progress events during execution
- Context modifier chain (tool results can modify context)
- Permission pre-check with classifier
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator

from claude_code.tools.base import BaseTool
from claude_code.data_types import ToolExecutionStatus, ToolResult, ToolResultMessage
from claude_code.pydantic_models import FrozenModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Streaming event types for tool execution progress
# ---------------------------------------------------------------------------


class ToolProgressEvent(FrozenModel):
    """Progress event emitted during tool execution."""

    tool_call_id: str
    tool_name: str
    status: ToolExecutionStatus
    duration_ms: float = 0.0
    result_preview: str = ""


# ---------------------------------------------------------------------------
# Batch partitioning (matching TS partitionToolCalls)
# ---------------------------------------------------------------------------


def _partition_tool_calls(
    tool_calls: list[dict[str, Any]],
    tools_by_name: dict[str, BaseTool],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition tool calls into concurrent and sequential batches.

    Translation of partitionToolCalls() from toolOrchestration.ts.
    Read-only + concurrency-safe tools go to concurrent batch.
    """
    concurrent: list[dict[str, Any]] = []
    sequential: list[dict[str, Any]] = []

    for tc in tool_calls:
        func = tc.get("function", {})
        tool_name = func.get("name", "")
        arguments_str = func.get("arguments", "{}")

        tool = tools_by_name.get(tool_name)
        if tool is None:
            sequential.append(tc)
            continue

        try:
            tool_input = json.loads(arguments_str) if arguments_str else {}
        except json.JSONDecodeError:
            sequential.append(tc)
            continue

        if tool.check_is_concurrency_safe(tool_input):
            concurrent.append(tc)
        else:
            sequential.append(tc)

    return concurrent, sequential


# ---------------------------------------------------------------------------
# Main orchestration (with streaming)
# ---------------------------------------------------------------------------


async def run_tools(
    tool_calls: list[dict[str, Any]],
    tools_by_name: dict[str, BaseTool],
    cwd: str,
    permission_checker: Any | None = None,
    **kwargs: Any,
) -> list[ToolResultMessage]:
    """Execute tool calls and return results.

    For non-streaming callers. Collects all results synchronously.
    """
    results: list[ToolResultMessage] = []
    async for event_or_result in run_tools_streaming(
        tool_calls=tool_calls,
        tools_by_name=tools_by_name,
        cwd=cwd,
        permission_checker=permission_checker,
        **kwargs,
    ):
        if isinstance(event_or_result, ToolResultMessage):
            results.append(event_or_result)
    return results


async def run_tools_streaming(
    tool_calls: list[dict[str, Any]],
    tools_by_name: dict[str, BaseTool],
    cwd: str,
    permission_checker: Any | None = None,
    **kwargs: Any,
) -> AsyncIterator[ToolProgressEvent | ToolResultMessage]:
    """Execute tool calls as an async generator, yielding progress events.

    Translation of runTools() async generator from toolOrchestration.ts.
    Yields ToolProgressEvent for status updates and ToolResultMessage for results.
    """
    if not tool_calls:
        return

    concurrent_calls, sequential_calls = _partition_tool_calls(tool_calls, tools_by_name)

    # Execute concurrent tools in parallel
    if concurrent_calls:
        # Yield started events
        for tc in concurrent_calls:
            func = tc.get("function", {})
            yield ToolProgressEvent(
                tool_call_id=tc.get("id", ""),
                tool_name=func.get("name", ""),
                status=ToolExecutionStatus.STARTED,
            )

        tasks = [
            _execute_single_tool(tc, tools_by_name, cwd, permission_checker=permission_checker, **kwargs)
            for tc in concurrent_calls
        ]
        concurrent_results = await asyncio.gather(*tasks, return_exceptions=True)

        for tc, result in zip(concurrent_calls, concurrent_results):
            tc_id = tc.get("id", "")
            func = tc.get("function", {})
            tool_name = func.get("name", "")

            if isinstance(result, Exception):
                msg = ToolResultMessage(tool_call_id=tc_id, content=f"Error: {result}")
                yield ToolProgressEvent(
                    tool_call_id=tc_id,
                    tool_name=tool_name,
                    status=ToolExecutionStatus.ERROR,
                    result_preview=str(result)[:200],
                )
                yield msg
            else:
                yield ToolProgressEvent(
                    tool_call_id=tc_id,
                    tool_name=tool_name,
                    status=ToolExecutionStatus.COMPLETED,
                    result_preview=result.content[:200] if result.content else "",
                )
                yield result

    # Execute sequential tools one by one
    for tc in sequential_calls:
        tc_id = tc.get("id", "")
        func = tc.get("function", {})
        tool_name = func.get("name", "")

        yield ToolProgressEvent(
            tool_call_id=tc_id,
            tool_name=tool_name,
            status=ToolExecutionStatus.STARTED,
        )

        try:
            start = time.monotonic()
            result = await _execute_single_tool(
                tc, tools_by_name, cwd, permission_checker=permission_checker, **kwargs
            )
            duration = (time.monotonic() - start) * 1000
            yield ToolProgressEvent(
                tool_call_id=tc_id,
                tool_name=tool_name,
                status=ToolExecutionStatus.COMPLETED,
                duration_ms=duration,
                result_preview=result.content[:200] if result.content else "",
            )
            yield result
        except Exception as e:
            yield ToolProgressEvent(
                tool_call_id=tc_id,
                tool_name=tool_name,
                status=ToolExecutionStatus.ERROR,
                result_preview=str(e)[:200],
            )
            yield ToolResultMessage(tool_call_id=tc_id, content=f"Error: {e}")


# ---------------------------------------------------------------------------
# Single tool execution
# ---------------------------------------------------------------------------


async def _execute_single_tool(
    tool_call: dict[str, Any],
    tools_by_name: dict[str, BaseTool],
    cwd: str,
    permission_checker: Any | None = None,
    **kwargs: Any,
) -> ToolResultMessage:
    """Execute a single tool call and return a ToolResultMessage."""
    tc_id = tool_call.get("id", "")
    func = tool_call.get("function", {})
    tool_name = func.get("name", "")
    arguments_str = func.get("arguments", "{}")

    tool = tools_by_name.get(tool_name)
    if tool is None:
        return ToolResultMessage(
            tool_call_id=tc_id,
            content=f"Error: Unknown tool '{tool_name}'",
        )

    # Parse arguments
    try:
        tool_input = json.loads(arguments_str) if arguments_str else {}
    except json.JSONDecodeError as e:
        return ToolResultMessage(
            tool_call_id=tc_id,
            content=f"Error parsing tool arguments: {e}",
        )

    # Check permissions
    if permission_checker is not None:
        is_read_only = tool.check_is_read_only(tool_input)

        # Special handling for Bash command classification
        if tool_name == "Bash":
            from claude_code.permissions.classifier import is_read_only_bash

            command = tool_input.get("command", "")
            is_read_only = is_read_only_bash(command)

        perm_result = permission_checker.check(
            tool_name=tool_name,
            tool_input=tool_input,
            is_read_only=is_read_only,
        )
        if perm_result.behavior == "deny":
            msg = perm_result.message or f"Permission denied for tool '{tool_name}'"
            return ToolResultMessage(tool_call_id=tc_id, content=msg)
        if perm_result.behavior == "ask":
            # In interactive mode, we would prompt the user here.
            # For now, auto-allow (matches bypassPermissions behavior).
            logger.debug("Permission 'ask' for %s — auto-allowing", tool_name)

    # Validate input
    validation = await tool.validate_input(tool_input)
    if not validation.result:
        return ToolResultMessage(
            tool_call_id=tc_id,
            content=f"Validation error: {validation.message}",
        )

    # Execute
    logger.debug("Executing tool %s with input: %s", tool_name, tool_input)
    result = await tool.execute(tool_input=tool_input, cwd=cwd, **kwargs)

    content = str(result.data) if result.data is not None else "(no output)"

    # Truncate if result exceeds max size
    if len(content) > tool.max_result_size_chars:
        content = content[: tool.max_result_size_chars] + "\n... (truncated)"

    return ToolResultMessage(tool_call_id=tc_id, content=content)
