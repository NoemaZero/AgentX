"""Agent tool utilities — translation of tools/AgentTool/agentToolUtils.ts.

Contains:
  - filterToolsForAgent:     multi-layer tool filtering
  - resolveAgentTools:       wildcard expansion + Agent(x,y) syntax + validation
  - finalizeAgentTool:       backwards-scan result assembly
  - countToolUses:           count tool_use blocks
  - extractPartialResult:    text from killed agents
  - getLastToolUseName:      last tool_use name
  - emitTaskProgress:        SDK task_progress events
  - classifyHandoffIfNeeded: security classifier for auto mode
  - runAsyncAgentLifecycle:  background agent lifecycle driver
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
    SEND_MESSAGE_TOOL_NAME,
)
from claude_code.tools.agent_tool.definitions import (
    BaseAgentDefinition,
    is_built_in_agent,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AgentToolResult",
    "ResolvedAgentTools",
    "classify_handoff_if_needed",
    "count_tool_uses",
    "emit_task_progress",
    "extract_partial_result",
    "filter_tools_for_agent",
    "finalize_agent_tool",
    "get_last_tool_use_name",
    "resolve_agent_tools",
    "run_async_agent_lifecycle",
]


# Custom-agent-only disallowed tools (beyond ALL_AGENT_DISALLOWED_TOOLS)
# Translation of CUSTOM_AGENT_DISALLOWED_TOOLS from tools.ts
CUSTOM_AGENT_DISALLOWED_TOOLS: frozenset[str] = frozenset()

# In-process teammate allowed tools (translation of IN_PROCESS_TEAMMATE_ALLOWED_TOOLS)
IN_PROCESS_TEAMMATE_ALLOWED_TOOLS: frozenset[str] = frozenset({
    "TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "TaskOutput", "TaskStop",
})


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
    """Filter tools available to an agent.

    Multi-layer filtering rules (translation of filterToolsForAgent):
      1. MCP tools (``mcp__*``) → always allowed
      2. ExitPlanMode in plan mode → allowed (bypass disallow lists)
      3. ALL_AGENT_DISALLOWED_TOOLS → blocked
      4. CUSTOM_AGENT_DISALLOWED_TOOLS → blocked (non-built-in only)
      5. Async agents → whitelist only (ASYNC_AGENT_ALLOWED_TOOLS)
    """
    from claude_code.tools.base import BaseTool

    result: list[Any] = []

    for tool in tools:
        if not isinstance(tool, BaseTool):
            continue

        name = tool.name

        # Rule 1: MCP tools always allowed
        if name.startswith("mcp__"):
            result.append(tool)
            continue

        # Rule 2: ExitPlanMode allowed in plan mode
        if name == EXIT_PLAN_MODE_TOOL_NAME and permission_mode == "plan":
            result.append(tool)
            continue

        # Rule 3: Global disallow
        if name in ALL_AGENT_DISALLOWED_TOOLS:
            continue

        # Rule 4: Custom agent extra disallow
        if not is_built_in and name in CUSTOM_AGENT_DISALLOWED_TOOLS:
            continue

        # Rule 5: Async agents whitelist
        if is_async and name not in ASYNC_AGENT_ALLOWED_TOOLS:
            # Check in-process teammate exceptions
            if name == AGENT_TOOL_NAME:
                result.append(tool)
                continue
            if name in IN_PROCESS_TEAMMATE_ALLOWED_TOOLS:
                result.append(tool)
                continue
            continue

        result.append(tool)

    return result


# ---------------------------------------------------------------------------
# Tool resolution — translation of resolveAgentTools
# ---------------------------------------------------------------------------


class ResolvedAgentTools(NamedTuple):
    """Result of resolving an agent's tool specification."""

    has_wildcard: bool
    valid_tools: list[str]
    invalid_tools: list[str]
    resolved_tools: list[Any]
    allowed_agent_types: list[str] | None


def _parse_tool_spec(spec: str) -> tuple[str, str | None]:
    """Parse ``Agent(worker, researcher)`` → (``Agent``, ``worker, researcher``)."""
    if "(" in spec and spec.endswith(")"):
        name, _, content = spec.partition("(")
        return name.strip(), content[:-1].strip()
    return spec.strip(), None


