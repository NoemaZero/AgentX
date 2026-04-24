"""Skills system — strict translation of skills/loadSkillsDir.ts.

Loads skill definitions from:
1. ~/.agentx/skills/ (user skills)
2. .agentx/skills/ (project skills, walk up)
3. Additional dirs (--add-dir)
4. Bundled skills (programmatic)

Each skill is a directory with SKILL.md inside, containing optional YAML frontmatter.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from pydantic import Field

from AgentX.data_types import AgentContextMode, AgentModel, SkillSource
from AgentX.pydantic_models import FrozenModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SKILL_BUDGET_CONTEXT_PERCENT = 0.01
MAX_LISTING_DESC_CHARS = 250

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class SkillDefinition(FrozenModel):
    """Parsed skill definition — translation of SkillCommand type."""

    name: str
    description: str = ""
    prompt: str = ""
    when_to_use: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    arguments: list[str] = Field(default_factory=list)
    argument_hint: str = ""
    model: AgentModel | str | None = None
    context: AgentContextMode | None = AgentContextMode.FORK
    paths: list[str] = Field(default_factory=list)  # conditional activation patterns
    user_invocable: bool = True
    disable_model_invocation: bool = False
    version: str = ""
    source_path: str = ""
    loaded_from: SkillSource | str | None = None
    base_dir: str = ""
    frontmatter: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_skill_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML-like frontmatter from skill markdown.

    Returns (frontmatter_dict, remaining_content).
    Simple key:value parser to avoid PyYAML dependency.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    fm_text = match.group(1)
    body = content[match.end():]
    fm: dict[str, Any] = {}

    current_key: str | None = None
    current_list: list[str] | None = None

    for line in fm_text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Check for list continuation (  - item)
        if stripped.startswith("- ") and current_key and current_list is not None:
            current_list.append(stripped[2:].strip().strip("'\""))
            fm[current_key] = current_list
            continue

        if ":" in stripped:
            if current_key and current_list is not None:
                fm[current_key] = current_list

            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()

            if not value:
                # Might be the start of a list
                current_key = key
                current_list = []
                continue

            current_key = None
            current_list = None

            # Parse value
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
        else:
            current_key = None
            current_list = None

    if current_key and current_list is not None:
        fm[current_key] = current_list

    return fm, body


def _parse_skill_definition(
    name: str, content: str, source_path: str, loaded_from: str = ""
) -> SkillDefinition:
    """Parse a skill definition from markdown content."""
    fm, body = parse_skill_frontmatter(content)

    # Parse allowed-tools
    allowed_tools_raw = fm.get("allowed-tools", fm.get("allowed_tools", []))
    if isinstance(allowed_tools_raw, str):
        allowed_tools = [t.strip() for t in allowed_tools_raw.split(",")]
    elif isinstance(allowed_tools_raw, list):
        allowed_tools = [str(t) for t in allowed_tools_raw]
    else:
        allowed_tools = []

    # Parse arguments
    args_raw = fm.get("arguments", [])
    if isinstance(args_raw, str):
        arguments = [a.strip() for a in args_raw.split(",")]
    elif isinstance(args_raw, list):
        arguments = [str(a) for a in args_raw]
    else:
        arguments = []

    # Parse paths
    paths_raw = fm.get("paths", [])
    if isinstance(paths_raw, str):
        paths = [p.strip() for p in paths_raw.split(",")]
    elif isinstance(paths_raw, list):
        paths = [str(p) for p in paths_raw]
    else:
        paths = []

    # Extract description from body if not in frontmatter
    description = fm.get("description", "")
    if not description:
        description = _extract_description_from_body(body)

    return SkillDefinition(
        name=fm.get("name", name),
        description=description,
        prompt=body.strip(),
        when_to_use=fm.get("when_to_use", fm.get("when-to-use", "")),
        allowed_tools=allowed_tools,
        arguments=arguments,
        argument_hint=fm.get("argument-hint", fm.get("argument_hint", "")),
        model=fm.get("model", ""),
        context=fm.get("context"),
        paths=paths,
        user_invocable=fm.get("user-invocable", fm.get("user_invocable", True)),
        disable_model_invocation=fm.get(
            "disable-model-invocation", fm.get("disable_model_invocation", False)
        ),
        version=fm.get("version", ""),
        source_path=source_path,
        loaded_from=loaded_from,
        base_dir=str(Path(source_path).parent) if source_path else "",
        frontmatter=fm,
    )


def _extract_description_from_body(text: str) -> str:
    """Extract a description from the first non-heading paragraph."""
    for para in text.split("\n\n"):
        stripped = para.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:MAX_LISTING_DESC_CHARS]
    return ""


# ---------------------------------------------------------------------------
# Directory loading
# ---------------------------------------------------------------------------


def load_skills_dir(directory: str | Path, loaded_from: str = "") -> list[SkillDefinition]:
    """Load all skill definitions from a directory.

    Supports:
    - skill-name/SKILL.md (standard)
    - skill-name.md (legacy commands compat)
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []

    skills: list[SkillDefinition] = []
    seen_names: set[str] = set()

    try:
        for entry in sorted(directory.iterdir()):
            if entry.is_dir():
                skill_file = entry / "SKILL.md"
                if skill_file.is_file():
                    name = entry.name
                    if name in seen_names:
                        continue
                    try:
                        content = skill_file.read_text(encoding="utf-8")
                        skill = _parse_skill_definition(name, content, str(skill_file), loaded_from)
                        skills.append(skill)
                        seen_names.add(name)
                    except Exception as exc:
                        logger.warning("Failed to read skill %s: %s", skill_file, exc)
            elif entry.is_file() and entry.suffix.lower() == ".md":
                name = entry.stem
                if name in seen_names:
                    continue
                try:
                    content = entry.read_text(encoding="utf-8")
                    skill = _parse_skill_definition(name, content, str(entry), loaded_from)
                    skills.append(skill)
                    seen_names.add(name)
                except Exception as exc:
                    logger.warning("Failed to read skill file %s: %s", entry, exc)
    except Exception as exc:
        logger.warning("Failed to list skills dir %s: %s", directory, exc)

    return skills


