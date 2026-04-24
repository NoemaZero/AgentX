"""Agent definition loading — strict translation of loadAgentsDir.ts.

Loads agent definitions from:
1. ~/.agentx/agents/  — user agents
2. .agentx/agents/    — project agents
3. Built-in agent types

Each agent is a markdown file with optional YAML frontmatter.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from pydantic import Field

from AgentX.data_types import AgentContextMode, AgentModel, maybe_coerce_str_enum
from AgentX.pydantic_models import FrozenModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class AgentDefinition(FrozenModel):
    """Parsed agent definition — translation of AgentDefinition type."""

    name: str
    description: str = ""
    prompt: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    model: AgentModel | None = None
    context: AgentContextMode | None = None
    source_path: str = ""
    frontmatter: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a markdown file.

    Returns (frontmatter_dict, remaining_content).
    Uses a simple key:value parser to avoid PyYAML dependency.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    fm_text = match.group(1)
    body = content[match.end():]
    fm: dict[str, Any] = {}

    for line in fm_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            # Handle list values (simple inline format)
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

    return fm, body


def _parse_agent_definition(name: str, content: str, source_path: str) -> AgentDefinition:
    """Parse an agent definition from markdown content."""
    fm, body = _parse_frontmatter(content)

    # Extract allowed-tools
    allowed_tools_raw = fm.get("allowed-tools", fm.get("allowed_tools", []))
    if isinstance(allowed_tools_raw, str):
        allowed_tools = [t.strip() for t in allowed_tools_raw.split(",")]
    elif isinstance(allowed_tools_raw, list):
        allowed_tools = [str(t) for t in allowed_tools_raw]
    else:
        allowed_tools = []

    return AgentDefinition(
        name=fm.get("name", name),
        description=fm.get("description", _extract_first_paragraph(body)),
        prompt=body.strip(),
        allowed_tools=allowed_tools,
        model=maybe_coerce_str_enum(AgentModel, fm.get("model")),
        context=maybe_coerce_str_enum(AgentContextMode, fm.get("context")),
        source_path=source_path,
        frontmatter=fm,
    )


def _extract_first_paragraph(text: str) -> str:
    """Extract the first non-empty paragraph from markdown text."""
    for para in text.split("\n\n"):
        stripped = para.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:200]
    return ""


# ---------------------------------------------------------------------------
# Directory loading
# ---------------------------------------------------------------------------


def load_agents_dir(directory: str | Path) -> list[AgentDefinition]:
    """Load all agent definitions from a directory.

    Supports both flat markdown files and name/AGENT.md structure.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []

    agents: list[AgentDefinition] = []
    seen_names: set[str] = set()

    try:
        for entry in sorted(directory.iterdir()):
            if entry.is_file() and entry.suffix.lower() == ".md":
                name = entry.stem
                if name in seen_names:
                    continue
                try:
                    content = entry.read_text(encoding="utf-8")
                    agent = _parse_agent_definition(name, content, str(entry))
                    agents.append(agent)
                    seen_names.add(name)
                except OSError as exc:
                    logger.warning("Failed to read agent file %s: %s", entry, exc)
            elif entry.is_dir():
                # Check for AGENT.md inside the directory
                agent_file = entry / "AGENT.md"
                if agent_file.is_file():
                    name = entry.name
                    if name in seen_names:
                        continue
                    try:
                        content = agent_file.read_text(encoding="utf-8")
                        agent = _parse_agent_definition(name, content, str(agent_file))
                        agents.append(agent)
                        seen_names.add(name)
                    except OSError as exc:
                        logger.warning("Failed to read agent file %s: %s", agent_file, exc)
    except OSError as exc:
        logger.warning("Failed to list agents dir %s: %s", directory, exc)

    return agents


def get_all_agent_definitions(
    cwd: str = "",
    additional_dirs: list[str] | None = None,
) -> list[AgentDefinition]:
    """Load agent definitions from all standard locations.

    Priority (later overrides earlier):
    1. Project agents: .agentx/agents/ (walk up to home)
    2. User agents: ~/.agentx/agents/
    3. Additional dirs (from --add-dir etc.)
    """
    agents: dict[str, AgentDefinition] = {}

    # 1. Project agents — walk up from cwd
    if cwd:
        current = Path(cwd)
        home = Path.home()
        while current != current.parent and current != home.parent:
            agents_dir = current / ".agentx" / "agents"
            if agents_dir.is_dir():
                for agent in load_agents_dir(agents_dir):
                    if agent.name not in agents:
                        agents[agent.name] = agent
            current = current.parent

    # 2. User agents
    user_dir = Path.home() / ".agentx" / "agents"
    for agent in load_agents_dir(user_dir):
        agents.setdefault(agent.name, agent)

    # 3. Additional dirs
    for d in additional_dirs or []:
        agents_dir = Path(d) / ".agentx" / "agents"
        for agent in load_agents_dir(agents_dir):
            agents[agent.name] = agent  # Override

    return list(agents.values())
