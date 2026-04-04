"""Agent color manager — translation of tools/AgentTool/agentColorManager.ts.

Manages the assignment of UI colors to agent types.  Each non-general-purpose
agent is assigned a unique color from a rotating palette.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

__all__ = [
    "AGENT_COLORS",
    "AGENT_COLOR_TO_THEME_COLOR",
    "AgentColorName",
    "get_agent_color",
    "set_agent_color",
]


# ---------------------------------------------------------------------------
# Color type — 8 supported agent UI colours
# ---------------------------------------------------------------------------


class AgentColorName(StrEnum):
    """Supported agent UI colors (matches TS AgentColorName)."""

    RED = "red"
    BLUE = "blue"
    GREEN = "green"
    YELLOW = "yellow"
    PURPLE = "purple"
    ORANGE = "orange"
    PINK = "pink"
    CYAN = "cyan"


# Ordered palette — used for round-robin assignment
AGENT_COLORS: Final[tuple[AgentColorName, ...]] = (
    AgentColorName.RED,
    AgentColorName.BLUE,
    AgentColorName.GREEN,
    AgentColorName.YELLOW,
    AgentColorName.PURPLE,
    AgentColorName.ORANGE,
    AgentColorName.PINK,
    AgentColorName.CYAN,
)

# Mapping from color name → theme color key (suffixed for agent-only usage)
AGENT_COLOR_TO_THEME_COLOR: Final[dict[AgentColorName, str]] = {
    AgentColorName.RED: "red_FOR_SUBAGENTS_ONLY",
    AgentColorName.BLUE: "blue_FOR_SUBAGENTS_ONLY",
    AgentColorName.GREEN: "green_FOR_SUBAGENTS_ONLY",
    AgentColorName.YELLOW: "yellow_FOR_SUBAGENTS_ONLY",
    AgentColorName.PURPLE: "purple_FOR_SUBAGENTS_ONLY",
    AgentColorName.ORANGE: "orange_FOR_SUBAGENTS_ONLY",
    AgentColorName.PINK: "pink_FOR_SUBAGENTS_ONLY",
    AgentColorName.CYAN: "cyan_FOR_SUBAGENTS_ONLY",
}

# Global mutable map — agent_type → assigned colour
_agent_color_map: dict[str, AgentColorName] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_agent_color(agent_type: str) -> str | None:
    """Return the theme-color key for *agent_type*, or ``None``.

    The ``general-purpose`` agent intentionally has no colour.
    """
    if agent_type == "general-purpose":
        return None
    color = _agent_color_map.get(agent_type)
    if color is not None and color in AGENT_COLORS:
        return AGENT_COLOR_TO_THEME_COLOR[color]
    return None


def set_agent_color(agent_type: str, color: AgentColorName | str | None) -> None:
    """Register (or remove) the colour for *agent_type*.

    *color* is validated against ``AGENT_COLORS``; invalid values are ignored.
    """
    if not color:
        _agent_color_map.pop(agent_type, None)
        return
    try:
        validated = AgentColorName(color)
    except ValueError:
        return  # silently ignore invalid colours
    if validated in AGENT_COLORS:
        _agent_color_map[agent_type] = validated


def get_agent_color_map() -> dict[str, AgentColorName]:
    """Read-only access to the global colour map (for testing / inspection)."""
    return dict(_agent_color_map)


def clear_agent_color_map() -> None:
    """Reset colour assignments (useful in tests)."""
    _agent_color_map.clear()
