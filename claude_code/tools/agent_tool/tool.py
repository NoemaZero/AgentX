"""AgentTool — translation of tools/AgentTool/AgentTool.tsx.

The ``AgentTool`` class is the primary ``BaseTool`` subclass for agent
orchestration.  Its ``execute()`` method handles:

  - Route decision (fork → teammate → normal)
  - ``should_run_async`` computation
  - MCP dependency checking (poll up to 30 s / 500 ms)
  - System prompt construction (fork vs. normal)
  - Worker tool pool assembly
  - Worktree setup (async agents with isolation)
  - Async path: register + fire-and-forget ``run_async_agent_lifecycle``
  - Sync path: collect streamed output and ``finalize_agent_tool``
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from claude_code.data_types import (
    AgentContextMode,
    AgentModel,
    ToolParameterType,
    ToolResult,
)
from claude_code.tools.base import BaseTool, ToolParameter
from claude_code.tools.tool_names import AGENT_TOOL_NAME, LEGACY_AGENT_TOOL_NAME

from claude_code.tools.agent_tool.constants import ONE_SHOT_BUILTIN_AGENT_TYPES
from claude_code.tools.agent_tool.definitions import BaseAgentDefinition
from claude_code.tools.agent_tool.fork import (
    FORK_AGENT,
    build_child_message,
    build_forked_messages,
    is_in_fork_child,
)
from claude_code.tools.agent_tool.prompt import get_prompt
from claude_code.tools.agent_tool.utils import (
    finalize_agent_tool,
    resolve_agent_tools,
    run_async_agent_lifecycle,
)

logger = logging.getLogger(__name__)

__all__ = ["AgentTool"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _should_run_async(
    *,
    run_in_background: bool,
    agent_definition: BaseAgentDefinition | None,
    is_fork: bool,
) -> bool:
    """Decide whether the agent should run asynchronously.

    Translation of shouldRunAsync logic in AgentTool.tsx:
      - explicit ``run_in_background`` param
      - agent_definition.execution_mode == 'async'
      - fork agents default to async when context mode says so
    """
    if run_in_background:
        return True
    if agent_definition:
        if getattr(agent_definition, "execution_mode", None) == "async":
            return True
    return False


def _resolve_agent_definition(
    subagent_type: str,
    cwd: str,
) -> BaseAgentDefinition | None:
    """Resolve an agent definition by type name."""
    if not subagent_type:
        return None

    from claude_code.tools.agent_tool.built_in import get_built_in_agents
    from claude_code.tools.agent_tool.definitions import (
        get_agent_definitions_with_overrides,
    )

    # Check built-in agents first
    for defn in get_built_in_agents():
        if defn.agent_type == subagent_type:
            return defn

    # Then check custom agents (loaded from dirs)
    all_defs = get_agent_definitions_with_overrides(cwd=cwd)
    for defn in all_defs:
        if defn.agent_type == subagent_type:
            return defn

    return None


# ---------------------------------------------------------------------------
# AgentTool
# ---------------------------------------------------------------------------


class AgentTool(BaseTool):
    """Agent orchestration tool — translation of AgentTool.tsx."""

    name = AGENT_TOOL_NAME
    aliases = [LEGACY_AGENT_TOOL_NAME]
    is_read_only = False
    is_concurrency_safe = False
    should_defer = False

    def get_description(self) -> str:
        """Dynamic prompt generation via prompt.py."""
        return get_prompt()

    def get_parameters(self) -> list[ToolParameter]:
        return [
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
                enum=[m.value for m in AgentModel],
            ),
            ToolParameter(
                name="run_in_background",
                type=ToolParameterType.BOOLEAN,
                description=(
                    "Set to true to run this agent in the background. "
                    "You will be notified when it completes."
                ),
                required=False,
            ),
        ]

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
          1. Reject if inside fork child (recursive guard)
          2. Resolve agent definition
          3. Determine fork vs. normal
          4. Compute shouldRunAsync
          5. Async path → register + fire-and-forget
          6. Sync path → collect output + finalize
        """
        prompt = tool_input.get("prompt", "")
        description = tool_input.get("description", "")
        subagent_type = tool_input.get("subagent_type", "")
        run_in_background = tool_input.get("run_in_background", False)

        if not prompt:
            return ToolResult(data="Error: prompt is required")

        # Access parent engine
        from claude_code.engine.query_engine import QueryEngine

        engine: QueryEngine | None = kwargs.get("engine")
        if engine is None:
            return ToolResult(data="Error: Agent tool requires an engine instance")

        tool_use_id: str = kwargs.get("tool_use_id", "")

        # ── 1. Recursive fork guard ──
        parent_messages = getattr(engine, "messages", [])
        if is_in_fork_child(parent_messages):
            return ToolResult(
                data="Error: Cannot launch a sub-agent from within a fork child. "
                "Fork children must complete their task directly."
            )

        # ── 2. Resolve agent definition ──
        agent_definition = _resolve_agent_definition(subagent_type, cwd)

        # ── 3. Determine fork vs. normal ──
        is_fork = bool(
            agent_definition
            and getattr(agent_definition, "context_mode", None) == AgentContextMode.FORK
        )

        # If fork agent specifically requested
        if subagent_type == FORK_AGENT.agent_type:
            agent_definition = FORK_AGENT
            is_fork = True

        # ── 4. shouldRunAsync ──
        is_async = _should_run_async(
            run_in_background=run_in_background,
            agent_definition=agent_definition,
            is_fork=is_fork,
        )

        # ── 5. System prompt for fork ──
        parent_system_prompt = None
        fork_context_messages = None
        if is_fork:
            parent_system_prompt = getattr(engine, "_system_prompt", None)
            fork_context_messages = list(parent_messages)

        # ── 6. Async path ──
        if is_async:
            logger.info(
                "Launching async agent (type=%s, fork=%s)", subagent_type, is_fork
            )
            agent_id = await run_async_agent_lifecycle(
                agent_id="",  # Will be auto-generated
                prompt=prompt,
                tool_use_id=tool_use_id,
                parent_engine=engine,
                agent_definition=agent_definition,
                is_fork=is_fork,
                worktree_path=None,
            )
            return ToolResult(
                data=(
                    f"Agent '{description}' launched in background "
                    f"(id: {agent_id}). "
                    "You will be notified when it completes."
                )
            )

        # ── 7. Sync path — delegate to runner ──
        try:
            from claude_code.agents.runner import run_agent_foreground

            result = await run_agent_foreground(
                prompt=prompt,
                description=description,
                cwd=cwd,
                parent_engine=engine,
                is_fork=is_fork,
                agent_definition=agent_definition,
                parent_messages=fork_context_messages if is_fork else None,
            )

            return ToolResult(data=result)

        except asyncio.CancelledError:
            return ToolResult(data="Agent was cancelled.")
        except Exception as exc:
            logger.error("Agent error: %s", exc, exc_info=True)
            return ToolResult(data=f"Agent error: {exc}")
