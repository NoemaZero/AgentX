"""Skill tool — strict translation of SkillTool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_code.skills import (
    SkillDefinition,
    get_all_skills,
    get_skill_prompt,
)
from claude_code.tools.base import BaseTool, ToolParameter
from claude_code.tools.tool_names import SKILL_TOOL_NAME
from claude_code.data_types import ToolResult

SKILL_DESCRIPTION = """Load a skill definition and apply its instructions.

Skills are loaded from .claude/skills/ directories and provide reusable
instruction sets. Each skill has a SKILL.md file with optional YAML frontmatter.

Use this tool when you want to apply a predefined workflow or set of instructions.
Skills can contain templates, coding patterns, review checklists, etc."""


class SkillTool(BaseTool):
    """Load and execute a skill definition.

    Translation of tools/SkillTool/SkillTool.ts.
    Supports both inline (default) and forked (context: "fork") execution.
    """

    name = SKILL_TOOL_NAME
    should_defer = False
    _skills_cache: dict[str, SkillDefinition] | None = None

    def get_description(self) -> str:
        return SKILL_DESCRIPTION

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="skill_name",
                type="string",
                description="The name of the skill to invoke (or path to a SKILL.md file)",
            ),
            ToolParameter(
                name="arguments",
                type="string",
                description="Optional arguments to pass to the skill",
                required=False,
            ),
        ]

    def _get_skills(self, cwd: str) -> dict[str, SkillDefinition]:
        """Load and cache available skills."""
        if self._skills_cache is None:
            skills = get_all_skills(cwd=cwd)
            self._skills_cache = {s.name: s for s in skills}
        return self._skills_cache

    def invalidate_cache(self) -> None:
        """Clear the skills cache to force reload."""
        self._skills_cache = None

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        skill_name = tool_input.get("skill_name", tool_input.get("skill_path", ""))
        arguments = tool_input.get("arguments", "")

        if not skill_name:
            return ToolResult(data="Error: skill_name is required")

        # Strip leading /
        skill_name = skill_name.lstrip("/")

        # Try as a direct file path first
        path = Path(skill_name)
        if not path.is_absolute():
            path = Path(cwd) / skill_name

        if path.exists() and path.is_file():
            try:
                content = path.read_text(encoding="utf-8")
                return ToolResult(
                    data=f"Loaded skill from {path.name}:\n\n{content}",
                )
            except OSError as exc:
                return ToolResult(data=f"Error reading skill file: {exc}")

        # Look up by name in loaded skills
        skills = self._get_skills(cwd)
        skill = skills.get(skill_name)

        if skill is None:
            # Suggest similar names
            available = list(skills.keys())[:20]
            msg = f"Error: Skill not found: {skill_name}"
            if available:
                msg += f"\nAvailable skills: {', '.join(available)}"
            return ToolResult(data=msg)

        # Check disable_model_invocation
        if skill.disable_model_invocation:
            return ToolResult(
                data=f"Error: Skill '{skill_name}' has disabled model invocation. "
                "It can only be invoked by the user via / commands."
            )

        # Generate the prompt content
        prompt_content = get_skill_prompt(skill, arguments)

        # Build result metadata
        result_data: dict[str, Any] = {
            "success": True,
            "commandName": skill.name,
            "status": "inline",
        }

        if skill.allowed_tools:
            result_data["allowedTools"] = skill.allowed_tools
        if skill.model and skill.model != "inherit":
            result_data["model"] = skill.model

        return ToolResult(
            data=f"Loaded skill '{skill.name}':\n\n{prompt_content}",
        )
