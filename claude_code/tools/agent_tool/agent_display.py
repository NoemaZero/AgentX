"""Agent display utilities — translation of tools/AgentTool/agentDisplay.ts.

Shared utilities for displaying agent information.
Used by both the CLI ``claude agents`` handler and the interactive ``/agents`` command.

Key exports:
  - ``AGENT_SOURCE_GROUPS`` — ordered display groups
  - ``resolve_agent_overrides()`` — annotate with override info + deduplicate
  - ``resolve_agent_model_display()`` — display string for agent model
  - ``get_override_source_label()`` — human-readable source label
  - ``compare_agents_by_name()`` — case-insensitive alphabetical sort
"""

from __future__ import annotations

from typing import Any, NamedTuple

from claude_code.tools.agent_tool.definitions import (
    AgentSource,
    BaseAgentDefinition,
)

__all__ = [
    "AGENT_SOURCE_GROUPS",
    "AgentSourceGroup",
    "ResolvedAgent",
    "compare_agents_by_name",
    "get_override_source_label",
    "resolve_agent_model_display",
    "resolve_agent_overrides",
]


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class AgentSourceGroup(NamedTuple):
    """An ordered display group for agents."""

    label: str
    source: str  # AgentSource value or 'built-in' / 'plugin'


class ResolvedAgent(NamedTuple):
    """Agent annotated with override information."""

    agent: BaseAgentDefinition
    overridden_by: str | None  # AgentSource value, or None if this is the winning def


# ---------------------------------------------------------------------------
# Ordered source groups — both CLI and interactive UI should use this
# Translation of AGENT_SOURCE_GROUPS constant
# ---------------------------------------------------------------------------

AGENT_SOURCE_GROUPS: list[AgentSourceGroup] = [
    AgentSourceGroup(label="User agents", source="userSettings"),
    AgentSourceGroup(label="Project agents", source="projectSettings"),
    AgentSourceGroup(label="Local agents", source="localSettings"),
    AgentSourceGroup(label="Managed agents", source="policySettings"),
    AgentSourceGroup(label="Plugin agents", source="plugin"),
    AgentSourceGroup(label="CLI arg agents", source="flagSettings"),
    AgentSourceGroup(label="Built-in agents", source="built-in"),
]


# ---------------------------------------------------------------------------
# resolve_agent_overrides — annotate + deduplicate
# ---------------------------------------------------------------------------


def resolve_agent_overrides(
    all_agents: list[BaseAgentDefinition],
    active_agents: list[BaseAgentDefinition],
) -> list[ResolvedAgent]:
    """Annotate agents with override information.

    An agent is "overridden" when another agent with the same type from a
    higher-priority source takes precedence.

    Also deduplicates by ``(agent_type, source)`` to handle git worktree
    duplicates where the same agent file is loaded from both the worktree
    and main repo.

    Translation of resolveAgentOverrides from agentDisplay.ts.
    """
    # Build lookup: agent_type → winning definition
    active_map: dict[str, BaseAgentDefinition] = {}
    for agent in active_agents:
        active_map[agent.agent_type] = agent

    seen: set[str] = set()
    resolved: list[ResolvedAgent] = []

    for agent in all_agents:
        key = f"{agent.agent_type}:{agent.source}"
        if key in seen:
            continue
        seen.add(key)

        active = active_map.get(agent.agent_type)
        overridden_by = (
            active.source if active and active.source != agent.source else None
        )
        resolved.append(ResolvedAgent(agent=agent, overridden_by=overridden_by))

    return resolved


# ---------------------------------------------------------------------------
# resolve_agent_model_display
# ---------------------------------------------------------------------------


def resolve_agent_model_display(agent: BaseAgentDefinition) -> str | None:
    """Resolve the display model string for an agent.

    Returns the model alias or ``'inherit'`` for display purposes.

    Translation of resolveAgentModelDisplay from agentDisplay.ts.
    """
    model = agent.model or _get_default_subagent_model()
    if not model:
        return None
    return "inherit" if model == "inherit" else model


def _get_default_subagent_model() -> str:
    """Return the default model used for subagents.

    Translation of getDefaultSubagentModel from utils/model/agent.ts.
    """
    try:
        from claude_code.config import Config

        return Config.default_model() if hasattr(Config, "default_model") else "sonnet"
    except (ImportError, Exception):
        return "sonnet"


# ---------------------------------------------------------------------------
# get_override_source_label
# ---------------------------------------------------------------------------

_SOURCE_DISPLAY_NAMES: dict[str, str] = {
    "userSettings": "user",
    "projectSettings": "project",
    "localSettings": "local",
    "policySettings": "managed",
    "flagSettings": "CLI",
    "plugin": "plugin",
    "built-in": "built-in",
}


def get_override_source_label(source: str) -> str:
    """Get a human-readable label for the source that overrides an agent.

    Returns lowercase, e.g. ``"user"``, ``"project"``, ``"managed"``.

    Translation of getOverrideSourceLabel from agentDisplay.ts.
    """
    return _SOURCE_DISPLAY_NAMES.get(source, source).lower()


# ---------------------------------------------------------------------------
# compare_agents_by_name
# ---------------------------------------------------------------------------


def compare_agents_by_name(
    a: BaseAgentDefinition,
    b: BaseAgentDefinition,
) -> int:
    """Compare agents alphabetically by name (case-insensitive).

    Returns negative if ``a < b``, zero if equal, positive if ``a > b``.

    Translation of compareAgentsByName from agentDisplay.ts.
    """
    a_lower = a.agent_type.lower()
    b_lower = b.agent_type.lower()
    if a_lower < b_lower:
        return -1
    if a_lower > b_lower:
        return 1
    return 0
