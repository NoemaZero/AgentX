"""TodoWriteTool — strict translation of tools/TodoWriteTool/."""

from __future__ import annotations

from typing import Any

from claude_code.tools.base import BaseTool, ToolParameter
from claude_code.tools.tool_names import TODO_WRITE_TOOL_NAME
from claude_code.data_types import ToolResult

TODO_DESCRIPTION = (
    "Use this tool to create and manage a todo list for tracking progress on tasks."
)

TODO_PROMPT = (
    "Update the todo list for the current session. To be used proactively and often "
    "to track progress and pending tasks. Make sure that at least one task is in_progress "
    "at all times. Always provide both content (imperative) and activeForm (present "
    "continuous) for each task."
)


class TodoWriteTool(BaseTool):
    name = TODO_WRITE_TOOL_NAME
    is_read_only = False
    is_concurrency_safe = False
    should_defer = True
    strict = True
    search_hint = "manage the session task checklist"

    def get_description(self) -> str:
        return f"{TODO_DESCRIPTION}\n\n{TODO_PROMPT}"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="todos",
                type="array",
                description="The updated todo list",
                items={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Unique ID for the todo item"},
                        "content": {"type": "string", "description": "The todo content (imperative form)"},
                        "activeForm": {"type": "string", "description": "Present continuous form of the task"},
                        "status": {
                            "type": "string",
                            "enum": ["not_started", "in_progress", "completed"],
                            "description": "Current status",
                        },
                    },
                    "required": ["id", "content", "status"],
                },
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        todos = tool_input.get("todos", [])
        if not isinstance(todos, list):
            return ToolResult(data="Error: todos must be an array")

        # Store todos in the app state (via kwargs)
        app_state = kwargs.get("app_state")
        if app_state is not None:
            app_state.set_todos(todos)

        # Format response
        lines = ["Todo list updated:"]
        for t in todos:
            status_icon = {"not_started": "\u2b1c", "in_progress": "\U0001f504", "completed": "\u2705"}.get(
                t.get("status", "not_started"), "\u2b1c"
            )
            lines.append(f"  {status_icon} {t.get('content', '(no content)')}")

        return ToolResult(data="\n".join(lines))