def get_all_skills(
    cwd: str = "",
    additional_dirs: list[str] | None = None,
) -> list[SkillDefinition]:
    """Load skill definitions from all standard locations.

    Priority: managed > user > project > additional > bundled.
    Later entries override earlier ones by name.
    """
    skills: dict[str, SkillDefinition] = {}

    # 1. Project skills — walk up from cwd
    if cwd:
        current = Path(cwd)
        home = Path.home()
        while current != current.parent and current != home.parent:
            skills_dir = current / ".agentx" / "skills"
            if skills_dir.is_dir():
                for skill in load_skills_dir(skills_dir, loaded_from="project"):
                    if skill.name not in skills:
                        skills[skill.name] = skill
            # Also check legacy commands dir
            cmds_dir = current / ".agentx" / "commands"
            if cmds_dir.is_dir():
                for skill in load_skills_dir(cmds_dir, loaded_from="project"):
                    if skill.name not in skills:
                        skills[skill.name] = skill
            current = current.parent

    # 2. User skills
    user_dir = Path.home() / ".agentx" / "skills"
    for skill in load_skills_dir(user_dir, loaded_from="user"):
        skills.setdefault(skill.name, skill)

    # 3. Additional dirs
    for d in additional_dirs or []:
        skills_dir = Path(d) / ".agentx" / "skills"
        for skill in load_skills_dir(skills_dir, loaded_from="additional"):
            skills[skill.name] = skill

    # 4. Bundled skills (programmatic, lowest priority)
    bundled_dir = Path(__file__).parent / "bundled"
    for skill in load_skills_dir(bundled_dir, loaded_from="bundled"):
        skills.setdefault(skill.name, skill)

    return list(skills.values())


# ---------------------------------------------------------------------------
# Skill listing for system prompt
# ---------------------------------------------------------------------------


def format_skill_listing(
    skills: list[SkillDefinition],
    context_window: int = 128_000,
) -> str:
    """Format skill listing for the system prompt.

    Respects the 1% context budget.
    """
    if not skills:
        return ""

    budget_chars = int(context_window * SKILL_BUDGET_CONTEXT_PERCENT * 4)  # rough token-to-char
    lines: list[str] = []
    total_chars = 0

    for skill in skills:
        if skill.disable_model_invocation:
            continue

        desc = skill.description[:MAX_LISTING_DESC_CHARS] if skill.description else ""
        when = f" - {skill.when_to_use}" if skill.when_to_use else ""
        line = f"  - {skill.name}: {desc}{when}"

        if total_chars + len(line) > budget_chars:
            # Truncate: just show name
            line = f"  - {skill.name}"
            if total_chars + len(line) > budget_chars:
                break

        lines.append(line)
        total_chars += len(line)

    if not lines:
        return ""

    return "Available skills:\n" + "\n".join(lines)


def get_skill_prompt(skill: SkillDefinition, args: str = "") -> str:
    """Generate the prompt content for executing a skill.

    Translation of getPromptForCommand().
    """
    content = skill.prompt

    # Replace template variables
    if skill.base_dir:
        content = f"Base directory for this skill: {skill.base_dir}\n\n{content}"
    content = content.replace("${CLAUDE_SKILL_DIR}", skill.base_dir)

    # Simple argument substitution
    if args and skill.arguments:
        for i, arg_name in enumerate(skill.arguments):
            parts = args.split(maxsplit=len(skill.arguments) - 1)
            if i < len(parts):
                content = content.replace(f"${{{arg_name}}}", parts[i])
                content = content.replace(f"${{arg{i + 1}}}", parts[i])

    return content
