"""GlobTool — strict translation of tools/GlobTool/."""

from __future__ import annotations

import glob as _glob
import os
from typing import Any

from AgentX.tools.base import BaseTool, ToolParameter
from AgentX.tools.tool_names import GLOB_TOOL_NAME
from AgentX.data_types import ToolResult

GLOB_DESCRIPTION = """- Fast file pattern matching tool that works with any codebase size
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Use this tool when you need to find files by name patterns
- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the Agent tool instead"""


class GlobTool(BaseTool):
    name = GLOB_TOOL_NAME
    is_read_only = True
    is_concurrency_safe = True
    should_defer = False
    search_hint = "find files by name pattern or wildcard"

    def get_description(self) -> str:
        return GLOB_DESCRIPTION

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="pattern",
                type="string",
                description="The glob pattern to match files against",
            ),
            ToolParameter(
                name="path",
                type="string",
                description=(
                    "The directory to search in. If not specified, the current working directory "
                    "will be used. IMPORTANT: Omit this field to use the default directory. "
                    'DO NOT enter "undefined" or "null" - simply omit it for the default behavior. '
                    "Must be a valid directory path if provided."
                ),
                required=False,
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        pattern = tool_input.get("pattern", "")
        search_path = tool_input.get("path") or cwd

        if not pattern:
            return ToolResult(data="Error: pattern is required")

        if not os.path.isabs(search_path):
            search_path = os.path.join(cwd, search_path)

        try:
            full_pattern = os.path.join(search_path, pattern)
            matches = _glob.glob(full_pattern, recursive=True)

            # Sort by modification time (most recent first)
            matches.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)

            if not matches:
                return ToolResult(data=f"No files matched pattern: {pattern} in {search_path}")

            result_lines = [f"Found {len(matches)} file(s):"]
            for m in matches[:500]:  # Limit results
                result_lines.append(m)

            if len(matches) > 500:
                result_lines.append(f"... and {len(matches) - 500} more")

            return ToolResult(data="\n".join(result_lines))

        except Exception as e:
            return ToolResult(data=f"Error: {e}")
