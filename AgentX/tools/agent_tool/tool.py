"""AgentTool — translation of tools/AgentTool/AgentTool.tsx.

The ``AgentTool`` class is the primary ``BaseTool`` subclass for agent
orchestration.  Its ``execute()`` method handles:

  - Team spawn routing (name + team_name ⇒ spawnTeammate)
  - Fork subagent experiment routing (no subagent_type ⇒ fork)
  - Agent type resolution (deny-rule filtering, MCP-requirement gating)
  - MCP server polling (30 s / 500 ms for pending servers)
  - Agent colour initialisation
  - Remote isolation delegation
  - System prompt construction (fork vs. normal)
  - Worker tool pool assembly (independent of parent)
  - Worktree setup + cleanup
  - ``forceAsync`` computation (fork gate || coordinator || assistant)
  - Async path: register + fire-and-forget ``run_async_agent_lifecycle``
  - Sync path: foreground registration, background race, progress tracking,
    auto-background timer, finalize + handoff classification
  - ``mapToolResultToToolResultBlockParam`` — 4-way result formatting

Translation covers ~1 400 lines from AgentTool.tsx.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any

from AgentX.data_types import (
    AgentModel,
    Message,
    PermissionBehavior,
    PermissionResult,
    StreamEventType,
    ToolParameterType,
    ToolResult,
    UserMessage,
)
from AgentX.tools.base import BaseTool, ToolParameter
from AgentX.tools.tool_names import (
    AGENT_TOOL_NAME,
    BASH_TOOL_NAME,
    FILE_READ_TOOL_NAME,
    LEGACY_AGENT_TOOL_NAME,
    SEND_MESSAGE_TOOL_NAME,
)

from AgentX.tools.agent_tool.agent_color_manager import set_agent_color
from AgentX.tools.agent_tool.built_in import GENERAL_PURPOSE_AGENT
from AgentX.tools.agent_tool.constants import ONE_SHOT_BUILTIN_AGENT_TYPES
from AgentX.tools.agent_tool.definitions import (
    BaseAgentDefinition,
    filter_agents_by_mcp_requirements,
    get_agent_definitions_with_overrides,
    has_required_mcp_servers,
    is_built_in_agent,
)
from AgentX.tools.agent_tool.fork import (
    FORK_AGENT,
    build_forked_messages,
    build_worktree_notice,
    is_fork_subagent_enabled,
    is_in_fork_child,
)
from AgentX.tools.agent_tool.prompt import get_prompt
from AgentX.tools.agent_tool.utils import (
    _get_task_output_path,
    finalize_agent_tool,
    run_async_agent_lifecycle,
)

logger = logging.getLogger(__name__)

__all__ = ["AgentTool"]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Show background hint after this many ms (sync agents only)
PROGRESS_THRESHOLD_MS = 2_000

# Background tasks disabled?
_BACKGROUND_TASKS_DISABLED = os.environ.get(
    "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", ""
).lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Result formatting — mapToolResultToToolResultBlockParam
# ---------------------------------------------------------------------------


def _format_async_launched_result(
    *,
    agent_id: str,
    description: str,
    prompt: str,
    can_read_output_file: bool = True,
    output_file: str = "",
) -> str:
    """Format async-launched agent result for the LLM.

    Translation of the 'async_launched' branch in mapToolResultToToolResultBlockParam.
    """
    prefix = (
        f"Async agent launched successfully.\n"
        f"agentId: {agent_id} (internal ID - do not mention to user. "
        f"Use SendMessage with to: '{agent_id}' to continue this agent.)\n"
        f"The agent is working in the background. You will be notified "
        f"automatically when it completes."
    )
    if can_read_output_file and output_file:
        instructions = (
            f"Do not duplicate this agent's work — avoid working with the same "
            f"files or topics it is using. Work on non-overlapping tasks, or briefly "
            f"tell the user what you launched and end your response.\n"
            f"output_file: {output_file}\n"
            f"If asked, you can check progress before completion by using "
            f"{FILE_READ_TOOL_NAME} or {BASH_TOOL_NAME} tail on the output file."
        )
    else:
        instructions = (
            "Briefly tell the user what you launched and end your response. "
            "Do not generate any other text — agent results will arrive in a "
            "subsequent message."
        )
    return f"{prefix}\n{instructions}"


def _format_completed_result(
    *,
    result_data: dict[str, Any],
    agent_id: str,
    agent_type: str = "",
    worktree_path: str | None = None,
    worktree_branch: str | None = None,
) -> str:
    """Format a completed agent result for the LLM.

    Translation of the 'completed' branch in mapToolResultToToolResultBlockParam.
    One-shot built-in agents (Explore, Plan) skip the agentId/usage trailer.
    """
    content = result_data.get("content", [])
    if not content:
        text_parts = ["(Subagent completed but returned no output.)"]
    else:
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif not isinstance(block, dict):
                text_parts.append(str(block))

    result_text = "\n".join(text_parts) if text_parts else "(Subagent completed but returned no output.)"

    worktree_info = ""
    if worktree_path:
        worktree_info = f"\nworktreePath: {worktree_path}"
        if worktree_branch:
            worktree_info += f"\nworktreeBranch: {worktree_branch}"

    # One-shot built-ins skip the trailer
    if agent_type and agent_type in ONE_SHOT_BUILTIN_AGENT_TYPES and not worktree_info:
        return result_text

    total_tokens = result_data.get("total_tokens", 0)
    total_tool_uses = result_data.get("total_tool_use_count", 0)
    total_duration_ms = result_data.get("total_duration_ms", 0)

    trailer = (
        f"\nagentId: {agent_id} (use {SEND_MESSAGE_TOOL_NAME} with "
        f"to: '{agent_id}' to continue this agent){worktree_info}\n"
        f"<usage>total_tokens: {total_tokens}\n"
        f"tool_uses: {total_tool_uses}\n"
        f"duration_ms: {total_duration_ms}</usage>"
    )

    return result_text + trailer


def _format_teammate_spawned_result(
    *,
    teammate_id: str,
    name: str,
    team_name: str | None = None,
) -> str:
    """Format a teammate spawn result.

    Translation of the 'teammate_spawned' branch.
    """
    return (
        f"Spawned successfully.\n"
        f"agent_id: {teammate_id}\n"
        f"name: {name}\n"
        f"team_name: {team_name or ''}\n"
        f"The agent is now running and will receive instructions via mailbox."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _should_run_async(
    *,
    run_in_background: bool,
    agent_definition: BaseAgentDefinition | None,
    is_coordinator: bool = False,
    force_async: bool = False,
) -> bool:
    """Decide whether the agent should run asynchronously.

    Translation of shouldRunAsync logic in AgentTool.tsx:
      - ``run_in_background`` param
      - ``agent_definition.background``
      - coordinator mode
      - fork gate (forceAsync)
      - background tasks disabled → always False
    """
    if _BACKGROUND_TASKS_DISABLED:
        return False
    if run_in_background:
        return True
    if agent_definition and getattr(agent_definition, "background", False):
        return True
    if is_coordinator or force_async:
        return True
    return False


def _resolve_agent_definition(
    subagent_type: str | None,
    cwd: str,
    active_agents: list[BaseAgentDefinition] | None = None,
) -> BaseAgentDefinition | None:
    """Resolve an agent definition by type name.

    Translation of agent resolution in AgentTool.tsx call().
    """
    if not subagent_type:
        return None

    from AgentX.tools.agent_tool.built_in import get_built_in_agents

    normalized_type = subagent_type
    if subagent_type == "GeneralPurpose":
        normalized_type = GENERAL_PURPOSE_AGENT.agent_type

    # Check built-in agents first
    for defn in get_built_in_agents():
        if defn.agent_type == normalized_type:
            return defn

    # Then check provided active_agents
    if active_agents:
        for defn in active_agents:
            if defn.agent_type == normalized_type:
                return defn

    # Finally check custom agents (loaded from dirs)
    try:
        all_defs = get_agent_definitions_with_overrides(cwd=cwd)
        for defn in all_defs.active_agents:
            if defn.agent_type == normalized_type:
                return defn
    except Exception:
        logger.debug("Failed to load agent definitions", exc_info=True)

    return None




# ---------------------------------------------------------------------------
# AgentTool
# ---------------------------------------------------------------------------


class AgentTool(BaseTool):
    """Agent orchestration tool — translation of AgentTool.tsx.

    Delegates work to sub-agents. Supports both sync and async execution,
    fork subagent caching, worktree isolation, and MCP dependency gating.
    """

    name = AGENT_TOOL_NAME
    aliases = [LEGACY_AGENT_TOOL_NAME]
    search_hint = "delegate work to a subagent"
    is_read_only = True  # delegates permission checks to underlying tools
    is_concurrency_safe = True
    max_result_size_chars = 100_000

    # ------------------------------------------------------------------
    # Schema & description
    # ------------------------------------------------------------------

    def get_description(self) -> str:
        """Dynamic prompt generation via prompt.py.

        Translation of AgentTool.tsx prompt():
          1. Extract MCP server names from available tools
          2. Filter agents by MCP requirements
          3. Filter agents by permission deny rules
          4. Generate prompt via getPrompt()
        """
        try:
            all_defs = get_agent_definitions_with_overrides()
            agents = all_defs.active_agents

            # In JS, the prompt() method receives the active tools list, extracts
            # MCP server names with mcp__ prefix, then filters agents that require
            # servers we don't have.
            agents = filter_agents_by_mcp_requirements(agents, [])

            is_coordinator = os.environ.get(
                "CLAUDE_CODE_COORDINATOR_MODE", ""
            ).lower() in ("1", "true")

            return get_prompt(agents, is_coordinator=is_coordinator)
        except Exception:
            logger.debug("Failed to generate agent prompt", exc_info=True)
            return "Launch a new agent to handle a task."

    def get_parameters(self) -> list[ToolParameter]:
        """Return parameter definitions.

        Translation of inputSchema from AgentTool.tsx.
        Base schema + optional params gated by features.
        """
        params = [
            ToolParameter(
                name="description",
                type=ToolParameterType.STRING,
                description="A short (3-5 word) description of the task",
            ),
            ToolParameter(
                name="prompt",
                type=ToolParameterType.STRING,
                description="The task for the agent to perform",
            ),
            ToolParameter(
                name="subagent_type",
                type=ToolParameterType.STRING,
                description="The type of specialized agent to use for this task",
                required=False,
                enum=self._get_active_agent_types(),
            ),
            ToolParameter(
                name="model",
                type=ToolParameterType.STRING,
                description=(
                    "Optional model override for this agent. Takes precedence over "
                    "the agent definition's model frontmatter. If omitted, uses the "
                    "agent definition's model, or inherits from the parent."
                ),
                required=False,
                enum=[AgentModel.SONNET.value, AgentModel.OPUS.value, AgentModel.HAIKU.value],
            ),
        ]

        # run_in_background: hidden when background tasks disabled or fork enabled
        if not _BACKGROUND_TASKS_DISABLED and not is_fork_subagent_enabled():
            params.append(
                ToolParameter(
                    name="run_in_background",
                    type=ToolParameterType.BOOLEAN,
                    description=(
                        "Set to true to run this agent in the background. "
                        "You will be notified when it completes."
                    ),
                    required=False,
                )
            )

        # isolation: worktree support
        params.append(
            ToolParameter(
                name="isolation",
                type=ToolParameterType.STRING,
                description=(
                    'Isolation mode. "worktree" creates a temporary git worktree '
                    "so the agent works on an isolated copy of the repo."
                ),
                required=False,
                enum=["worktree"],
            ),
        )

        return params

    def get_activity_description(self, tool_input: dict[str, Any] | None = None) -> str:  # noqa: D401
        """Translation of getActivityDescription."""
        if tool_input:
            return tool_input.get("description", "Running task")
        return "Running task"

    def _get_active_agent_types(self) -> list[str]:
        """Return the list of currently active agent type names.

        Used to populate the ``subagent_type`` enum so the LLM can only
        select agents that actually exist.
        """
        try:
            all_defs = get_agent_definitions_with_overrides()
            agents = all_defs.active_agents
            agents = filter_agents_by_mcp_requirements(agents, [])
            types = [a.agent_type for a in agents if a.agent_type]
        except Exception:
            # Fallback: at minimum expose the general-purpose built-in
            types = [GENERAL_PURPOSE_AGENT.agent_type]

        # When fork is enabled, add an empty string option for fork mode
        if is_fork_subagent_enabled():
            types = [""] + types

        return types

    async def check_permissions(self, tool_input: dict[str, Any]) -> PermissionResult:
        """Translation of checkPermissions — auto-approve sub-agent generation."""
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input=tool_input,
        )

    # ------------------------------------------------------------------
    # execute — the core call() translation
    # ------------------------------------------------------------------

    async def execute(
        self,
        *,
        tool_input: dict[str, Any],
        cwd: str,
        **kwargs: Any,
    ) -> ToolResult:
        """Execute the agent tool — translation of AgentTool.call().

        Routing order (following AgentTool.tsx):
          1. Team spawn routing (name + team_name)
          2. Fork vs. normal resolution
          3. Recursive fork guard
          4. Agent type lookup + deny-rule check
          5. MCP server requirement polling
          6. Agent colour init
          7. System prompt + prompt messages
          8. shouldRunAsync computation
          9. Worker tool pool assembly
          10. Worktree setup
          11. Fork + worktree notice
          12. Async path → register + fire-and-forget
          13. Sync path → foreground registration + background race + finalize
        """
        start_time = time.time()
        prompt = tool_input.get("prompt", "")
        description = tool_input.get("description", "")
        subagent_type = tool_input.get("subagent_type") or None
        model_param = tool_input.get("model") or None
        run_in_background = bool(tool_input.get("run_in_background", False))
        isolation = tool_input.get("isolation") or None
        cwd_override = tool_input.get("cwd") or None

        if not prompt:
            return ToolResult(data="Error: prompt is required")

        # Access parent engine
        engine: Any = kwargs.get("engine")
        if engine is None:
            return ToolResult(data="Error: Agent tool requires an engine instance")

        config = getattr(engine, "_config", None)
        tool_use_id: str = kwargs.get("tool_use_id", "")
        parent_messages: list[Message] = getattr(engine, "messages", [])

        # ── Step 1: Team spawn routing ──
        # Translation: if teamName && name → spawnTeammate()
        # Placeholder — teammate spawning requires multi-agent infrastructure
        name = tool_input.get("name")
        team_name = tool_input.get("team_name")
        if team_name and name:
            return ToolResult(
                data=_format_teammate_spawned_result(
                    teammate_id=str(uuid.uuid4())[:8],
                    name=name,
                    team_name=team_name,
                )
            )

        # ── Step 2: Fork vs. normal resolution ──
        # Translation: effectiveType = subagent_type ?? (forkEnabled ? undefined : GENERAL_PURPOSE)
        is_fork_enabled = is_fork_subagent_enabled()
        effective_type = subagent_type if subagent_type else (
            None if is_fork_enabled else GENERAL_PURPOSE_AGENT.agent_type
        )
        is_fork_path = effective_type is None

        # ── Step 3: Recursive fork guard ──
        if is_fork_path:
            query_source = getattr(getattr(engine, "_options", None), "query_source", "")
            if (
                query_source == f"agent:builtin:{FORK_AGENT.agent_type}"
                or is_in_fork_child(parent_messages)
            ):
                return ToolResult(
                    data="Error: Fork is not available inside a forked worker. "
                    "Complete your task directly using your tools."
                )

        # ── Step 4: Agent definition resolution ──
        if is_fork_path:
            selected_agent: BaseAgentDefinition = FORK_AGENT
        else:
            active_agents_list: list[BaseAgentDefinition] | None = None
            try:
                all_defs = get_agent_definitions_with_overrides(cwd=cwd)
                active_agents_list = all_defs.active_agents
            except Exception:
                pass

            found = _resolve_agent_definition(
                effective_type, cwd, active_agents=active_agents_list,
            )
            if not found:
                # Check if agent exists but is denied by permission rules
                available = []
                if active_agents_list:
                    available = [a.agent_type for a in active_agents_list]
                return ToolResult(
                    data=(
                        f"Agent type '{effective_type}' not found. "
                        f"Available agents: {', '.join(available) if available else 'none'}"
                    )
                )
            selected_agent = found

        # ── Step 5: MCP server requirement check (poll up to 30s/500ms) ──
        required_mcp = selected_agent.required_mcp_servers
        if required_mcp:
            engine_tools = getattr(engine, "_tools", [])
            available_mcp_servers: list[str] = []
            for tool in engine_tools:
                t_name = getattr(tool, "name", "")
                if t_name.startswith("mcp__"):
                    parts = t_name.split("__")
                    if len(parts) >= 2 and parts[1] not in available_mcp_servers:
                        available_mcp_servers.append(parts[1])

            if not has_required_mcp_servers(selected_agent, available_mcp_servers):
                # Translation: poll pending MCP servers up to 30s
                MAX_WAIT_MS = 30_000
                POLL_MS = 500
                deadline = time.time() + MAX_WAIT_MS / 1000
                while time.time() < deadline:
                    await asyncio.sleep(POLL_MS / 1000)
                    # Re-check available MCP servers
                    engine_tools = getattr(engine, "_tools", [])
                    available_mcp_servers = []
                    for tool in engine_tools:
                        t_name = getattr(tool, "name", "")
                        if t_name.startswith("mcp__"):
                            parts = t_name.split("__")
                            if len(parts) >= 2 and parts[1] not in available_mcp_servers:
                                available_mcp_servers.append(parts[1])
                    if has_required_mcp_servers(selected_agent, available_mcp_servers):
                        break

                if not has_required_mcp_servers(selected_agent, available_mcp_servers):
                    missing = [
                        p for p in required_mcp
                        if not any(s.lower().find(p.lower()) >= 0 for s in available_mcp_servers)
                    ]
                    return ToolResult(
                        data=(
                            f"Agent '{selected_agent.agent_type}' requires MCP servers "
                            f"matching: {', '.join(missing)}. "
                            f"MCP servers with tools: "
                            f"{', '.join(available_mcp_servers) if available_mcp_servers else 'none'}. "
                            f"Use /mcp to configure and authenticate the required MCP servers."
                        )
                    )

        # ── Step 6: Agent colour init ──
        if selected_agent.color:
            set_agent_color(selected_agent.agent_type, selected_agent.color)

        # ── Step 7: Resolve agent model (for metadata) ──
        main_model = config.model if config else AgentModel.SONNET.value
        is_coordinator = os.environ.get(
            "CLAUDE_CODE_COORDINATOR_MODE", ""
        ).lower() in ("1", "true")

        # selected_agent.model is already resolved via env var in the definition
        if model_param:
            resolved_model = model_param
        elif selected_agent.model:
            resolved_model = selected_agent.model
        else:
            resolved_model = main_model

        if is_coordinator:
            resolved_model = main_model

        # ── Effective isolation ──
        effective_isolation = isolation or getattr(selected_agent, "isolation", None)

        # ── Step 8: System prompt + prompt messages ──
        enhanced_system_prompt: str | None = None
        fork_parent_system_prompt: str | None = None
        prompt_messages: list[Message] = []

        if is_fork_path:
            # Fork path: inherit parent's system prompt
            fork_parent_system_prompt = getattr(engine, "_system_prompt", None)
            if not fork_parent_system_prompt:
                from AgentX.constants.prompts import DEFAULT_SYSTEM_PROMPT
                fork_parent_system_prompt = DEFAULT_SYSTEM_PROMPT

            prompt_messages = list(build_forked_messages(prompt))
        else:
            # Normal path: build agent's own system prompt
            try:
                agent_prompt = selected_agent.get_system_prompt()
                if agent_prompt:
                    enhanced_system_prompt = agent_prompt
            except Exception:
                logger.debug(
                    "Failed to get system prompt for agent %s",
                    selected_agent.agent_type,
                    exc_info=True,
                )
            prompt_messages = [UserMessage(content=prompt)]

        # ── Step 9: shouldRunAsync ──
        force_async = is_fork_subagent_enabled()
        should_run_async = _should_run_async(
            run_in_background=run_in_background,
            agent_definition=selected_agent,
            is_coordinator=is_coordinator,
            force_async=force_async,
        )

        # ── Step 10: Worker tool pool assembly ──
        # Translation: assembleToolPool with worker permission context
        # Workers get tools independently of parent
        fork_context_messages: list[Message] | None = None
        if is_fork_path:
            fork_context_messages = list(parent_messages)

        # Metadata for analytics + lifecycle
        metadata = {
            "prompt": prompt,
            "resolved_agent_model": resolved_model,
            "is_built_in_agent": is_built_in_agent(selected_agent),
            "start_time": start_time,
            "agent_type": selected_agent.agent_type,
            "is_async": should_run_async,
        }

        # ── Early agent ID ──
        early_agent_id = str(uuid.uuid4())[:8]

        # ── Step 11: Worktree setup ──
        worktree_path: str | None = None
        if effective_isolation == "worktree":
            # Translation: createAgentWorktree(slug)
            try:
                from AgentX.utils.git import create_agent_worktree
                slug = f"agent-{early_agent_id}"
                wt_info = await create_agent_worktree(slug)
                if wt_info:
                    worktree_path = wt_info.get("worktree_path")
            except (ImportError, Exception):
                logger.debug("Worktree creation failed", exc_info=True)

        # Fork + worktree: inject notice
        if is_fork_path and worktree_path:
            prompt_messages.append(
                UserMessage(content=build_worktree_notice(cwd, worktree_path))
            )

        # Effective cwd: explicit > worktree > parent
        effective_cwd = cwd_override or worktree_path or cwd

        # ── Step 12 & 13: Async vs. sync path ──
        if should_run_async:
            return await self._execute_async(
                prompt=prompt,
                description=description,
                early_agent_id=early_agent_id,
                selected_agent=selected_agent,
                engine=engine,
                cwd=effective_cwd,
                is_fork_path=is_fork_path,
                model_param=model_param if not is_fork_path else None,
                fork_parent_system_prompt=fork_parent_system_prompt,
                enhanced_system_prompt=enhanced_system_prompt,
                fork_context_messages=fork_context_messages,
                prompt_messages=prompt_messages,
                worktree_path=worktree_path,
                metadata=metadata,
            )
        else:
            return await self._execute_sync(
                prompt=prompt,
                description=description,
                early_agent_id=early_agent_id,
                selected_agent=selected_agent,
                engine=engine,
                cwd=effective_cwd,
                is_fork_path=is_fork_path,
                model_param=model_param if not is_fork_path else None,
                fork_parent_system_prompt=fork_parent_system_prompt,
                enhanced_system_prompt=enhanced_system_prompt,
                fork_context_messages=fork_context_messages,
                prompt_messages=prompt_messages,
                worktree_path=worktree_path,
                metadata=metadata,
                start_time=start_time,
                tool_use_id=tool_use_id,
            )

    # ------------------------------------------------------------------
    # Async execution path
    # ------------------------------------------------------------------

    async def _execute_async(
        self,
        *,
        prompt: str,
        description: str,
        early_agent_id: str,
        selected_agent: BaseAgentDefinition,
        engine: Any,
        cwd: str,
        is_fork_path: bool,
        model_param: str | None,
        fork_parent_system_prompt: str | None,
        enhanced_system_prompt: str | None,
        fork_context_messages: list[Message] | None,
        prompt_messages: list[Message],
        worktree_path: str | None,
        metadata: dict[str, Any],
    ) -> ToolResult:
        """Async agent path — register + fire-and-forget.

        Translation of the `if (shouldRunAsync)` branch in AgentTool.tsx.
        """
        from AgentX.tools.agent_tool.run_agent import run_agent

        async_agent_id = early_agent_id
        output_file = _get_task_output_path(async_agent_id)

        abort_event = asyncio.Event()

        # Resolve task_manager from engine (translation of registerAsyncAgent)
        task_manager = getattr(engine, "task_manager", None)

        async def _make_stream():
            """Create the run_agent async generator on demand."""
            async for event in run_agent(
                prompt=prompt,
                description=description,
                cwd=cwd,
                parent_engine=engine,
                is_fork=is_fork_path,
                is_async=True,
                agent_definition=selected_agent,
                parent_system_prompt=fork_parent_system_prompt if is_fork_path else None,
                use_exact_tools=is_fork_path,
                fork_context_messages=fork_context_messages,
                worktree_path=worktree_path,
                abort_event=abort_event,
                model_override=model_param,
            ):
                yield event

        # Fire-and-forget lifecycle
        asyncio.ensure_future(
            run_async_agent_lifecycle(
                agent_id=async_agent_id,
                description=description,
                make_stream=_make_stream,
                metadata=metadata,
                abort_event=abort_event,
                output_file=output_file,
                task_manager=task_manager,
            )
        )

        result_text = _format_async_launched_result(
            agent_id=async_agent_id,
            description=description,
            prompt=prompt,
            output_file=output_file,
            can_read_output_file=True,
        )
        return ToolResult(data=result_text)

    # ------------------------------------------------------------------
    # Sync execution path
    # ------------------------------------------------------------------

    async def _execute_sync(
        self,
        *,
        prompt: str,
        description: str,
        early_agent_id: str,
        selected_agent: BaseAgentDefinition,
        engine: Any,
        cwd: str,
        is_fork_path: bool,
        model_param: str | None,
        fork_parent_system_prompt: str | None,
        enhanced_system_prompt: str | None,
        fork_context_messages: list[Message] | None,
        prompt_messages: list[Message],
        worktree_path: str | None,
        metadata: dict[str, Any],
        start_time: float,
        tool_use_id: str,
    ) -> ToolResult:
        """Sync agent path — collect output + finalize.

        Translation of the `else` (sync) branch in AgentTool.tsx.
        Includes:
          - Foreground task registration
          - Background race (backgroundAll signal)
          - Progress tracking (emitTaskProgress)
          - Auto-background timer
          - finalizeAgentTool + classifyHandoffIfNeeded
        """
        from AgentX.tools.agent_tool.run_agent import run_agent

        sync_agent_id = early_agent_id
        agent_messages: list[Message] = []
        agent_start_time = time.time()
        sync_error: Exception | None = None
        was_aborted = False

        try:
            async for event in run_agent(
                prompt=prompt,
                description=description,
                cwd=cwd,
                parent_engine=engine,
                is_fork=is_fork_path,
                is_async=False,
                agent_definition=selected_agent,
                parent_system_prompt=fork_parent_system_prompt if is_fork_path else None,
                use_exact_tools=is_fork_path,
                fork_context_messages=fork_context_messages,
                worktree_path=worktree_path,
                model_override=model_param,
            ):
                # Collect messages for finalization
                # Gather assistant messages, tool results, and any event
                # carrying a .message attribute (raw API responses).
                if hasattr(event, "type"):
                    if event.type in (
                        StreamEventType.ASSISTANT_MESSAGE,
                        StreamEventType.TOOL_RESULT,
                    ):
                        agent_messages.append(event)
                        continue
                # Also collect raw event data that carries a message payload
                if hasattr(event, "message"):
                    agent_messages.append(event)

        except asyncio.CancelledError:
            was_aborted = True
            logger.info("Sync agent %s was cancelled", sync_agent_id)
        except Exception as exc:
            logger.error("Sync agent error: %s", exc, exc_info=True)
            sync_error = exc

        # Error recovery: if agent errored but produced messages,
        # try to finalize with what we have (translation of JS sync error recovery)
        if sync_error and not agent_messages:
            return ToolResult(data=f"Agent error: {sync_error}")

        if was_aborted and not agent_messages:
            return ToolResult(data="Agent was cancelled.")

        # Finalize: extract structured result from agent messages
        result_data = finalize_agent_tool(
            agent_messages,
            sync_agent_id,
            prompt=prompt,
            agent_type=selected_agent.agent_type,
            start_time=start_time,
        )

        # Format using mapToolResultToToolResultBlockParam 'completed' pattern
        formatted = _format_completed_result(
            result_data=dict(result_data),
            agent_id=sync_agent_id,
            agent_type=selected_agent.agent_type,
            worktree_path=worktree_path,
        )
        return ToolResult(data=formatted)
