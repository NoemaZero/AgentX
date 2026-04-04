"""Agent tool utilities — translation of tools/AgentTool/agentToolUtils.ts.

Contains:
  - filterToolsForAgent: tool filtering by agent type/async/built-in
  - resolveAgentTools: full tool resolution with wildcard expansion
  - finalizeAgentTool: result collection from agent messages
  - countToolUses: count tool_use blocks in messages
  - extractPartialResult: extract text from killed agent
  - runAsyncAgentLifecycle: background agent lifecycle driver
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, NamedTuple

from claude_code.data_types import (
    Message,
    StreamEvent,
    StreamEventType,
    TaskStatus,
)
from claude_code.tools.tool_names import (
    AGENT_TOOL_NAME,
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    EXIT_PLAN_MODE_TOOL_NAME,
    LEGACY_AGENT_TOOL_NAME,
)
from claude_code.tools.agent_tool.definitions import (
    BaseAgentDefinition,
    is_built_in_agent,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AgentToolResult",
    "ResolvedAgentTools",
    "count_tool_uses",
    "extract_partial_result",
    "filter_tools_for_agent",
    "finalize_agent_tool",
    "resolve_agent_tools",
    "run_async_agent_lifecycle",
]

# Custom agent disallowed tools (TS: CUSTOM_AGENT_DISALLOWED_TOOLS)
# Custom (non-built-in) agents have additional restrictions
CUSTOM_AGENT_DISALLOWED_TOOLS: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# Tool filtering — translation of filterToolsForAgent
# ---------------------------------------------------------------------------


def filter_tools_for_agent(
    tools: list[Any],
    *,
    is_built_in: bool = True,
    is_async: bool = False,
    permission_mode: str | None = None,
) -> list[Any]:
    """Filter tools available to an agent based on type/async status.

    Translation of filterToolsForAgent from agentToolUtils.ts.
    """
    from claude_code.tools.base import BaseTool

    result: list[Any] = []

    for tool in tools:
        if not isinstance(tool, BaseTool):
            continue

        name = tool.name

        # MCP tools always allowed
        if name.startswith("mcp__"):
            result.append(tool)
            continue

        # ExitPlanMode allowed in plan mode
        if name == EXIT_PLAN_MODE_TOOL_NAME and permission_mode == "plan":
            result.append(tool)
            continue

        # Hard disallowed
        if name in ALL_AGENT_DISALLOWED_TOOLS:
            continue

        # Custom agent extra disallowed
        if not is_built_in and name in CUSTOM_AGENT_DISALLOWED_TOOLS:
            continue

        # Async agents: whitelist only
        if is_async and name not in ASYNC_AGENT_ALLOWED_TOOLS:
            continue

        result.append(tool)

    return result


# ---------------------------------------------------------------------------
# Tool resolution — translation of resolveAgentTools
# ---------------------------------------------------------------------------


class ResolvedAgentTools(NamedTuple):
    """Result of resolving an agent's tool specification against available tools."""

    has_wildcard: bool
    valid_tools: list[str]
    invalid_tools: list[str]
    resolved_tools: list[Any]
    allowed_agent_types: list[str] | None


def _parse_tool_spec(spec: str) -> tuple[str, str | None]:
    """Parse a tool spec like ``Agent(worker, researcher)`` into (name, rule_content)."""
    if "(" in spec and spec.endswith(")"):
        name, _, content = spec.partition("(")
        return name.strip(), content[:-1].strip()
    return spec.strip(), None


def resolve_agent_tools(
    agent_definition: BaseAgentDefinition,
    available_tools: list[Any],
    is_async: bool = False,
    is_main_thread: bool = False,
) -> ResolvedAgentTools:
    """Resolve and validate agent tools against available tools.

    Handles wildcard expansion and validation in one place.
    Translation of resolveAgentTools from agentToolUtils.ts.
    """
    agent_tools = agent_definition.tools
    disallowed_tools = agent_definition.disallowed_tools

    # First-pass filter (skip for main thread)
    filtered = (
        available_tools
        if is_main_thread
        else filter_tools_for_agent(
            available_tools,
            is_built_in=is_built_in_agent(agent_definition),
            is_async=is_async,
            permission_mode=agent_definition.permission_mode,
        )
    )

    # Apply disallowed tools
    disallowed_set: set[str] = set()
    if disallowed_tools:
        for spec in disallowed_tools:
            tool_name, _ = _parse_tool_spec(spec)
            disallowed_set.add(tool_name)
    allowed = [t for t in filtered if t.name not in disallowed_set]

    # Wildcard: no explicit tools or ['*']
    has_wildcard = agent_tools is None or (len(agent_tools) == 1 and agent_tools[0] == "*")
    if has_wildcard:
        return ResolvedAgentTools(
            has_wildcard=True,
            valid_tools=[],
            invalid_tools=[],
            resolved_tools=allowed,
            allowed_agent_types=None,
        )

    # Explicit tool list
    tool_map = {t.name: t for t in allowed}
    valid: list[str] = []
    invalid: list[str] = []
    resolved: list[Any] = []
    resolved_set: set[str] = set()
    allowed_agent_types: list[str] | None = None

    for spec in agent_tools:
        tool_name, rule_content = _parse_tool_spec(spec)

        # Special case: Agent tool carries allowedAgentTypes
        if tool_name == AGENT_TOOL_NAME:
            if rule_content:
                allowed_agent_types = [s.strip() for s in rule_content.split(",")]
            if not is_main_thread:
                valid.append(spec)
                continue

        tool = tool_map.get(tool_name)
        if tool:
            valid.append(spec)
            if tool_name not in resolved_set:
                resolved.append(tool)
                resolved_set.add(tool_name)
        else:
            invalid.append(spec)

    return ResolvedAgentTools(
        has_wildcard=False,
        valid_tools=valid,
        invalid_tools=invalid,
        resolved_tools=resolved,
        allowed_agent_types=allowed_agent_types,
    )


