"""Agent definitions — translation of tools/AgentTool/loadAgentsDir.ts.

Defines the AgentDefinition type hierarchy (built-in, custom, plugin)
and loading from Markdown / JSON / directories.
"""

from __future__ import annotations

import logging
import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from pydantic import ConfigDict, Field

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
    "filter_agents_by_mcp_requirements",
    "get_active_agents_from_list",
    "get_agent_definitions_with_overrides",
    "has_required_mcp_servers",
    "is_built_in_agent",
    "is_custom_agent",
    "parse_agent_from_markdown",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AgentSource(StrEnum):
    """Where the agent definition came from — priority order (later overrides)."""

    BUILT_IN = "built-in"
    PLUGIN = "plugin"
    USER_SETTINGS = "userSettings"
    PROJECT_SETTINGS = "projectSettings"
    FLAG_SETTINGS = "flagSettings"
    POLICY_SETTINGS = "policySettings"


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


# Valid permission modes (mirrors TS PermissionMode)
PERMISSION_MODES = frozenset({
    "auto",
    "acceptEdits",
    "bypassPermissions",
    "plan",
    "bubble",
})

# Valid effort levels
EFFORT_LEVELS = frozenset({"low", "medium", "high"})


# ---------------------------------------------------------------------------
# Agent definition models
# ---------------------------------------------------------------------------


class BaseAgentDefinition(MutableModel):
    """Common fields for all agent definitions."""

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
    required_mcp_servers: list[str] | None = None
    background: bool = False
    initial_prompt: str | None = None
    memory: AgentMemoryScope | None = None
    isolation: IsolationMode | None = None
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
    """Custom agents from user/project/policy settings — prompt stored via closure."""

    pass


class PluginAgentDefinition(BaseAgentDefinition):
    """Plugin agents — similar to custom but with plugin metadata."""

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


def has_required_mcp_servers(agent: BaseAgentDefinition, available_servers: list[str]) -> bool:
    """Check if an agent's required MCP servers are available."""
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
    """Filter agents by MCP server requirement availability."""
    return [a for a in agents if has_required_mcp_servers(a, available_servers)]


# ---------------------------------------------------------------------------
# Active agent resolution (priority-based override)
# ---------------------------------------------------------------------------


def get_active_agents_from_list(all_agents: list[BaseAgentDefinition]) -> list[BaseAgentDefinition]:
    """Get active agents by applying priority-based overrides.

    Later sources override earlier ones (same agentType):
    built-in → plugin → userSettings → projectSettings → flagSettings → policySettings
    """
    groups: dict[AgentSource, list[BaseAgentDefinition]] = {
        AgentSource.BUILT_IN: [],
        AgentSource.PLUGIN: [],
        AgentSource.USER_SETTINGS: [],
        AgentSource.PROJECT_SETTINGS: [],
        AgentSource.FLAG_SETTINGS: [],
        AgentSource.POLICY_SETTINGS: [],
    }
    for agent in all_agents:
        group = groups.get(agent.source)
        if group is not None:
            group.append(agent)

    agent_map: dict[str, BaseAgentDefinition] = {}
    for source in (
        AgentSource.BUILT_IN,
        AgentSource.PLUGIN,
        AgentSource.USER_SETTINGS,
        AgentSource.PROJECT_SETTINGS,
        AgentSource.FLAG_SETTINGS,
        AgentSource.POLICY_SETTINGS,
    ):
        for agent in groups[source]:
            agent_map[agent.agent_type] = agent

    return list(agent_map.values())


