"""WebSearchTool — strict translation of tools/WebSearchTool/."""

from __future__ import annotations

import asyncio
import html as _html
import logging
import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from AgentX.data_types import ToolResult
from AgentX.tools.base import BaseTool, ToolParameter
from AgentX.tools.tool_names import WEB_SEARCH_TOOL_NAME
from AgentX.constants.identity import get_app_name

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"

# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------


def _validate_url(url: str) -> tuple[bool, str]:
    """Validate URL scheme and domain."""
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False, f"Only http/https allowed, got '{p.scheme or 'none'}'"
        if not p.netloc:
            return False, "Missing domain"
        if p.username or p.password:
            return False, "URL must not contain credentials"
        return True, ""
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return _html.unescape(text).strip()


def _normalize(text: str) -> str:
    """Normalize whitespace."""
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------------------
# Result formatter
# ---------------------------------------------------------------------------


def _format_results(query: str, items: list[dict[str, Any]], n: int) -> str:
    """Format search results into plaintext output."""
    if not items:
        return f"No results for: {query}"
    lines = [f"Results for: {query}\n"]
    for i, item in enumerate(items[:n], 1):
        title = _normalize(_strip_tags(item.get("title", "")))
        snippet = _normalize(_strip_tags(item.get("content", "")))
        lines.append(f"{i}. {title}\n   {item.get('url', '')}")
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Current month/year helper (matches TS original prompt)
# ---------------------------------------------------------------------------


def _get_current_month_year() -> str:
    return datetime.now().strftime("%B %Y")


# ---------------------------------------------------------------------------
# Description (character-for-character translation of the TypeScript original)
# ---------------------------------------------------------------------------


def get_web_search_description() -> str:
    return f"""- Allows {get_app_name()} to search the web and use the results to inform responses
- Provides up-to-date information for current events and recent data
- Returns search result information formatted as search result blocks, including links as markdown hyperlinks
- Use this tool for accessing information beyond {get_app_name()}'s knowledge cutoff
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


# ---------------------------------------------------------------------------
# WebSearchTool
# ---------------------------------------------------------------------------


class WebSearchTool(BaseTool):
    name = WEB_SEARCH_TOOL_NAME
    is_read_only = True
    is_concurrency_safe = True
    should_defer = True

    def get_description(self) -> str:
        return get_web_search_description()

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="query", type="string", description="The search query to use", required=True),
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

        # Resolve provider: env var > brave > tavily > duckduckgo
        provider = (os.environ.get("SEARCH_PROVIDER", "")).strip().lower()
        if not provider:
            if os.environ.get("BRAVE_API_KEY", ""):
                provider = "brave"
            elif os.environ.get("TAVILY_API_KEY", ""):
                provider = "tavily"
            else:
                provider = "duckduckgo"

        n = 5  # default result count

        try:
            if provider == "brave":
                result_text = await self._search_brave(query, n)
            elif provider == "tavily":
                result_text = await self._search_tavily(query, n)
            elif provider == "duckduckgo":
                result_text = await self._search_duckduckgo(query, n)
            else:
                result_text = f"Error: unknown search provider '{provider}'"
        except Exception as e:
            logger.warning("Search provider '%s' failed: %s", provider, e)
            # Fallback to DuckDuckGo
            try:
                result_text = await self._search_duckduckgo(query, n)
            except Exception as e2:
                result_text = f"Error: All search providers failed ({e2})"

        return ToolResult(data=result_text)

    # ── Provider implementations ──

    async def _search_brave(self, query: str, n: int) -> str:
        api_key = os.environ.get("BRAVE_API_KEY", "")
        if not api_key:
            logger.warning("BRAVE_API_KEY not set, falling back to DuckDuckGo")
            return await self._search_duckduckgo(query, n)
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": n},
                headers={"Accept": "application/json", "X-Subscription-Token": api_key},
                timeout=10.0,
            )
            r.raise_for_status()
        items = [
            {"title": x.get("title", ""), "url": x.get("url", ""), "content": x.get("description", "")}
            for x in r.json().get("web", {}).get("results", [])
        ]
        return _format_results(query, items, n)

    async def _search_tavily(self, query: str, n: int) -> str:
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            logger.warning("TAVILY_API_KEY not set, falling back to DuckDuckGo")
            return await self._search_duckduckgo(query, n)
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"query": query, "max_results": n},
                timeout=15.0,
            )
            r.raise_for_status()
        return _format_results(query, r.json().get("results", []), n)

    async def _search_duckduckgo(self, query: str, n: int) -> str:
        """Search via DuckDuckGo (free, no API key needed)."""
        try:
            from ddgs import DDGS

            ddgs = DDGS(timeout=10)
            raw = await asyncio.wait_for(
                asyncio.to_thread(ddgs.text, query, max_results=n),
                timeout=15.0,
            )
            if not raw:
                return f"No results for: {query}"
            items = [
                {"title": r.get("title", ""), "url": r.get("href", ""), "content": r.get("body", "")}
                for r in raw
            ]
            return _format_results(query, items, n)
        except ImportError:
            # Fallback: direct HTML scrape
            return await self._search_duckduckgo_html(query, n)
        except Exception as e:
            return f"Error: DuckDuckGo search failed ({e})"

    async def _search_duckduckgo_html(self, query: str, n: int) -> str:
        """Fallback DuckDuckGo search via HTML scraping."""
        try:
            from urllib.parse import quote

            url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    url,
                    headers={"User-Agent": USER_AGENT},
                    timeout=10.0,
                )
                r.raise_for_status()
            # Parse result snippets from HTML
            results = re.findall(
                r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>([^<]*)</a>.*?<a[^>]*class="result__snippet"[^>]*>([^<]*)</a>',
                r.text,
                re.DOTALL,
            )
            items = [
                {"title": _html.unescape(t), "url": u, "content": _html.unescape(s)}
                for u, t, s in results[:n]
            ]
            return _format_results(query, items, n)
        except Exception as e:
            return f"Error: DuckDuckGo HTML search failed ({e})"
