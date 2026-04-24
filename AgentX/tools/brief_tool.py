"""Brief tool — strict translation of BriefTool."""

from __future__ import annotations

from typing import Any

from AgentX.tools.base import BaseTool, ToolParameter
from AgentX.tools.tool_names import BRIEF_TOOL_NAME
from AgentX.data_types import ToolResult


class BriefTool(BaseTool):
    """Send a brief message to the user with optional attachments."""

    name = BRIEF_TOOL_NAME

    def get_description(self) -> str:
        return "Send a brief message to the user. Use for proactive insights or concise updates."

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="message",
                type="string",
                description="The message for the user. Supports markdown formatting.",
            ),
            ToolParameter(
                name="attachments",
                type="array",
                description="Optional file paths (absolute or relative to cwd) to attach",
                required=False,
                items={"type": "string"},
            ),
            ToolParameter(
                name="status",
                type="string",
                description=(
                    "Use 'proactive' when you're surfacing something the user hasn't asked for, "
                    "'normal' otherwise."
                ),
                enum=["normal", "proactive"],
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        message = tool_input.get("message", "")
        status = tool_input.get("status", "normal")
        attachments = tool_input.get("attachments", [])

        parts = [message]
        if attachments:
            parts.append(f"\nAttachments: {', '.join(attachments)}")

        return ToolResult(data="\n".join(parts))
