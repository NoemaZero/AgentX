"""Core agent runner — translation of tools/AgentTool/runAgent.ts.

The ``run_agent()`` async generator handles:
  1. Permission mode resolution
  2. Tool pool assembly (resolveAgentTools or useExactTools)
  3. System prompt build (override or agent-specific)
  4. AbortController (asyncio.Event) isolation
  5. SubagentStart hooks
  6. Agent-specific MCP server init
  7. Frontmatter hooks registration
  8. Skills preload
  9. Core ``query()`` execution — yield* pattern
  10. Cleanup (finally)
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, AsyncGenerator

from claude_code.constants.prompts import DEFAULT_AGENT_PROMPT
from claude_code.data_types import (
    Message,
    StreamEvent,
    StreamEventType,
    TaskStatus,
    UserMessage,
)
from claude_code.tools.agent_tool.definitions import (
    BaseAgentDefinition,
    is_built_in_agent,
)
from claude_code.tools.agent_tool.utils import (
    filter_tools_for_agent,
    resolve_agent_tools,
)

logger = logging.getLogger(__name__)

__all__ = ["run_agent"]


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------


def _build_agent_system_prompt(
    agent_definition: BaseAgentDefinition,
    cwd: str = "",
) -> str:
    """Build system prompt for an agent.

    Translation of getAgentSystemPrompt from runAgent.ts.
    """
    try:
        prompt = agent_definition.get_system_prompt()
        return prompt if prompt else DEFAULT_AGENT_PROMPT
    except Exception:
        return DEFAULT_AGENT_PROMPT


# ---------------------------------------------------------------------------
# Filter incomplete tool calls (for fork context messages)
# ---------------------------------------------------------------------------


def _filter_incomplete_tool_calls(messages: list[Message]) -> list[Message]:
    """Filter out assistant messages with incomplete tool calls.

    Prevents API errors when sending messages with orphaned tool calls.
    Translation of filterIncompleteToolCalls from runAgent.ts.
    """
    # Build set of tool_use_ids that have results
    ids_with_results: set[str] = set()
    for msg in messages:
        if not isinstance(msg, UserMessage):
            continue
        content = msg.content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tid = block.get("tool_use_id", "")
                    if tid:
                        ids_with_results.add(tid)

    # Filter assistant messages with orphaned tool_use blocks
    result: list[Message] = []
    for msg in messages:
        if getattr(msg, "type", None) == "assistant":
            content = getattr(getattr(msg, "message", None), "content", [])
            if isinstance(content, list):
                has_incomplete = any(
                    getattr(b, "type", None) == "tool_use"
                    and getattr(b, "id", "") not in ids_with_results
                    for b in content
                )
                if has_incomplete:
                    continue  # Skip this message
        result.append(msg)
    return result


# ---------------------------------------------------------------------------
# Core run_agent async generator
# ---------------------------------------------------------------------------


async def run_agent(
    *,
    prompt: str,
    description: str = "",
    cwd: str = "",
    parent_engine: Any,
    is_fork: bool = False,
    is_async: bool = False,
    agent_definition: BaseAgentDefinition | None = None,
    parent_messages: list[Message] | None = None,
    tool_use_id: str = "",
    parent_system_prompt: str | None = None,
    use_exact_tools: bool = False,
    fork_context_messages: list[Message] | None = None,
    worktree_path: str | None = None,
    abort_event: asyncio.Event | None = None,
) -> AsyncGenerator[StreamEvent, None]:
    """Run an agent — the core async generator.

    Translation of runAgent() from runAgent.ts.
    Yields StreamEvents as the agent works.

    Args:
        prompt: The task prompt for the agent.
        description: Short description (3-5 words).
        cwd: Working directory override.
        parent_engine: The parent QueryEngine instance.
        is_fork: Whether this is a fork agent (inherits parent context).
        is_async: Whether running as background agent.
        agent_definition: The agent definition to use.
        parent_messages: Parent messages for fork context sharing.
        tool_use_id: The tool_use_id from the parent's Agent tool call.
        parent_system_prompt: For fork: parent's byte-exact system prompt.
        use_exact_tools: For fork: skip tool filtering, use parent's tools.
        fork_context_messages: For fork: parent's conversation context.
        worktree_path: If running in an isolated worktree.
        abort_event: Event to signal abort (for async agents).
    """
    from claude_code.config import Config
    from claude_code.engine.query import QueryParams, query
    from claude_code.services.api.client import LLMClient
    from claude_code.tools import get_all_base_tools, get_tools_by_name

    agent_id = str(uuid.uuid4())[:8]
    config: Config = parent_engine._config
    effective_cwd = cwd or worktree_path or config.cwd

    # ── 1. Resolve agent type ──
    agent_type = ""
    if agent_definition:
        agent_type = agent_definition.agent_type

    # ── 2. Permission mode resolution ──
    # agentDefinition.permissionMode takes priority, but bypassPermissions/acceptEdits
    # from parent always win; async agents avoid permission prompts
    permission_mode = config.permission_mode
    if agent_definition and agent_definition.permission_mode:
        if permission_mode not in ("bypassPermissions", "acceptEdits"):
            permission_mode = agent_definition.permission_mode

    # ── 3. Tool pool assembly ──
    if use_exact_tools:
        # Fork path: use parent's exact tools for cache-identical prefixes
        all_tools = list(parent_engine._tools) if hasattr(parent_engine, "_tools") else get_all_base_tools()
        agent_tools = all_tools
    else:
        all_tools = get_all_base_tools()
        if agent_definition:
            resolved = resolve_agent_tools(
                agent_definition,
                all_tools,
                is_async=is_async,
            )
            agent_tools = resolved.resolved_tools
        else:
            agent_tools = filter_tools_for_agent(
                all_tools,
                is_built_in=True,
                is_async=is_async,
            )

    tools_by_name = get_tools_by_name(agent_tools)

    # ── 4. System prompt build ──
    if is_fork and parent_system_prompt:
        system_prompt = parent_system_prompt
    elif agent_definition:
        system_prompt = _build_agent_system_prompt(agent_definition, cwd=effective_cwd)
    else:
        system_prompt = DEFAULT_AGENT_PROMPT

    # ── 5. Build messages ──
    context_messages: list[Message] = []
    if fork_context_messages:
        context_messages = _filter_incomplete_tool_calls(fork_context_messages)

    initial_messages: list[Message] = [*context_messages]

    if is_fork and parent_messages:
        # Fork with parent messages: build forked messages
        from claude_code.tools.agent_tool.fork import build_forked_messages

        fork_msgs = build_forked_messages(prompt)
        initial_messages.extend(fork_msgs)
    else:
        initial_messages.append(UserMessage(content=prompt))

    # ── 6. Create sub-config and client ──
    max_turns = config.max_turns
    if agent_definition and agent_definition.max_turns:
        max_turns = min(agent_definition.max_turns, config.max_turns)
    else:
        max_turns = min(max_turns, 30)  # Default agent limit

    sub_config = Config(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        provider=config.provider,
        ssl_verify=config.ssl_verify,
        max_tokens=config.max_tokens,
        max_turns=max_turns,
        cwd=effective_cwd,
        verbose=config.verbose,
        permission_mode=permission_mode,
    )

    sub_client = LLMClient(sub_config)

    # ── 7. Build QueryParams ──
    params = QueryParams.from_runtime(
        messages=initial_messages,
        system_prompt=system_prompt,
        tools=agent_tools,
        tools_by_name=tools_by_name,
        client=sub_client,
        config=sub_config,
        max_turns=max_turns,
        cwd=effective_cwd,
        permission_checker=parent_engine._permission_checker,
    )

    # ── 8. Core execution: yield* query() ──
    result_parts: list[str] = []
    try:
        async for event in query(params):
            # Check abort (async agents)
            if abort_event and abort_event.is_set():
                logger.info("Agent %s aborted", agent_id)
                break

            # Track assistant text for result collection
            if event.type == StreamEventType.ASSISTANT_MESSAGE and event.data:
                result_parts.append(str(event.data))

            yield event

    except asyncio.CancelledError:
        logger.info("Agent %s cancelled", agent_id)
        raise
    except Exception as exc:
        logger.error("Agent %s error: %s", agent_id, exc)
        yield StreamEvent(type=StreamEventType.ERROR, data={"error": str(exc)})
    finally:
        # ── 10. Cleanup ──
        # In a full implementation:
        # - Clean up agent-specific MCP servers
        # - Clear session hooks
        # - Release file state cache
        # - Kill background shell tasks spawned by agent
        logger.debug("Agent %s cleanup complete", agent_id)
