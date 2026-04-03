"""ToolSearch tool — strict translation of ToolSearchTool."""

from __future__ import annotations

import re
from typing import Any

from claude_code.tools.base import BaseTool, ToolParameter
from claude_code.tools.tool_names import TOOL_SEARCH_TOOL_NAME
from claude_code.data_types import ToolResult


class ToolSearchTool(BaseTool):
    """Search for deferred tools by name or keyword."""

    name = TOOL_SEARCH_TOOL_NAME

    def get_description(self) -> str:
        return (
            "Search for available tools by name or keyword. "
            "Use 'select:<tool_name>' for direct selection, or keywords to search."
        )

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                type="string",
                description=(
                    'Query to find deferred tools. Use "select:<tool_name>" '
                    "for direct selection, or keywords to search."
                ),
            ),
            ToolParameter(
                name="max_results",
                type="integer",
                description="Maximum number of results to return (default: 5)",
                required=False,
                default=5,
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        query = tool_input.get("query", "")
        max_results = tool_input.get("max_results", 5)

        # Get all available tools from the engine
        all_tools: list[BaseTool] = kwargs.get("all_tools", [])
        if not all_tools:
            return ToolResult(data="No tools available to search")

        # Direct selection mode
        if query.startswith("select:"):
            tool_name = query[7:].strip()
            for tool in all_tools:
                if tool.name.lower() == tool_name.lower() or tool_name.lower() in [
                    a.lower() for a in tool.aliases
                ]:
                    return ToolResult(
                        data=f"Selected tool: {tool.name}\nDescription: {tool.get_description()}"
                    )
            return ToolResult(data=f"Tool '{tool_name}' not found")

        # Keyword search
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error:
            pattern = re.compile(re.escape(query), re.IGNORECASE)

        matches: list[tuple[str, str, str]] = []
        for tool in all_tools:
            score_parts: list[str] = [tool.name]
            if tool.search_hint:
                score_parts.append(tool.search_hint)
            score_parts.append(tool.get_description())

            searchable = " ".join(score_parts)
            if pattern.search(searchable):
                matches.append((tool.name, tool.get_description()[:120], tool.search_hint or ""))

        if not matches:
            return ToolResult(data=f"No tools matching '{query}' found")

        matches = matches[:max_results]
        lines = [f"Found {len(matches)} tool(s):"]
        for name, desc, hint in matches:
            lines.append(f"  - {name}: {desc}")
            if hint:
                lines.append(f"    Hint: {hint}")

        return ToolResult(data="\n".join(lines))
