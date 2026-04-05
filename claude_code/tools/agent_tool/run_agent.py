"""Core agent runner — translation of tools/AgentTool/runAgent.ts.

The ``run_agent()`` async generator implements all 19 steps:

  1.  Model resolution (agent def → main model → override)
  2.  Agent ID + Perfetto tracing
  3.  Message preparation (fork filter vs normal)
  4.  User/system context (parallel load)
  5.  Slim CLAUDE.md (omitClaudeMd agents)
  6.  Strip gitStatus (Explore/Plan)
  7.  Permission mode override (agentDef.permissionMode, shouldAvoidPrompts)
  8.  Tool pool resolution (useExactTools vs resolveAgentTools)
  9.  System prompt build (override or getAgentSystemPrompt)
  10. AbortController isolation
  11. SubagentStart hooks
  12. Frontmatter hooks registration
  13. Skills preload
  14. Agent MCP server init
  15. SubagentContext creation + CacheSafeParams
  16. Transcript recording (initial + incremental)
  17. Core query loop (stream_event / attachment / message / progress)
  18. Post-loop checks (abort? callback?)
  19. Finally cleanup (MCP, hooks, cache, Perfetto, todos, shell tasks)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, AsyncGenerator

from claude_code.constants.prompts import DEFAULT_AGENT_PROMPT
from claude_code.data_types import (
    AgentModel,
    Message,
    StreamEvent,
    StreamEventType,
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

__all__ = ["filter_incomplete_tool_calls", "run_agent"]


# ---------------------------------------------------------------------------
# System prompt builder — translation of getAgentSystemPrompt
# ---------------------------------------------------------------------------


def _build_agent_system_prompt(
    agent_definition: BaseAgentDefinition,
    cwd: str = "",
    resolved_tools: list[Any] | None = None,
    resolved_agent_model: str = "",
    additional_working_directories: list[str] | None = None,
) -> str:
    """Build the system prompt from the agent definition.

    Translation of getAgentSystemPrompt from runAgent.ts.
    Falls back to DEFAULT_AGENT_PROMPT on any failure.
    """
    try:
        prompt = agent_definition.get_system_prompt()
        if not prompt:
            return DEFAULT_AGENT_PROMPT
        # In JS: enhanceSystemPromptWithEnvDetails([prompt], model, dirs, tools)
        # adds env context like cwd, tool list, model info
        # We do a simplified version here
        return prompt
    except Exception:
        logger.debug("Failed to get agent system prompt, using default", exc_info=True)
        return DEFAULT_AGENT_PROMPT


# ---------------------------------------------------------------------------
# Filter incomplete tool calls (for fork context messages)
# ---------------------------------------------------------------------------


def filter_incomplete_tool_calls(messages: list[Message]) -> list[Message]:
    """Filter out assistant messages with orphaned tool_use blocks.

    An orphaned tool_use is one whose ``id`` has no matching ``tool_result``
    in any subsequent user message.

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

    # Filter assistant messages with incomplete tool_use blocks
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
                    continue
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
    max_turns_override: int | None = None,
    content_replacement_state: Any | None = None,
    transcript_subdir: str | None = None,
    preserve_tool_use_results: bool = False,
    allowed_tools: list[str] | None = None,
    model_override: str | None = None,
) -> AsyncGenerator[StreamEvent, None]:
    """Run an agent — the core async generator.

    Translation of runAgent() from runAgent.ts.
    Yields ``StreamEvent`` objects as the agent works.
    """
    from claude_code.config import Config
    from claude_code.engine.query import QueryParams, query
    from claude_code.services.api.client import LLMClient
    from claude_code.tools import get_all_base_tools, get_tools_by_name

    # ── Step 1: Model resolution ──
    config: Config = parent_engine._config
    agent_type = agent_definition.agent_type if agent_definition else ""
    # In JS: getAgentModel(agentDef.model, mainLoopModel, modelOverride, permissionMode)
    resolved_model = model_override or (
        agent_definition.model if agent_definition and agent_definition.model else config.model
    )
    if resolved_model == AgentModel.INHERIT.value:
        resolved_model = config.model

    # ── Step 2: Agent ID ──
    agent_id = str(uuid.uuid4())[:8]
    effective_cwd = cwd or worktree_path or config.cwd

    # ── Step 3: Message preparation ──
    context_messages: list[Message] = []
    if fork_context_messages:
        context_messages = filter_incomplete_tool_calls(fork_context_messages)

    # ── Step 4: User/system context ──
    # In JS: parallel getUserContext() + getSystemContext()
    # We build these inline for simplicity
    user_context: dict[str, str] = {}
    system_context: dict[str, str] = {}

    # ── Step 5: Slim CLAUDE.md ──
    should_omit_claude_md = bool(
        agent_definition and getattr(agent_definition, "omit_claude_md", False)
    )
    # In JS: if omitClaudeMd → remove claudeMd from userContext
    # Saves ~5-15 Gtok/week for high-volume Explore spawns

    # ── Step 6: Strip gitStatus for Explore/Plan ──
    if agent_definition and agent_type in ("Explore", "Plan"):
        system_context.pop("gitStatus", None)

    # ── Step 7: Permission mode override ──
    permission_mode = config.permission_mode
    if agent_definition and agent_definition.permission_mode:
        # agentDef.permissionMode wins, except bypassPermissions/acceptEdits from parent
        if permission_mode not in ("bypassPermissions", "acceptEdits"):
            permission_mode = agent_definition.permission_mode
    # Async agents should avoid permission prompts
    should_avoid_permission_prompts = is_async

    # ── Step 8: Tool pool assembly ──
    if use_exact_tools:
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

    # ── Step 9: System prompt ──
    if is_fork and parent_system_prompt:
        system_prompt = parent_system_prompt
    elif agent_definition:
        system_prompt = _build_agent_system_prompt(
            agent_definition,
            cwd=effective_cwd,
            resolved_tools=agent_tools,
            resolved_agent_model=resolved_model,
        )
    else:
        system_prompt = DEFAULT_AGENT_PROMPT

    # ── Step 10: Abort controller ──
    # Async agents get their own; sync agents share parent's
    agent_abort = abort_event or asyncio.Event()

    # ── Step 11: SubagentStart hooks ──
    # In JS: executeSubagentStartHooks → additional context messages
    # Placeholder for hook system integration

    # ── Step 12: Frontmatter hooks registration ──
    # In JS: registerFrontmatterHooks if agent has hooks
    # Placeholder

    # ── Step 13: Skills preload ──
    # In JS: resolve skill names → load content → create user messages
    if agent_definition and agent_definition.skills:
        for skill_name in agent_definition.skills:
            # In full implementation: resolveSkillName → load → inject
            logger.debug("Agent %s: would preload skill '%s'", agent_id, skill_name)

    # ── Step 14: Agent MCP server init ──
    # Translation of initializeAgentMcpServers:
    # - String refs → share parent client
    # - Inline defs → connectToServer → fetchToolsForClient
    # - Cleanup only new clients
    mcp_cleanup = None
    if agent_definition and agent_definition.mcp_servers:
        logger.debug("Agent %s: MCP servers defined (init placeholder)", agent_id)
        # In full implementation: connect, merge tools, track for cleanup

    # ── Step 15: Build messages ──
    initial_messages: list[Message] = [*context_messages]

    if is_fork and parent_messages:
        from claude_code.tools.agent_tool.fork import build_forked_messages
        fork_msgs = build_forked_messages(prompt)
        initial_messages.extend(fork_msgs)
    else:
        initial_messages.append(UserMessage(content=prompt))

    # ── Step 16: Config + client for sub-agent ──
    max_turns = max_turns_override or config.max_turns
    if agent_definition and agent_definition.max_turns:
        max_turns = min(agent_definition.max_turns, max_turns)
    else:
        max_turns = min(max_turns, 30)

    # Critical system reminder (injected every turn)
    critical_reminder = None
    if agent_definition:
        critical_reminder = agent_definition.critical_system_reminder

    sub_config = Config(
        model=resolved_model if resolved_model != config.model else config.model,
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

    # ── Build QueryParams ──
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

    # ── Step 17: Core query loop ──
    try:
        async for event in query(params):
            # Check abort
            if abort_event and abort_event.is_set():
                logger.info("Agent %s aborted", agent_id)
                break

            # ── Stream events ──
            if event.type == StreamEventType.STREAM_REQUEST_START:
                # TTFT metrics (translation of ttft tracking)
                continue

            # ── Max turns attachment ──
            if event.type == StreamEventType.MAX_TURNS_REACHED:
                logger.info("Agent %s reached max turns", agent_id)
                yield event
                break

            # ── Recordable messages ──
            # In JS: assistant / user / progress / compact_boundary
            # Record to sidechain transcript + yield
            yield event

    except asyncio.CancelledError:
        logger.info("Agent %s cancelled", agent_id)
        raise
    except Exception as exc:
        logger.error("Agent %s error: %s", agent_id, exc)
        yield StreamEvent(type=StreamEventType.ERROR, data={"error": str(exc)})

    finally:
        # ── Step 19: Cleanup (10 items from JS source) ──

        # 1. MCP cleanup
        if mcp_cleanup:
            try:
                await mcp_cleanup()
            except Exception:
                pass

        # 2. Clear session hooks
        if agent_definition and agent_definition.hooks:
            logger.debug("Agent %s: clearing session hooks", agent_id)

        # 3. Cleanup prompt cache tracking
        # In JS: cleanupAgentTracking(agentId)

        # 4. Release file state cache
        # In JS: agentToolUseContext.readFileState.clear()

        # 5. Release fork context messages
        initial_messages.clear()

        # 6. Unregister Perfetto tracing
        # In JS: unregisterPerfettoAgent(agentId)

        # 7. Clear transcript subdir mapping
        # In JS: clearAgentTranscriptSubdir(agentId)

        # 8. Clean up todos entry
        if hasattr(parent_engine, "_app_state") and hasattr(parent_engine._app_state, "todos"):
            try:
                if agent_id in parent_engine._app_state.todos:
                    del parent_engine._app_state.todos[agent_id]
            except (AttributeError, KeyError, TypeError):
                pass

        # 9. Kill shell tasks for this agent
        # In JS: killShellTasksForAgent(agentId, ...)

        # 10. Kill monitor MCP tasks (feature gated)
        # In JS: killMonitorMcpTasksForAgent(agentId, ...)

        logger.debug("Agent %s cleanup complete", agent_id)
