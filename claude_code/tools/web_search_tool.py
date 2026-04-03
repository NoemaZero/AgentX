"""WebSearchTool — strict translation of tools/WebSearchTool/."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from claude_code.tools.base import BaseTool, ToolParameter
from claude_code.tools.tool_names import WEB_SEARCH_TOOL_NAME
from claude_code.data_types import ToolResult


def _get_current_month_year() -> str:
    return datetime.now().strftime("%B %Y")


def get_web_search_description() -> str:
    return f"""- Allows Claude to search the web and use the results to inform responses
- Provides up-to-date information for current events and recent data
- Returns search result information formatted as search result blocks, including links as markdown hyperlinks
- Use this tool for accessing information beyond Claude's knowledge cutoff
- Searches are performed automatically within a single API call

CRITICAL REQUIREMENT - You MUST follow this:
  - After answering the user's question, you MUST include a "Sources:" section at the end of your response
  - In the Sources section, list all relevant URLs from the search results as markdown hyperlinks: [Title](URL)
  - This is MANDATORY - never skip including sources in your response
  - Example format:

    [Your answer here]

    Sources:
    - [Source Title 1](https://example.com/1)
    - [Source Title 2](https://example.com/2)

Usage notes:
  - Domain filtering is supported to include or block specific websites
  - Web search is only available in the US

IMPORTANT - Use the correct year in search queries:
  - The current month is {_get_current_month_year()}. You MUST use this year when searching for recent information, documentation, or current events.
  - Example: If the user asks for "latest React docs", search for "React documentation" with the current year, NOT last year"""


class WebSearchTool(BaseTool):
    name = WEB_SEARCH_TOOL_NAME
    is_read_only = True
    is_concurrency_safe = True
    should_defer = True

    def get_description(self) -> str:
        return get_web_search_description()

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="query", type="string", description="The search query to use"),
            ToolParameter(
                name="allowed_domains",
                type="array",
                description="Only include search results from these domains",
                required=False,
                items={"type": "string"},
            ),
            ToolParameter(
                name="blocked_domains",
                type="array",
                description="Never include search results from these domains",
                required=False,
                items={"type": "string"},
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        query = tool_input.get("query", "")
        if not query:
            return ToolResult(data="Error: query is required")

        # Web search requires external API integration
        # Placeholder: returns a message indicating search is not configured
        return ToolResult(
            data=(
                "Web search is not yet configured. To enable, set SEARCH_API_KEY "
                "and SEARCH_API_URL in your environment.\n\n"
                f"Attempted query: {query}"
            )
        )
