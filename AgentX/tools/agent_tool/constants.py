"""Constants — translation of tools/AgentTool/constants.ts."""

from __future__ import annotations

from AgentX.tools.tool_names import AGENT_TOOL_NAME, LEGACY_AGENT_TOOL_NAME

__all__ = [
    "AGENT_TOOL_NAME",
    "LEGACY_AGENT_TOOL_NAME",
    "ONE_SHOT_BUILTIN_AGENT_TYPES",
    "VERIFICATION_AGENT_TYPE",
]

VERIFICATION_AGENT_TYPE = "verification"

# Built-in agents that run once and return a report — the parent never
# SendMessages back to continue them. Skip the agentId/SendMessage/usage
# trailer for these to save tokens (~135 chars × 34M Explore runs/week).
ONE_SHOT_BUILTIN_AGENT_TYPES: frozenset[str] = frozenset({"Explore", "Plan"})