# ---------------------------------------------------------------------------
# Frontmatter parser (simple key:value, avoids PyYAML dependency)
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a markdown file."""
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
            current_list_key = None
            continue

        # Detect YAML list items (e.g., "  - Read")
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

            # Empty value → start of a list
            if not value:
                current_list_key = key
                current_list = []
                continue

            # Inline list [a, b, c]
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

    # Flush any trailing list
    if current_list_key:
        fm[current_list_key] = current_list

    return fm, body


# ---------------------------------------------------------------------------
# Parse tools from frontmatter
# ---------------------------------------------------------------------------


def _parse_tools_from_frontmatter(raw: Any) -> list[str] | None:
    """Parse tools field from frontmatter — supports list or comma-separated string."""
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
    """Parse agent definition from markdown file data.

    Translation of parseAgentFromMarkdown from loadAgentsDir.ts.
    """
    agent_type = frontmatter.get("name")
    when_to_use = frontmatter.get("description", "")

    if not agent_type or not isinstance(agent_type, str):
        return None
    if not when_to_use or not isinstance(when_to_use, str):
        logger.debug("Agent file %s missing 'description' in frontmatter", file_path)
        return None

    # Unescape newlines
    when_to_use = when_to_use.replace("\\n", "\n")

    # Parse optional fields
    color = frontmatter.get("color")
    model_raw = frontmatter.get("model")
    model: str | None = None
    if isinstance(model_raw, str) and model_raw.strip():
        trimmed = model_raw.strip()
        model = "inherit" if trimmed.lower() == "inherit" else trimmed

    background_raw = frontmatter.get("background")
    background = background_raw is True or background_raw == "true"

    memory_raw = frontmatter.get("memory")
    memory: AgentMemoryScope | None = None
    if memory_raw and memory_raw in ("user", "project", "local"):
        memory = AgentMemoryScope(memory_raw)

    isolation_raw = frontmatter.get("isolation")
    isolation: IsolationMode | None = None
    if isolation_raw == "worktree":
        isolation = IsolationMode.WORKTREE

    effort = frontmatter.get("effort")
    permission_mode_raw = frontmatter.get("permissionMode")
    permission_mode: str | None = None
    if permission_mode_raw and permission_mode_raw in PERMISSION_MODES:
        permission_mode = permission_mode_raw

    max_turns_raw = frontmatter.get("maxTurns")
    max_turns: int | None = None
    if isinstance(max_turns_raw, int) and max_turns_raw > 0:
        max_turns = max_turns_raw
    elif isinstance(max_turns_raw, str) and max_turns_raw.isdigit():
        max_turns = int(max_turns_raw) if int(max_turns_raw) > 0 else None

    filename = os.path.splitext(os.path.basename(file_path))[0]

    tools = _parse_tools_from_frontmatter(frontmatter.get("tools"))
    disallowed_tools = _parse_tools_from_frontmatter(frontmatter.get("disallowedTools"))
    skills_raw = frontmatter.get("skills")
    skills: list[str] | None = None
    if isinstance(skills_raw, list):
        skills = [str(s).strip() for s in skills_raw if str(s).strip()]
    elif isinstance(skills_raw, str):
        skills = [s.strip() for s in skills_raw.split(",") if s.strip()]

    mcp_servers = frontmatter.get("mcpServers")
    if isinstance(mcp_servers, list):
        mcp_servers = list(mcp_servers)
    else:
        mcp_servers = None

    hooks = frontmatter.get("hooks") if isinstance(frontmatter.get("hooks"), dict) else None

    initial_prompt_raw = frontmatter.get("initialPrompt")
    initial_prompt = initial_prompt_raw if isinstance(initial_prompt_raw, str) and initial_prompt_raw.strip() else None

    # If memory is enabled, auto-inject file tools
    if memory and tools is not None:
        from claude_code.tools.tool_names import FILE_EDIT_TOOL_NAME, FILE_READ_TOOL_NAME, FILE_WRITE_TOOL_NAME

        tool_set = set(tools)
        for t in (FILE_WRITE_TOOL_NAME, FILE_EDIT_TOOL_NAME, FILE_READ_TOOL_NAME):
            if t not in tool_set:
                tools.append(t)

    system_prompt = content.strip()

    def _make_prompt_fn(sp: str, mem: AgentMemoryScope | None, at: str) -> Callable[..., str]:
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
        color=color if color in [c.value for c in AgentColorName] else None,
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
    """Parse agent definition from JSON data."""
    try:
        description = definition.get("description", "")
        prompt = definition.get("prompt", "")
        if not description or not prompt:
            return None

        tools = _parse_tools_from_frontmatter(definition.get("tools"))
        disallowed_tools = _parse_tools_from_frontmatter(definition.get("disallowedTools"))
        memory_raw = definition.get("memory")
        memory = AgentMemoryScope(memory_raw) if memory_raw in ("user", "project", "local") else None

        # Auto-inject file tools for memory agents
        if memory and tools is not None:
            from claude_code.tools.tool_names import FILE_EDIT_TOOL_NAME, FILE_READ_TOOL_NAME, FILE_WRITE_TOOL_NAME

            tool_set = set(tools)
            for t in (FILE_WRITE_TOOL_NAME, FILE_EDIT_TOOL_NAME, FILE_READ_TOOL_NAME):
                if t not in tool_set:
                    tools.append(t)

        system_prompt = prompt

        def _make_prompt_fn(sp: str, mem: AgentMemoryScope | None, at: str) -> Callable[..., str]:
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
            isolation=IsolationMode(definition["isolation"]) if definition.get("isolation") == "worktree" else None,
            mcp_servers=definition.get("mcpServers"),
            hooks=definition.get("hooks"),
        )
        agent._get_system_prompt = _make_prompt_fn(system_prompt, memory, name)
        return agent

    except Exception as exc:
        logger.warning("Error parsing agent '%s' from JSON: %s", name, exc)
        return None


# ---------------------------------------------------------------------------
# Directory loading
# ---------------------------------------------------------------------------


def load_agents_dir(directory: str | Path) -> list[CustomAgentDefinition]:
    """Load all agent definitions from a directory (markdown files)."""
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
                        str(entry),
                        str(directory),
                        fm,
                        body,
                        AgentSource.USER_SETTINGS,  # overridden by caller
                    )
                    if agent:
                        agents.append(agent)
                        seen.add(agent.agent_type)
                except OSError as exc:
                    logger.warning("Failed to read agent file %s: %s", entry, exc)
    except OSError as exc:
        logger.warning("Failed to list agents dir %s: %s", directory, exc)

    return agents


def get_agent_definitions_with_overrides(cwd: str = "") -> AgentDefinitionsResult:
    """Load agent definitions from all standard locations.

    Priority (later overrides earlier):
    1. built-in agents
    2. user agents: ~/.claude/agents/
    3. project agents: .claude/agents/
    """
    from claude_code.tools.agent_tool.built_in import get_built_in_agents

    all_agents_list: list[BaseAgentDefinition] = []

    # 1. Built-in
    all_agents_list.extend(get_built_in_agents())

    # 2. User agents
    user_dir = Path.home() / ".claude" / "agents"
    for agent in load_agents_dir(user_dir):
        agent.source = AgentSource.USER_SETTINGS
        all_agents_list.append(agent)

    # 3. Project agents (walk up from cwd)
    if cwd:
        current = Path(cwd)
        home = Path.home()
        while current != current.parent and current != home.parent:
            agents_dir = current / ".claude" / "agents"
            if agents_dir.is_dir():
                for agent in load_agents_dir(agents_dir):
                    agent.source = AgentSource.PROJECT_SETTINGS
                    all_agents_list.append(agent)
                break  # Only the closest project dir
            current = current.parent

    active_agents = get_active_agents_from_list(all_agents_list)

    return AgentDefinitionsResult(
        active_agents=active_agents,
        all_agents=all_agents_list,
    )
