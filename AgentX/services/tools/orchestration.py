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

from AgentX.tools.base import BaseTool
from AgentX.data_types import (
    PermissionBehavior,
    PermissionDecision,
    ToolExecutionStatus,
    ToolResultMessage,
)
from AgentX.pydantic_models import FrozenModel

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
# Helpers
# ---------------------------------------------------------------------------


def _parse_tool_call(tc: dict[str, Any]) -> tuple[str, str, str]:
    """Parse tool call dict into (tool_call_id, tool_name, arguments_str)."""
    func = tc.get("function", {})
    return (
        tc.get("id", ""),
        func.get("name", ""),
        func.get("arguments", "{}"),
    )


def _yield_tool_result(
    tc_id: str,
    tool_name: str,
    status: ToolExecutionStatus,
    result: ToolResultMessage | None = None,
    duration_ms: float = 0.0,
    error: Exception | None = None,
) -> list[ToolProgressEvent | ToolResultMessage]:
    """Build the pair of progress event + optional result message."""
    preview = ""
    if result is not None and result.content:
        preview = result.content[:200]
    elif error is not None:
        preview = str(error)[:200]

    event = ToolProgressEvent(
        tool_call_id=tc_id,
        tool_name=tool_name,
        status=status,
        duration_ms=duration_ms,
        result_preview=preview,
    )
    return [event, result] if result is not None else [event]


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
        _, tool_name, arguments_str = _parse_tool_call(tc)

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
    hook_manager: Any | None = None,
    ask_callback: Any | None = None,
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
        hook_manager=hook_manager,
        ask_callback=ask_callback,
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
    hook_manager: Any | None = None,
    ask_callback: Any | None = None,
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
        for tc in concurrent_calls:
            tc_id, tool_name, _ = _parse_tool_call(tc)
            yield ToolProgressEvent(
                tool_call_id=tc_id,
                tool_name=tool_name,
                status=ToolExecutionStatus.STARTED,
            )

        tasks = [
            _execute_single_tool(
                tc, tools_by_name, cwd,
                permission_checker=permission_checker,
                hook_manager=hook_manager,
                ask_callback=ask_callback,
                **kwargs,
            )
            for tc in concurrent_calls
        ]
        concurrent_results = await asyncio.gather(*tasks, return_exceptions=True)

        for tc, result in zip(concurrent_calls, concurrent_results):
            tc_id, tool_name, _ = _parse_tool_call(tc)
            if isinstance(result, Exception):
                msg = ToolResultMessage(tool_call_id=tc_id, name=tool_name, content=f"Error: {result}")
                for item in _yield_tool_result(tc_id, tool_name, ToolExecutionStatus.ERROR, msg, error=result):
                    yield item
            else:
                for item in _yield_tool_result(tc_id, tool_name, ToolExecutionStatus.COMPLETED, result, result.duration_ms):
                    yield item

    # Execute sequential tools one by one
    for tc in sequential_calls:
        tc_id, tool_name, _ = _parse_tool_call(tc)

        yield ToolProgressEvent(
            tool_call_id=tc_id,
            tool_name=tool_name,
            status=ToolExecutionStatus.STARTED,
        )

        try:
            result = await _execute_single_tool(
                tc, tools_by_name, cwd,
                permission_checker=permission_checker,
                hook_manager=hook_manager,
                ask_callback=ask_callback,
                **kwargs,
            )
            for item in _yield_tool_result(tc_id, tool_name, ToolExecutionStatus.COMPLETED, result, result.duration_ms):
                yield item
        except Exception as e:
            msg = ToolResultMessage(tool_call_id=tc_id, name=tool_name, content=f"Error: {e}")
            for item in _yield_tool_result(tc_id, tool_name, ToolExecutionStatus.ERROR, msg, error=e):
                yield item


# ---------------------------------------------------------------------------
# Single tool execution
# ---------------------------------------------------------------------------


