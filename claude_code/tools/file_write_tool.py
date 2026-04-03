"""FileWriteTool — strict translation of tools/FileWriteTool/."""

from __future__ import annotations

import os
from typing import Any

from claude_code.tools.base import BaseTool, ToolParameter
from claude_code.tools.tool_names import FILE_WRITE_TOOL_NAME
from claude_code.data_types import ToolResult


class FileWriteTool(BaseTool):
    name = FILE_WRITE_TOOL_NAME
    is_read_only = False
    is_concurrency_safe = False
    should_defer = False
    strict = True
    search_hint = "create or overwrite files"

    def get_description(self) -> str:
        return "Write a file to the local filesystem."

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="file_path",
                type="string",
                description="The absolute path to the file to write (must be absolute, not relative)",
            ),
            ToolParameter(
                name="content",
                type="string",
                description="The content to write to the file",
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        file_path = tool_input.get("file_path", "")
        content = tool_input.get("content", "")

        if not file_path:
            return ToolResult(data="Error: file_path is required")

        if not os.path.isabs(file_path):
            file_path = os.path.join(cwd, file_path)

        try:
            # Create parent directories if needed
            parent = os.path.dirname(file_path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            is_new = not os.path.exists(file_path)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            action = "Created" if is_new else "Wrote"
            return ToolResult(data=f"{action} {file_path} ({line_count} lines)")

        except Exception as e:
            return ToolResult(data=f"Error writing file: {e}")
