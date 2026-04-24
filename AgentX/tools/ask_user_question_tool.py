"""AskUserQuestion tool — strict translation of AskUserQuestionTool."""

from __future__ import annotations

from typing import Any

from AgentX.tools.base import BaseTool, ToolParameter
from AgentX.tools.tool_names import ASK_USER_QUESTION_TOOL_NAME
from AgentX.data_types import ToolResult

ASK_USER_QUESTION_TOOL_CHIP_WIDTH = 12


class AskUserQuestionTool(BaseTool):
    """Ask the user multiple choice questions."""

    name = ASK_USER_QUESTION_TOOL_NAME
    is_read_only = True
    is_concurrency_safe = True
    should_defer = True
    search_hint = "prompt the user with a multiple-choice question"

    def get_description(self) -> str:
        return (
            "Asks the user multiple choice questions to gather information, "
            "clarify ambiguity, understand preferences, make decisions or offer them choices."
        )

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="questions",
                type="array",
                description="Questions to ask the user (1-4 questions)",
                items={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The complete question to ask the user",
                        },
                        "header": {
                            "type": "string",
                            "description": f"Very short label displayed as a chip/tag (max {ASK_USER_QUESTION_TOOL_CHIP_WIDTH} chars)",
                        },
                        "options": {
                            "type": "array",
                            "description": "The available choices (2-4 options)",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string", "description": "Option label text"},
                                    "description": {"type": "string", "description": "Optional description"},
                                    "recommended": {"type": "boolean", "description": "Mark as recommended"},
                                },
                                "required": ["label"],
                            },
                        },
                        "multiSelect": {
                            "type": "boolean",
                            "description": "Set to true to allow the user to select multiple options",
                            "default": False,
                        },
                    },
                    "required": ["question", "header", "options"],
                },
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        """Present questions to the user and collect answers.

        In non-interactive mode this returns the questions as-is.
        In REPL mode the UI layer intercepts this tool and prompts the user.
        """
        questions = tool_input.get("questions", [])
        if not questions:
            return ToolResult(data="Error: No questions provided")

        # Format questions for display — UI layer can intercept
        parts: list[str] = []
        for i, q in enumerate(questions, 1):
            text = q.get("question", "")
            header = q.get("header", "")
            options = q.get("options", [])
            parts.append(f"Q{i} [{header}]: {text}")
            for j, opt in enumerate(options, 1):
                label = opt.get("label", "")
                desc = opt.get("description", "")
                rec = " (recommended)" if opt.get("recommended") else ""
                suffix = f" — {desc}" if desc else ""
                parts.append(f"  {j}. {label}{rec}{suffix}")

        # In a real REPL, this would block for user input.
        # For now, return the formatted questions and let the caller handle it.
        return ToolResult(data="\n".join(parts))