async def _check_permission(
    *,
    permission_checker: Any,
    tool_name: str,
    tool_input: dict[str, Any],
    tool: BaseTool,
    ask_callback: Any | None,
    tc_id: str,
) -> ToolResultMessage | None:
    """Check tool execution permission. Returns error message if denied, None if allowed."""
    is_read_only = tool.check_is_read_only(tool_input)

    # Special handling for Bash command classification
    if tool_name == "Bash":
        from AgentX.permissions.classifier import is_read_only_bash

        command = tool_input.get("command", "")
        is_read_only = is_read_only_bash(command)

    perm_result = permission_checker.check(
        tool_name=tool_name,
        tool_input=tool_input,
        is_read_only=is_read_only,
    )
    if perm_result.behavior == PermissionBehavior.DENY:
        msg = perm_result.message or f"Permission denied for tool '{tool_name}'"
        return ToolResultMessage(tool_call_id=tc_id, name=tool_name, content=msg)
    if perm_result.behavior == PermissionBehavior.ASK:
        # Interactive permission prompt
        if ask_callback is not None:
            try:
                decision = await ask_callback(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    is_read_only=is_read_only,
                )
                if decision == PermissionDecision.DENY:
                    return ToolResultMessage(
                        tool_call_id=tc_id,
                        name=tool_name,
                        content=f"Permission denied by user for tool '{tool_name}'",
                    )
                if decision == PermissionDecision.ALLOW_SESSION:
                    permission_checker.grant_session_permission(tool_name)
                # ALLOW_ONCE or ALLOW_SESSION → fall through to execute
            except Exception as ask_exc:
                logger.warning("Permission ask callback error: %s", ask_exc)
                return ToolResultMessage(
                    tool_call_id=tc_id,
                    name=tool_name,
                    content=f"Permission error for tool '{tool_name}': {ask_exc}",
                )
        else:
            # No interactive callback available — auto-allow (matches bypassPermissions)
            logger.debug("Permission 'ask' for %s — no callback, auto-allowing", tool_name)

    return None


async def _execute_single_tool(
    tool_call: dict[str, Any],
    tools_by_name: dict[str, BaseTool],
    cwd: str,
    permission_checker: Any | None = None,
    hook_manager: Any | None = None,
    ask_callback: Any | None = None,
    **kwargs: Any,
) -> ToolResultMessage:
    """Execute a single tool call and return a ToolResultMessage."""

    tc_id, tool_name, arguments_str = _parse_tool_call(tool_call)
    start = time.monotonic()

    tool = tools_by_name.get(tool_name)
    if tool is None:
        return ToolResultMessage(
            tool_call_id=tc_id,
            name=tool_name,
            content=f"Error: Unknown tool '{tool_name}'",
        )

    # Parse arguments
    try:
        tool_input = json.loads(arguments_str) if arguments_str else {}
    except json.JSONDecodeError as e:
        return ToolResultMessage(
            tool_call_id=tc_id,
            name=tool_name,
            content=f"Error parsing tool arguments: {e}",
        )

    # Check permissions
    if permission_checker is not None:
        perm_error = await _check_permission(
            permission_checker=permission_checker,
            tool_name=tool_name,
            tool_input=tool_input,
            tool=tool,
            ask_callback=ask_callback,
            tc_id=tc_id,
        )
        if perm_error is not None:
            return perm_error

    # Validate input
    validation = await tool.validate_input(tool_input)
    if not validation.result:
        return ToolResultMessage(
            tool_call_id=tc_id,
            name=tool_name,
            content=f"Validation error: {validation.message}",
        )

    # Pre-tool-use hooks (may modify input)
    if hook_manager is not None:
        try:
            tool_input = await hook_manager.run_pre_tool_use(
                tool_name=tool_name,
                tool_input=tool_input,
            )
        except Exception as hook_exc:
            logger.warning("Pre-tool-use hook error for %s: %s", tool_name, hook_exc)

    # Execute
    logger.debug("Executing tool %s with input: %s", tool_name, tool_input)
    result = await tool.execute(tool_input=tool_input, cwd=cwd, **kwargs)

    content = str(result.data) if result.data is not None else "(no output)"

    # Post-tool-use hooks (may modify output)
    if hook_manager is not None:
        try:
            content = await hook_manager.run_post_tool_use(
                tool_name=tool_name,
                tool_input=tool_input,
                tool_output=content,
            )
        except Exception as hook_exc:
            logger.warning("Post-tool-use hook error for %s: %s", tool_name, hook_exc)

    # Truncate if result exceeds max size
    if len(content) > tool.max_result_size_chars:
        content = content[: tool.max_result_size_chars] + "\n... (truncated)"

    duration_ms = (time.monotonic() - start) * 1000
    return ToolResultMessage(tool_call_id=tc_id, name=tool_name, content=content, duration_ms=duration_ms)
