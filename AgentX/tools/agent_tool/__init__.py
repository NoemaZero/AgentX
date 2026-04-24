"""Agent tool package — translation of tools/AgentTool/ directory.

Re-exports the primary symbols needed by the rest of the codebase.

Module map:
  constants            — AGENT_TOOL_NAME, ONE_SHOT_BUILTIN_AGENT_TYPES, etc.
  agent_color_manager  — per-agent UI colour management
  agent_display        — display utilities for CLI / interactive commands
  agent_memory_snapshot — snapshot sync for agent memory
  built_in             — 6 built-in agent definitions
  definitions          — AgentDefinition type hierarchy + loading/parsing
  fork                 — fork subagent experiment
  memory               — agent memory (user/project/local scopes)
  prompt               — prompt generation (fork-aware)
  resume               — background agent resumption
  run_agent            — core 19-step async generator
  tool                 — AgentTool(BaseTool) class
  utils                — tool filtering, result assembly, lifecycle driver
"""

from __future__ import annotations

# ── Primary tool class ──
from AgentX.tools.agent_tool.tool import AgentTool

# ── Constants ──
from AgentX.tools.agent_tool.constants import (
    AGENT_TOOL_NAME,
    LEGACY_AGENT_TOOL_NAME,
    ONE_SHOT_BUILTIN_AGENT_TYPES,
    VERIFICATION_AGENT_TYPE,
)

# ── Definitions ──
from AgentX.tools.agent_tool.definitions import (
    AgentColorName,
    AgentSource,
    BaseAgentDefinition,
    BuiltInAgentDefinition,
    CustomAgentDefinition,
    IsolationMode,
    PluginAgentDefinition,
    get_active_agents_from_list,
    get_agent_definitions_with_overrides,
    is_built_in_agent,
    is_custom_agent,
    is_plugin_agent,
    parse_agent_from_markdown,
)

# ── Built-in agents ──
from AgentX.tools.agent_tool.built_in import (
    GENERAL_PURPOSE_AGENT,
    get_built_in_agents,
)

# ── Fork ──
from AgentX.tools.agent_tool.fork import (
    FORK_AGENT,
    build_forked_messages,
    is_fork_subagent_enabled,
    is_in_fork_child,
)

# ── Core runner ──
from AgentX.tools.agent_tool.run_agent import run_agent

# ── Resume ──
from AgentX.tools.agent_tool.resume import (
    ResumeAgentResult,
    resume_agent_background,
)

# ── Utilities ──
from AgentX.tools.agent_tool.utils import (
    AgentToolResult,
    filter_tools_for_agent,
    finalize_agent_tool,
    resolve_agent_tools,
    run_async_agent_lifecycle,
)

# ── Display ──
from AgentX.tools.agent_tool.agent_display import (
    AGENT_SOURCE_GROUPS,
    compare_agents_by_name,
    resolve_agent_model_display,
    resolve_agent_overrides,
)

# ── Colour ──
from AgentX.tools.agent_tool.agent_color_manager import (
    get_agent_color,
    set_agent_color,
)

# ── Memory ──
from AgentX.tools.agent_tool.memory import (
    AgentMemoryScope,
    load_agent_memory_prompt,
)

# ── Memory Snapshot ──
from AgentX.tools.agent_tool.agent_memory_snapshot import (
    check_agent_memory_snapshot,
    initialize_from_snapshot,
)

__all__ = [
    # Tool class
    "AgentTool",
    # Constants
    "AGENT_TOOL_NAME",
    "LEGACY_AGENT_TOOL_NAME",
    "ONE_SHOT_BUILTIN_AGENT_TYPES",
    "VERIFICATION_AGENT_TYPE",
    # Definitions
    "AgentColorName",
    "AgentSource",
    "BaseAgentDefinition",
    "BuiltInAgentDefinition",
    "CustomAgentDefinition",
    "IsolationMode",
    "PluginAgentDefinition",
    "get_active_agents_from_list",
    "get_agent_definitions_with_overrides",
    "is_built_in_agent",
    "is_custom_agent",
    "is_plugin_agent",
    "parse_agent_from_markdown",
    # Built-in
    "GENERAL_PURPOSE_AGENT",
    "get_built_in_agents",
    # Fork
    "FORK_AGENT",
    "build_forked_messages",
    "is_fork_subagent_enabled",
    "is_in_fork_child",
    # Runner
    "run_agent",
    # Resume
    "ResumeAgentResult",
    "resume_agent_background",
    # Utils
    "AgentToolResult",
    "filter_tools_for_agent",
    "finalize_agent_tool",
    "resolve_agent_tools",
    "run_async_agent_lifecycle",
    # Display
    "AGENT_SOURCE_GROUPS",
    "compare_agents_by_name",
    "resolve_agent_model_display",
    "resolve_agent_overrides",
    # Colour
    "get_agent_color",
    "set_agent_color",
    # Memory
    "AgentMemoryScope",
    "load_agent_memory_prompt",
    # Memory Snapshot
    "check_agent_memory_snapshot",
    "initialize_from_snapshot",
]