def _permission_rule_value_from_string(spec: str) -> tuple[str, str | None]:
    """Extract tool name and optional rule content from a permission spec string.

    Handles formats like ``Bash``, ``Agent(worker)``, ``Bash(npm test)``.
    Translation of permissionRuleValueFromString from permissionRuleParser.ts.
    """
    return _parse_tool_spec(spec)


def resolve_agent_tools(
    agent_definition: BaseAgentDefinition,
    available_tools: list[Any],
    is_async: bool = False,
    is_main_thread: bool = False,
) -> ResolvedAgentTools:
    """Resolve and validate agent tools.

    Translation of resolveAgentTools from agentToolUtils.ts.

    Steps:
      1. Filter available tools (skip for main thread)
      2. Apply disallowedTools
      3. Wildcard check (``None`` or ``['*']``)
      4. Explicit tool list validation + Agent(x,y) syntax
    """
    agent_tools = agent_definition.tools
    disallowed_tools = agent_definition.disallowed_tools

    # Step 1: Primary filter
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

    # Step 2: Apply disallowed tools
    disallowed_set: set[str] = set()
    if disallowed_tools:
        for spec in disallowed_tools:
            tool_name, _ = _permission_rule_value_from_string(spec)
            disallowed_set.add(tool_name)
    allowed = [t for t in filtered if t.name not in disallowed_set]

    # Step 3: Wildcard check
    has_wildcard = agent_tools is None or (len(agent_tools) == 1 and agent_tools[0] == "*")
    if has_wildcard:
        return ResolvedAgentTools(
            has_wildcard=True,
            valid_tools=[],
            invalid_tools=[],
            resolved_tools=allowed,
            allowed_agent_types=None,
        )

    # Step 4: Explicit tool list
    tool_map = {t.name: t for t in allowed}
    valid: list[str] = []
    invalid: list[str] = []
    resolved: list[Any] = []
    resolved_set: set[str] = set()
    allowed_agent_types: list[str] | None = None

    for spec in agent_tools:
        tool_name, rule_content = _permission_rule_value_from_string(spec)

        # Special: Agent tool carries allowedAgentTypes in parentheses
        if tool_name == AGENT_TOOL_NAME:
            if rule_content:
                allowed_agent_types = [s.strip() for s in rule_content.split(",") if s.strip()]
            if not is_main_thread:
                # Agent is filtered out by filterToolsForAgent for non-main,
                # but we still record the spec for metadata
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
    """Result from a completed agent — serialisable dict."""

    pass