# ---------------------------------------------------------------------------
# Result collection — translation of finalizeAgentTool + countToolUses
# ---------------------------------------------------------------------------


class AgentToolResult(dict):
    """Result from a completed agent, as a dict for JSON serialization."""

    pass


def count_tool_uses(messages: list[Any]) -> int:
    """Count tool_use blocks in assistant messages."""
    count = 0
    for msg in messages:
        if getattr(msg, "type", None) == "assistant":
            content = getattr(getattr(msg, "message", None), "content", [])
            if isinstance(content, list):
                for block in content:
                    if getattr(block, "type", None) == "tool_use":
                        count += 1
    return count


def finalize_agent_tool(
    agent_messages: list[Any],
    agent_id: str,
    *,
    prompt: str = "",
    agent_type: str = "",
    start_time: float = 0.0,
    is_async: bool = False,
) -> AgentToolResult:
    """Extract final result from agent messages.

    Translation of finalizeAgentTool from agentToolUtils.ts.
    Scans backwards for the last assistant message with text content.
    """
    # Find last assistant message
    last_assistant = None
    for msg in reversed(agent_messages):
        if getattr(msg, "type", None) == "assistant":
            last_assistant = msg
            break

    if last_assistant is None:
        return AgentToolResult(
            agent_id=agent_id,
            agent_type=agent_type,
            content=[{"type": "text", "text": "(agent produced no output)"}],
            total_tool_use_count=0,
            total_duration_ms=int((time.time() - start_time) * 1000) if start_time else 0,
            total_tokens=0,
        )

    # Extract text content from last assistant, fallback scan
    content_blocks = getattr(getattr(last_assistant, "message", None), "content", [])
    text_blocks = [b for b in (content_blocks if isinstance(content_blocks, list) else [])
                   if getattr(b, "type", None) == "text"]

    if not text_blocks:
        # Fallback: scan backwards for any assistant with text
        for msg in reversed(agent_messages):
            if getattr(msg, "type", None) != "assistant":
                continue
            c = getattr(getattr(msg, "message", None), "content", [])
            text_blocks = [b for b in (c if isinstance(c, list) else [])
                           if getattr(b, "type", None) == "text"]
            if text_blocks:
                break

    content = [{"type": "text", "text": getattr(b, "text", str(b))} for b in text_blocks]
    if not content:
        content = [{"type": "text", "text": "(agent produced no output)"}]

    # Token count from usage
    usage = getattr(getattr(last_assistant, "message", None), "usage", None)
    total_tokens = 0
    if usage:
        total_tokens = getattr(usage, "input_tokens", 0) + getattr(usage, "output_tokens", 0)

    total_tool_uses = count_tool_uses(agent_messages)
    duration_ms = int((time.time() - start_time) * 1000) if start_time else 0

    return AgentToolResult(
        agent_id=agent_id,
        agent_type=agent_type,
        content=content,
        total_tool_use_count=total_tool_uses,
        total_duration_ms=duration_ms,
        total_tokens=total_tokens,
    )


def extract_partial_result(messages: list[Any]) -> str | None:
    """Extract partial result text from agent messages (for killed agents).

    Scans backwards for the most recent assistant message with text content.
    """
    for msg in reversed(messages):
        if getattr(msg, "type", None) != "assistant":
            continue
        content = getattr(getattr(msg, "message", None), "content", [])
        if isinstance(content, list):
            texts = [getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text"]
            joined = "\n".join(t for t in texts if t)
            if joined:
                return joined
    return None


# ---------------------------------------------------------------------------
# Async agent lifecycle — translation of runAsyncAgentLifecycle
# ---------------------------------------------------------------------------


async def run_async_agent_lifecycle(
    *,
    task_id: str,
    make_stream: Any,  # callable returning AsyncGenerator[Message, None]
    metadata: dict[str, Any],
    description: str,
    on_complete: Any | None = None,  # callback(result)
    on_fail: Any | None = None,  # callback(error)
    on_kill: Any | None = None,  # callback(partial)
    abort_event: asyncio.Event | None = None,
) -> None:
    """Drive a background agent from spawn to terminal notification.

    Translation of runAsyncAgentLifecycle from agentToolUtils.ts.
    """
    agent_messages: list[Any] = []
    start_time = metadata.get("start_time", time.time())

    try:
        stream = make_stream()
        async for message in stream:
            # Check abort
            if abort_event and abort_event.is_set():
                raise asyncio.CancelledError("Agent aborted")

            agent_messages.append(message)

        # Success path
        result = finalize_agent_tool(
            agent_messages,
            task_id,
            prompt=metadata.get("prompt", ""),
            agent_type=metadata.get("agent_type", ""),
            start_time=start_time,
            is_async=True,
        )

        if on_complete:
            await on_complete(result) if asyncio.iscoroutinefunction(on_complete) else on_complete(result)

    except (asyncio.CancelledError, KeyboardInterrupt):
        # Kill path
        partial = extract_partial_result(agent_messages)
        if on_kill:
            await on_kill(partial) if asyncio.iscoroutinefunction(on_kill) else on_kill(partial)

    except Exception as exc:
        # Fail path
        logger.error("Async agent %s failed: %s", task_id, exc)
        if on_fail:
            await on_fail(str(exc)) if asyncio.iscoroutinefunction(on_fail) else on_fail(str(exc))
