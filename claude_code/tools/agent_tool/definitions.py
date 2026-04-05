"""Agent definitions — translation of tools/AgentTool/loadAgentsDir.ts.

Defines the ``AgentDefinition`` type hierarchy (built-in, custom, plugin)
and loading/parsing from Markdown frontmatter, JSON, and directory trees.

Priority order (later overrides earlier for the same ``agent_type``):
  built-in → plugin → userSettings → projectSettings → flagSettings → policySettings
"""

from __future__ import annotations

import logging
import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from pydantic import Field

from claude_code.data_types import AgentModel
from claude_code.pydantic_models import FrozenModel, MutableModel
from claude_code.tools.agent_tool.memory import AgentMemoryScope, load_agent_memory_prompt

logger = logging.getLogger(__name__)

__all__ = [
    "AgentColorName",
    "AgentDefinition",
    "AgentDefinitionsResult",
    "AgentSource",
    "BaseAgentDefinition",
    "BuiltInAgentDefinition",
    "CustomAgentDefinition",
    "IsolationMode",
    "PluginAgentDefinition",
    "clear_agent_definitions_cache",
    "filter_agents_by_mcp_requirements",
    "get_active_agents_from_list",
    "get_agent_definitions_with_overrides",
    "has_required_mcp_servers",
    "initialize_agent_memory_snapshots",
    "is_built_in_agent",
    "is_custom_agent",
    "is_plugin_agent",
    "parse_agent_from_json",
    "parse_agent_from_markdown",
    "parse_agents_from_json",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AgentSource(StrEnum):
    """Where the agent definition came from — priority order."""

    BUILT_IN = "built-in"
    PLUGIN = "plugin"
    USER_SETTINGS = "userSettings"
    PROJECT_SETTINGS = "projectSettings"
    FLAG_SETTINGS = "flagSettings"
    POLICY_SETTINGS = "policySettings"
    LOCAL_SETTINGS = "localSettings"


class IsolationMode(StrEnum):
    WORKTREE = "worktree"


class AgentColorName(StrEnum):
    """Supported agent UI colors."""

    BLUE = "blue"
    GREEN = "green"
    RED = "red"
    YELLOW = "yellow"
    PURPLE = "purple"
    CYAN = "cyan"
    ORANGE = "orange"
    PINK = "pink"


# Valid permission modes (mirrors TS PermissionMode)
PERMISSION_MODES = frozenset({
    "auto",
    "acceptEdits",
    "bypassPermissions",
    "plan",
    "bubble",
    "dontAsk",
})

# Valid effort levels
EFFORT_LEVELS = frozenset({"low", "medium", "high"})


# ---------------------------------------------------------------------------
# Agent definition models
# ---------------------------------------------------------------------------


class BaseAgentDefinition(MutableModel):
    """Common fields for all agent definitions.

    Mirrors ``BaseAgentDefinition`` from loadAgentsDir.ts.
    """

    agent_type: str
    when_to_use: str = ""
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    skills: list[str] | None = None
    mcp_servers: list[Any] | None = None
    hooks: dict[str, Any] | None = None
    color: str | None = None
    model: str | None = None
    effort: str | int | None = None
    permission_mode: str | None = None
    max_turns: int | None = None
    filename: str | None = None
    base_dir: str | None = None
    critical_system_reminder: str | None = None  # criticalSystemReminder_EXPERIMENTAL
    required_mcp_servers: list[str] | None = None
    background: bool = False
    initial_prompt: str | None = None
    memory: AgentMemoryScope | None = None
    isolation: IsolationMode | None = None
    pending_snapshot_update: bool = False
    omit_claude_md: bool = False
    source: AgentSource = AgentSource.BUILT_IN

    # Callable that returns the system prompt — set per subtype
    _get_system_prompt: Callable[..., str] | None = None

    def get_system_prompt(self, **kwargs: Any) -> str:
        """Return the system prompt for this agent."""
        if self._get_system_prompt is not None:
            return self._get_system_prompt(**kwargs)
        return ""


class BuiltInAgentDefinition(BaseAgentDefinition):
    """Built-in agents — dynamic prompts, no static systemPrompt field."""

    source: AgentSource = AgentSource.BUILT_IN
    base_dir: str | None = "built-in"
    callback: Callable[[], None] | None = None


class CustomAgentDefinition(BaseAgentDefinition):
    """Custom agents from user/project/policy settings."""

    pass


class PluginAgentDefinition(BaseAgentDefinition):
    """Plugin agents — from external plugin systems."""

    source: AgentSource = AgentSource.PLUGIN
    plugin: str = ""


# Union type
AgentDefinition = BuiltInAgentDefinition | CustomAgentDefinition | PluginAgentDefinition


# ---------------------------------------------------------------------------
# Type guards
# ---------------------------------------------------------------------------


def is_built_in_agent(agent: BaseAgentDefinition) -> bool:
    return agent.source is AgentSource.BUILT_IN


def is_custom_agent(agent: BaseAgentDefinition) -> bool:
    return agent.source not in (AgentSource.BUILT_IN, AgentSource.PLUGIN)


def is_plugin_agent(agent: BaseAgentDefinition) -> bool:
    return agent.source is AgentSource.PLUGIN


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class AgentDefinitionsResult(MutableModel):
    """Result of loading all agent definitions."""

    active_agents: list[BaseAgentDefinition] = Field(default_factory=list)
    all_agents: list[BaseAgentDefinition] = Field(default_factory=list)
    failed_files: list[dict[str, str]] | None = None
    allowed_agent_types: list[str] | None = None


# ---------------------------------------------------------------------------
# MCP requirement checking
# ---------------------------------------------------------------------------


def has_required_mcp_servers(
    agent: BaseAgentDefinition,
    available_servers: list[str],
) -> bool:
    """Check every required pattern has at least one matching server (case-insensitive)."""
    if not agent.required_mcp_servers:
        return True
    return all(
        any(server.lower().find(pattern.lower()) >= 0 for server in available_servers)
        for pattern in agent.required_mcp_servers
    )


def filter_agents_by_mcp_requirements(
    agents: list[BaseAgentDefinition],
    available_servers: list[str],
) -> list[BaseAgentDefinition]:
    return [a for a in agents if has_required_mcp_servers(a, available_servers)]


# ---------------------------------------------------------------------------
# Active agent resolution (priority-based override)
# ---------------------------------------------------------------------------

# Source priority order — later wins for same agentType
_SOURCE_PRIORITY: tuple[AgentSource, ...] = (
    AgentSource.BUILT_IN,
    AgentSource.PLUGIN,
    AgentSource.USER_SETTINGS,
    AgentSource.PROJECT_SETTINGS,
    AgentSource.LOCAL_SETTINGS,
    AgentSource.FLAG_SETTINGS,
    AgentSource.POLICY_SETTINGS,
)


def get_active_agents_from_list(
    all_agents: list[BaseAgentDefinition],
) -> list[BaseAgentDefinition]:
    """Deduplicate agents by type — higher-priority source wins."""
    groups: dict[AgentSource, list[BaseAgentDefinition]] = {s: [] for s in _SOURCE_PRIORITY}

    for agent in all_agents:
        group = groups.get(agent.source)
        if group is not None:
            group.append(agent)

    agent_map: dict[str, BaseAgentDefinition] = {}
    for source in _SOURCE_PRIORITY:
        for agent in groups[source]:
            agent_map[agent.agent_type] = agent

    return list(agent_map.values())


# ---------------------------------------------------------------------------
# Memory snapshot initialization
# ---------------------------------------------------------------------------


def initialize_agent_memory_snapshots(
    agents: list[CustomAgentDefinition],
    *,
    cwd: str = "",
) -> None:
    """Initialize memory snapshots for agents with ``memory == 'user'``.

    Translation of initializeAgentMemorySnapshots from loadAgentsDir.ts.
    """
    from claude_code.tools.agent_tool.agent_memory_snapshot import (
        check_agent_memory_snapshot,
        initialize_from_snapshot,
    )

    for agent in agents:
        if agent.memory != AgentMemoryScope.USER:
            continue
        try:
            action, ts = check_agent_memory_snapshot(
                agent.agent_type, agent.memory, cwd=cwd,
            )
            if action == "initialize" and ts:
                initialize_from_snapshot(agent.agent_type, agent.memory, ts, cwd=cwd)
            elif action == "prompt-update" and ts:
                agent.pending_snapshot_update = True
        except Exception as exc:
            logger.debug("Snapshot init failed for %s: %s", agent.agent_type, exc)


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown (avoids PyYAML dependency)."""
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    fm_text = match.group(1)
    body = content[match.end():]
    fm: dict[str, Any] = {}

    current_list_key: str | None = None
    current_list: list[str] = []

    for line in fm_text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if current_list_key:
                fm[current_list_key] = current_list
                current_list_key = None
                current_list = []
            continue

        if current_list_key and line.startswith("  ") and stripped.startswith("- "):
            item = stripped[2:].strip().strip("'\"")
            if item:
                current_list.append(item)
            continue
        elif current_list_key:
            fm[current_list_key] = current_list
            current_list_key = None
            current_list = []

        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()

            if not value:
                current_list_key = key
                current_list = []
                continue

            if value.startswith("[") and value.endswith("]"):
                items = [v.strip().strip("'\"") for v in value[1:-1].split(",")]
                fm[key] = [i for i in items if i]
            elif value.lower() == "true":
                fm[key] = True
            elif value.lower() == "false":
                fm[key] = False
            elif value.startswith('"') and value.endswith('"'):
                fm[key] = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                fm[key] = value[1:-1]
            else:
                fm[key] = value

    if current_list_key:
        fm[current_list_key] = current_list

    return fm, body


# ---------------------------------------------------------------------------
# Parse tools from frontmatter
# ---------------------------------------------------------------------------


def _parse_tools_from_frontmatter(raw: Any) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    if isinstance(raw, str):
        if raw == "*":
            return ["*"]
        return [t.strip() for t in raw.split(",") if t.strip()]
    return None


# ---------------------------------------------------------------------------
# Markdown agent parsing
# ---------------------------------------------------------------------------


def parse_agent_from_markdown(
    file_path: str,
    base_dir: str,
    frontmatter: dict[str, Any],
    content: str,
    source: AgentSource,
) -> CustomAgentDefinition | None:
    """Parse agent from markdown frontmatter + body.

    Translation of parseAgentFromMarkdown from loadAgentsDir.ts.
    """
    agent_type = frontmatter.get("name")
    when_to_use = frontmatter.get("description", "")

    if not agent_type or not isinstance(agent_type, str):
        return None
    if not when_to_use or not isinstance(when_to_use, str):
        logger.debug("Agent file %s missing 'description'", file_path)
        return None

    when_to_use = when_to_use.replace("\\n", "\n")

    # -- color --
    color_raw = frontmatter.get("color")
    color: str | None = None
    if isinstance(color_raw, str) and color_raw in [c.value for c in AgentColorName]:
        color = color_raw

    # -- model --
    model_raw = frontmatter.get("model")
    model: str | None = None
    if isinstance(model_raw, str) and model_raw.strip():
        trimmed = model_raw.strip()
        model = AgentModel.INHERIT.value if trimmed.lower() == AgentModel.INHERIT.value.lower() else trimmed

    # -- background --
    background_raw = frontmatter.get("background")
    background = background_raw is True or background_raw == "true"

    # -- memory --
    memory_raw = frontmatter.get("memory")
    memory: AgentMemoryScope | None = None
    if memory_raw and memory_raw in ("user", "project", "local"):
        memory = AgentMemoryScope(memory_raw)

    # -- isolation --
    isolation_raw = frontmatter.get("isolation")
    isolation: IsolationMode | None = None
    if isolation_raw == "worktree":
        isolation = IsolationMode.WORKTREE

    # -- effort --
    effort = frontmatter.get("effort")
    if isinstance(effort, str) and effort not in EFFORT_LEVELS:
        logger.warning("Agent %s: invalid effort '%s'", file_path, effort)
        effort = None
    elif isinstance(effort, int):
        pass
    elif effort is not None and not isinstance(effort, str):
        effort = None

    # -- permissionMode --
    pm_raw = frontmatter.get("permissionMode")
    permission_mode: str | None = None
    if pm_raw and pm_raw in PERMISSION_MODES:
        permission_mode = pm_raw

    # -- maxTurns --
    mt_raw = frontmatter.get("maxTurns")
    max_turns: int | None = None
    if isinstance(mt_raw, int) and mt_raw > 0:
        max_turns = mt_raw
    elif isinstance(mt_raw, str) and mt_raw.isdigit():
        max_turns = int(mt_raw) if int(mt_raw) > 0 else None

    filename = os.path.splitext(os.path.basename(file_path))[0]
    tools = _parse_tools_from_frontmatter(frontmatter.get("tools"))
    disallowed_tools = _parse_tools_from_frontmatter(frontmatter.get("disallowedTools"))

    # -- skills --
    skills_raw = frontmatter.get("skills")
    skills: list[str] | None = None
    if isinstance(skills_raw, list):
        skills = [str(s).strip() for s in skills_raw if str(s).strip()]
    elif isinstance(skills_raw, str):
        skills = [s.strip() for s in skills_raw.split(",") if s.strip()]

    # -- mcpServers --
    mcp_servers = frontmatter.get("mcpServers")
    if not isinstance(mcp_servers, list):
        mcp_servers = None

    # -- hooks --
    hooks = frontmatter.get("hooks") if isinstance(frontmatter.get("hooks"), dict) else None

    # -- initialPrompt --
    ip_raw = frontmatter.get("initialPrompt")
    initial_prompt = ip_raw if isinstance(ip_raw, str) and ip_raw.strip() else None

    # Auto-inject file tools for memory agents
    if memory and tools is not None:
        from claude_code.tools.tool_names import (
            FILE_EDIT_TOOL_NAME,
            FILE_READ_TOOL_NAME,
            FILE_WRITE_TOOL_NAME,
        )

        tool_set = set(tools)
        for t in (FILE_WRITE_TOOL_NAME, FILE_EDIT_TOOL_NAME, FILE_READ_TOOL_NAME):
            if t not in tool_set:
                tools.append(t)

    system_prompt = content.strip()

    def _make_prompt_fn(
        sp: str, mem: AgentMemoryScope | None, at: str,
    ) -> Callable[..., str]:
        def _fn(**_kwargs: Any) -> str:
            if mem:
                return sp + "\n\n" + load_agent_memory_prompt(at, mem)
            return sp
        return _fn

    agent = CustomAgentDefinition(
        agent_type=agent_type,
        when_to_use=when_to_use,
        tools=tools,
        disallowed_tools=disallowed_tools,
        skills=skills,
        initial_prompt=initial_prompt,
        mcp_servers=mcp_servers,
        hooks=hooks,
        source=source,
        filename=filename,
        base_dir=base_dir,
        color=color,
        model=model,
        effort=effort,
        permission_mode=permission_mode,
        max_turns=max_turns,
        background=background,
        memory=memory,
        isolation=isolation,
    )
    agent._get_system_prompt = _make_prompt_fn(system_prompt, memory, agent_type)

    return agent


# ---------------------------------------------------------------------------
# JSON agent parsing
# ---------------------------------------------------------------------------


def parse_agent_from_json(
    name: str,
    definition: dict[str, Any],
    source: AgentSource = AgentSource.FLAG_SETTINGS,
) -> CustomAgentDefinition | None:
    """Parse a single agent from JSON data."""
    try:
        description = definition.get("description", "")
        prompt = definition.get("prompt", "")
        if not description or not prompt:
            return None

        tools = _parse_tools_from_frontmatter(definition.get("tools"))
        disallowed_tools = _parse_tools_from_frontmatter(definition.get("disallowedTools"))
        memory_raw = definition.get("memory")
        memory = AgentMemoryScope(memory_raw) if memory_raw in ("user", "project", "local") else None

        if memory and tools is not None:
            from claude_code.tools.tool_names import (
                FILE_EDIT_TOOL_NAME,
                FILE_READ_TOOL_NAME,
                FILE_WRITE_TOOL_NAME,
            )

            tool_set = set(tools)
            for t in (FILE_WRITE_TOOL_NAME, FILE_EDIT_TOOL_NAME, FILE_READ_TOOL_NAME):
                if t not in tool_set:
                    tools.append(t)

        system_prompt = prompt

        def _make_prompt_fn(
            sp: str, mem: AgentMemoryScope | None, at: str,
        ) -> Callable[..., str]:
            def _fn(**_kwargs: Any) -> str:
                if mem:
                    return sp + "\n\n" + load_agent_memory_prompt(at, mem)
                return sp
            return _fn

        agent = CustomAgentDefinition(
            agent_type=name,
            when_to_use=description,
            tools=tools,
            disallowed_tools=disallowed_tools,
            source=source,
            model=definition.get("model"),
            effort=definition.get("effort"),
            permission_mode=definition.get("permissionMode"),
            max_turns=definition.get("maxTurns"),
            skills=definition.get("skills"),
            initial_prompt=definition.get("initialPrompt"),
            background=definition.get("background", False),
            memory=memory,
            isolation=(IsolationMode(definition["isolation"])
                       if definition.get("isolation") == "worktree" else None),
            mcp_servers=definition.get("mcpServers"),
            hooks=definition.get("hooks"),
        )
        agent._get_system_prompt = _make_prompt_fn(system_prompt, memory, name)
        return agent

    except Exception as exc:
        logger.warning("Error parsing agent '%s' from JSON: %s", name, exc)
        return None


def parse_agents_from_json(
    agents_json: dict[str, Any],
    source: AgentSource = AgentSource.FLAG_SETTINGS,
) -> list[AgentDefinition]:
    """Parse multiple agents from a JSON record."""
    results: list[AgentDefinition] = []
    for name, defn in agents_json.items():
        if not isinstance(defn, dict):
            continue
        agent = parse_agent_from_json(name, defn, source)
        if agent:
            results.append(agent)
    return results


# ---------------------------------------------------------------------------
# Directory loading
# ---------------------------------------------------------------------------


def load_agents_dir(directory: str | Path) -> list[CustomAgentDefinition]:
    """Load all markdown agent definitions from a directory."""
    directory = Path(directory)
    if not directory.is_dir():
        return []

    agents: list[CustomAgentDefinition] = []
    seen: set[str] = set()

    try:
        for entry in sorted(directory.iterdir()):
            if entry.is_file() and entry.suffix.lower() == ".md":
                name = entry.stem
                if name in seen:
                    continue
                try:
                    content = entry.read_text(encoding="utf-8")
                    fm, body = _parse_frontmatter(content)
                    agent = parse_agent_from_markdown(
                        str(entry), str(directory), fm, body,
                        AgentSource.USER_SETTINGS,
                    )
                    if agent:
                        agents.append(agent)
                        seen.add(agent.agent_type)
                except OSError as exc:
                    logger.warning("Failed to read agent file %s: %s", entry, exc)
    except OSError as exc:
        logger.warning("Failed to list agents dir %s: %s", directory, exc)

    return agents


# ---------------------------------------------------------------------------
# Cache for definitions
# ---------------------------------------------------------------------------

_definitions_cache: AgentDefinitionsResult | None = None


def clear_agent_definitions_cache() -> None:
    """Clear memoized agent definitions (call on settings change)."""
    global _definitions_cache
    _definitions_cache = None


def get_agent_definitions_with_overrides(cwd: str = "") -> AgentDefinitionsResult:
    """Load agent definitions from all standard locations.

    Priority (later overrides earlier):
      1. built-in agents
      2. user agents: ~/.claude/agents/
      3. project agents: .claude/agents/

    Results are cached (call ``clear_agent_definitions_cache()`` to reset).
    """
    global _definitions_cache
    if _definitions_cache is not None:
        return _definitions_cache

    from claude_code.tools.agent_tool.built_in import get_built_in_agents

    all_agents_list: list[BaseAgentDefinition] = []
    failed_files: list[dict[str, str]] = []

    # 1. Built-in
    try:
        all_agents_list.extend(get_built_in_agents())
    except Exception as exc:
        logger.error("Failed to load built-in agents: %s", exc)

    # 2. User agents
    user_dir = Path.home() / ".claude" / "agents"
    for agent in load_agents_dir(user_dir):
        agent.source = AgentSource.USER_SETTINGS
        all_agents_list.append(agent)

    # 3. Project agents (closest .claude/agents/ walking up from cwd)
    if cwd:
        current = Path(cwd)
        home = Path.home()
        while current != current.parent and current != home.parent:
            agents_dir = current / ".claude" / "agents"
            if agents_dir.is_dir():
                for agent in load_agents_dir(agents_dir):
                    agent.source = AgentSource.PROJECT_SETTINGS
                    all_agents_list.append(agent)
                break
            current = current.parent

    active_agents = get_active_agents_from_list(all_agents_list)

    # Initialize colours for active agents
    from claude_code.tools.agent_tool.agent_color_manager import (
        AGENT_COLORS,
        set_agent_color,
    )

    color_idx = 0
    for agent in active_agents:
        if agent.color:
            set_agent_color(agent.agent_type, agent.color)
        elif agent.agent_type != "general-purpose":
            set_agent_color(agent.agent_type, AGENT_COLORS[color_idx % len(AGENT_COLORS)].value)
            color_idx += 1

    # Initialize memory snapshots for custom agents with user-scope memory
    custom_agents = [a for a in active_agents if is_custom_agent(a) and isinstance(a, CustomAgentDefinition)]
    try:
        initialize_agent_memory_snapshots(custom_agents, cwd=cwd)
    except Exception as exc:
        logger.debug("Memory snapshot init failed: %s", exc)

    result = AgentDefinitionsResult(
        active_agents=active_agents,
        all_agents=all_agents_list,
        failed_files=failed_files if failed_files else None,
    )
    _definitions_cache = result
    return result