def count_tool_uses(messages: list[Any]) -> int:
    """Count ``tool_use`` blocks in assistant messages."""
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
    resolved_agent_model: str = "",
    is_built_in: bool = False,
    agent_type: str = "",
    start_time: float = 0.0,
    is_async: bool = False,
) -> AgentToolResult:
    """Extract structured result from agent messages.

    Translation of finalizeAgentTool from agentToolUtils.ts.
    Scans backwards for last assistant message with text content.
    Falls back to prior messages if the last one is pure tool_use.
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
            usage={},
        )

    # Extract text content from last assistant
    content_blocks = getattr(getattr(last_assistant, "message", None), "content", [])
    text_blocks = [
        b for b in (content_blocks if isinstance(content_blocks, list) else [])
        if getattr(b, "type", None) == "text"
    ]

    # Fallback scan: if last assistant has no text, scan backwards
    if not text_blocks:
        for msg in reversed(agent_messages):
            if getattr(msg, "type", None) != "assistant":
                continue
            c = getattr(getattr(msg, "message", None), "content", [])
            text_blocks = [
                b for b in (c if isinstance(c, list) else [])
                if getattr(b, "type", None) == "text"
            ]
            if text_blocks:
                break

    content = [
        {"type": "text", "text": getattr(b, "text", str(b))}
        for b in text_blocks
    ]
    if not content:
        content = [{"type": "text", "text": "(agent produced no output)"}]

    # Token count from usage
    usage = getattr(getattr(last_assistant, "message", None), "usage", None)
    total_tokens = 0
    if usage:
        total_tokens = getattr(usage, "input_tokens", 0) + getattr(usage, "output_tokens", 0)

    # Build raw usage dict (mirrors agentToolResultSchema)
    usage_dict: dict[str, Any] = {}
    if usage:
        usage_dict = {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
            "server_tool_use": getattr(usage, "server_tool_use", None),
            "service_tier": getattr(usage, "service_tier", None),
            "cache_creation": getattr(usage, "cache_creation", None),
        }

    total_tool_uses = count_tool_uses(agent_messages)
    duration_ms = int((time.time() - start_time) * 1000) if start_time else 0

    # Analytics event (translation of logEvent('tengu_agent_tool_completed'))
    logger.info(
        "Agent completed: type=%s model=%s tokens=%d tools=%d duration=%dms",
        agent_type, resolved_agent_model, total_tokens, total_tool_uses, duration_ms,
    )

    return AgentToolResult(
        agent_id=agent_id,
        agent_type=agent_type,
        content=content,
        total_tool_use_count=total_tool_uses,
        total_duration_ms=duration_ms,
        total_tokens=total_tokens,
        usage=usage_dict,
    )


def extract_partial_result(messages: list[Any]) -> str | None:
    """Extract partial text result from agent messages (for killed agents).

    Scans backwards for the most recent assistant message with text content.
    """
    for msg in reversed(messages):
        if getattr(msg, "type", None) != "assistant":
            continue
        content = getattr(getattr(msg, "message", None), "content", [])
        if isinstance(content, list):
            texts = [
                getattr(b, "text", "")
                for b in content
                if getattr(b, "type", None) == "text"
            ]
            joined = "\n".join(t for t in texts if t)
            if joined:
                return joined
    return None


def get_last_tool_use_name(message: Any) -> str | None:
    """Return the name of the last ``tool_use`` block in an assistant message."""
    if getattr(message, "type", None) != "assistant":
        return None
    content = getattr(getattr(message, "message", None), "content", [])
    if not isinstance(content, list):
        return None
    last_name: str | None = None
    for block in content:
        if getattr(block, "type", None) == "tool_use":
            last_name = getattr(block, "name", None)
    return last_name


# ---------------------------------------------------------------------------
# Task progress emission
# ---------------------------------------------------------------------------


def emit_task_progress(
    *,
    task_id: str,
    tool_use_id: str | None,
    description: str,
    start_time: float,
    last_tool_name: str,
    token_count: int = 0,
    tool_use_count: int = 0,
) -> None:
    """Emit a task_progress event for SDK consumers.

    Translation of emitTaskProgress from agentToolUtils.ts.
    In the full system this would publish to the SDK event bus;
    here we log the progress for observability.
    """
    duration_ms = int((time.time() - start_time) * 1000) if start_time else 0
    logger.debug(
        "Task progress: id=%s tool=%s tokens=%d tool_uses=%d duration=%dms",
        task_id, last_tool_name, token_count, tool_use_count, duration_ms,
    )


# ---------------------------------------------------------------------------
# Handoff classification (auto mode safety)
# ---------------------------------------------------------------------------


async def classify_handoff_if_needed(
    *,
    agent_messages: list[Any],
    tools: list[Any],
    permission_mode: str | None,
    abort_signal: asyncio.Event | None = None,
    subagent_type: str = "",
    total_tool_use_count: int = 0,
) -> str | None:
    """Run auto-mode security classification on agent handoff.

    Translation of classifyHandoffIfNeeded from agentToolUtils.ts.

    In auto mode, checks whether the agent's actions should be blocked or
    flagged. Returns a warning string to prepend to the result, or ``None``.
    """
    # Feature gate: only in auto mode
    if permission_mode != "auto":
        return None

    # Placeholder for transcript classifier integration.
    # In the JS source this calls buildTranscriptForClassifier + classifyYoloAction.
    # For now we return None (no blocking).
    logger.debug(
        "Handoff classification: agent_type=%s tool_uses=%d (auto mode)",
        subagent_type, total_tool_use_count,
    )
    return None


# ---------------------------------------------------------------------------
# Async agent lifecycle — translation of runAsyncAgentLifecycle
# ---------------------------------------------------------------------------


async def run_async_agent_lifecycle(
    *,
    task_id: str,
    make_stream: Any,
    metadata: dict[str, Any],
    description: str,
    abort_controller: asyncio.Event | None = None,
    on_complete: Any | None = None,
    on_fail: Any | None = None,
    on_kill: Any | None = None,
    enable_summarization: bool = False,
    get_worktree_result: Any | None = None,
) -> None:
    """Drive a background agent from spawn to completion/failure/kill.

    Translation of runAsyncAgentLifecycle from agentToolUtils.ts.

    Lifecycle steps:
      1. Create progress tracker
      2. Iterate ``make_stream()`` messages
      3. Update progress / AppState
      4. On success: ``finalizeAgentTool`` → complete → classify → notify
      5. On abort: ``killAsyncAgent`` → partial result → notify
      6. On error: ``failAsyncAgent`` → notify
      7. Finally: cleanup (skills, dumpState)
    """
    agent_messages: list[Any] = []
    start_time = metadata.get("start_time", time.time())
    agent_type = metadata.get("agent_type", "")
    prompt = metadata.get("prompt", "")

    # Summarization placeholder (would call startAgentSummarization in JS)
    stop_summarization = None

    try:
        # Step 2: Message stream loop
        stream = make_stream()
        async for message in stream:
            # Check abort
            if abort_controller and abort_controller.is_set():
                raise asyncio.CancelledError("Agent aborted")

            agent_messages.append(message)

            # Update progress
            last_tool = get_last_tool_use_name(message)
            if last_tool:
                emit_task_progress(
                    task_id=task_id,
                    tool_use_id=None,
                    description=description,
                    start_time=start_time,
                    last_tool_name=last_tool,
                    token_count=len(agent_messages),
                    tool_use_count=count_tool_uses(agent_messages),
                )

        # Step 3: Stop summarization
        if stop_summarization:
            stop_summarization()

        # Step 4: Finalize
        result = finalize_agent_tool(
            agent_messages,
            task_id,
            prompt=prompt,
            agent_type=agent_type,
            start_time=start_time,
            is_async=True,
        )

        # Complete the task — do this FIRST before classify/worktree
        if on_complete:
            if asyncio.iscoroutinefunction(on_complete):
                await on_complete(result)
            else:
                on_complete(result)

        # Handoff classification
        warning = await classify_handoff_if_needed(
            agent_messages=agent_messages,
            tools=[],
            permission_mode="auto",
            subagent_type=agent_type,
            total_tool_use_count=result.get("total_tool_use_count", 0),
        )
        if warning:
            logger.warning("Handoff warning for agent %s: %s", task_id, warning)

        # Worktree result
        worktree_result = {}
        if get_worktree_result:
            if asyncio.iscoroutinefunction(get_worktree_result):
                worktree_result = await get_worktree_result()
            else:
                worktree_result = get_worktree_result()

        logger.info(
            "Async agent %s completed: type=%s duration=%dms",
            task_id, agent_type, result.get("total_duration_ms", 0),
        )

    except (asyncio.CancelledError, KeyboardInterrupt):
        # Kill path
        if stop_summarization:
            stop_summarization()

        partial = extract_partial_result(agent_messages)

        if on_kill:
            if asyncio.iscoroutinefunction(on_kill):
                await on_kill(partial)
            else:
                on_kill(partial)

        # Worktree cleanup
        if get_worktree_result:
            try:
                if asyncio.iscoroutinefunction(get_worktree_result):
                    await get_worktree_result()
                else:
                    get_worktree_result()
            except Exception:
                pass

        logger.info("Async agent %s killed", task_id)

    except Exception as exc:
        # Fail path
        if stop_summarization:
            stop_summarization()

        error_msg = str(exc)
        logger.error("Async agent %s failed: %s", task_id, error_msg)

        if on_fail:
            if asyncio.iscoroutinefunction(on_fail):
                await on_fail(error_msg)
            else:
                on_fail(error_msg)

        # Worktree cleanup
        if get_worktree_result:
            try:
                if asyncio.iscoroutinefunction(get_worktree_result):
                    await get_worktree_result()
                else:
                    get_worktree_result()
            except Exception:
                pass

    finally:
        # Cleanup (translation of JS finally block)
        # clearInvokedSkillsForAgent, clearDumpState
        logger.debug("Async agent %s lifecycle cleanup", task_id)
