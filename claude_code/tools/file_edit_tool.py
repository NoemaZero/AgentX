"""FileEditTool — strict translation of tools/FileEditTool/."""

from __future__ import annotations

import os
from typing import Any

from claude_code.tools.base import BaseTool, ToolParameter
from claude_code.tools.tool_names import FILE_EDIT_TOOL_NAME
from claude_code.data_types import ToolResult

EDIT_DESCRIPTION = """Performs exact string replacements in files.

Usage:
- You must use your `Read` tool at least once in the conversation before editing. This tool will error if you attempt an edit without reading the file.
- When editing text from Read tool output, ensure you preserve the exact indentation (tabs/spaces) as it appears AFTER the line number prefix. The line number prefix format is: line number + tab. Everything after that is the actual file content to match. Never include any part of the line number prefix in the old_string or new_string.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.
- The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`.
- Use `replace_all` for replacing and renaming strings across the file. This parameter is useful if you want to rename a variable for instance."""


class FileEditTool(BaseTool):
    name = FILE_EDIT_TOOL_NAME
    is_read_only = False
    is_concurrency_safe = False
    should_defer = False
    strict = True
    search_hint = "modify file contents in place"

    def get_description(self) -> str:
        return EDIT_DESCRIPTION

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="file_path",
                type="string",
                description="The absolute path to the file to modify",
            ),
            ToolParameter(name="old_string", type="string", description="The text to replace"),
            ToolParameter(
                name="new_string",
                type="string",
                description="The text to replace it with (must be different from old_string)",
            ),
            ToolParameter(
                name="replace_all",
                type="boolean",
                description="Replace all occurrences of old_string (default false)",
                required=False,
                default=False,
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        file_path = tool_input.get("file_path", "")
        old_string = tool_input.get("old_string", "")
        new_string = tool_input.get("new_string", "")
        replace_all = tool_input.get("replace_all", False)

        if not file_path:
            return ToolResult(data="Error: file_path is required")

        if not os.path.isabs(file_path):
            file_path = os.path.join(cwd, file_path)

        if not os.path.exists(file_path):
            return ToolResult(data=f"Error: File not found: {file_path}")

        if old_string == new_string:
            return ToolResult(data="Error: old_string and new_string must be different")

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            if old_string not in content:
                return ToolResult(data=f"Error: old_string not found in {file_path}")

            count = content.count(old_string)
            if count > 1 and not replace_all:
                return ToolResult(
                    data=f"Error: old_string matches {count} locations in {file_path}. "
                    "Provide more context to make it unique, or set replace_all=true."
                )

            if replace_all:
                new_content = content.replace(old_string, new_string)
            else:
                new_content = content.replace(old_string, new_string, 1)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            replacements = count if replace_all else 1
            return ToolResult(
                data=f"Edited {file_path}: replaced {replacements} occurrence(s)"
            )

        except Exception as e:
            return ToolResult(data=f"Error editing file: {e}")
