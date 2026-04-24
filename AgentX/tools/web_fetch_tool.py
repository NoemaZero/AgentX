"""WebFetchTool — strict translation of tools/WebFetchTool/."""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

from AgentX.constants.identity import get_app_user_agent
from AgentX.tools.base import BaseTool, ToolParameter
from AgentX.tools.tool_names import WEB_FETCH_TOOL_NAME
from AgentX.data_types import ToolResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (matching TS source values)
# ---------------------------------------------------------------------------
MAX_URL_LENGTH = 2000
MAX_HTTP_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB
MAX_MARKDOWN_LENGTH = 100_000  # characters
FETCH_TIMEOUT_S = 60  # seconds
MAX_REDIRECTS = 10
CACHE_TTL_S = 15 * 60  # 15 minutes
CACHE_MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50MB

# ---------------------------------------------------------------------------
# Preapproved domains (subset matching TS preapproved.ts)
# ---------------------------------------------------------------------------
PREAPPROVED_DOMAINS: frozenset[str] = frozenset({
    "docs.anthropic.com", "console.anthropic.com", "support.anthropic.com",
    "docs.python.org", "docs.djangoproject.com", "flask.palletsprojects.com",
    "fastapi.tiangolo.com", "pydantic-docs.helpmanual.io",
    "docs.rs", "doc.rust-lang.org",
    "developer.mozilla.org", "nodejs.org", "react.dev", "nextjs.org",
    "docs.github.com", "docs.gitlab.com",
    "docs.aws.amazon.com", "cloud.google.com", "learn.microsoft.com",
    "docs.docker.com", "kubernetes.io",
    "pypi.org", "npmjs.com", "crates.io",
    "stackoverflow.com", "en.wikipedia.org",
})

FETCH_DESCRIPTION = """- Fetches content from a specified URL and processes it using an AI model
- Takes a URL and a prompt as input
- Fetches the URL content, converts HTML to markdown
- Processes the content with the prompt using a small, fast model
- Returns the model's response about the content
- Use this tool when you need to retrieve and analyze web content

Usage notes:
  - IMPORTANT: If an MCP-provided web fetch tool is available, prefer using that tool instead of this one, as it may have fewer restrictions.
  - The URL must be a fully-formed valid URL
  - HTTP URLs will be automatically upgraded to HTTPS
  - The prompt should describe what information you want to extract from the page
  - This tool is read-only and does not modify any files
  - Results may be summarized if the content is very large
  - Includes a self-cleaning 15-minute cache for faster responses when repeatedly accessing the same URL
  - When a URL redirects to a different host, the tool will inform you and provide the redirect URL in a special format. You should then make a new WebFetch request with the redirect URL to fetch the content.
  - For GitHub URLs, prefer using the gh CLI via Bash instead (e.g., gh pr view, gh issue view, gh api)."""


# ---------------------------------------------------------------------------
# Simple LRU cache implementation with TTL
# ---------------------------------------------------------------------------
class _CacheEntry:
    __slots__ = ("content", "content_type", "size", "timestamp")

    def __init__(self, content: str, content_type: str, size: int) -> None:
        self.content = content
        self.content_type = content_type
        self.size = size
        self.timestamp = time.monotonic()

    def is_expired(self) -> bool:
        return (time.monotonic() - self.timestamp) > CACHE_TTL_S


class _URLCache:
    """Simple URL → content cache with TTL and size limit."""

    def __init__(self, max_bytes: int = CACHE_MAX_SIZE_BYTES, ttl: float = CACHE_TTL_S) -> None:
        self._store: dict[str, _CacheEntry] = {}
        self._max_bytes = max_bytes
        self._ttl = ttl
        self._current_size = 0

    def get(self, key: str) -> _CacheEntry | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if entry.is_expired():
            self._remove(key)
            return None
        return entry

    def put(self, key: str, content: str, content_type: str) -> None:
        size = len(content.encode("utf-8", errors="replace"))
        # Evict expired entries first
        self._evict_expired()
        # Evict until we have room
        while self._current_size + size > self._max_bytes and self._store:
            oldest_key = next(iter(self._store))
            self._remove(oldest_key)
        self._store[key] = _CacheEntry(content, content_type, size)
        self._current_size += size

    def clear(self) -> None:
        self._store.clear()
        self._current_size = 0

    def _remove(self, key: str) -> None:
        entry = self._store.pop(key, None)
        if entry is not None:
            self._current_size -= entry.size

    def _evict_expired(self) -> None:
        expired = [k for k, v in self._store.items() if v.is_expired()]
        for k in expired:
            self._remove(k)


_url_cache = _URLCache()


# ---------------------------------------------------------------------------
# URL Validation (matches TS validateURL)
# ---------------------------------------------------------------------------
def _validate_url(url: str) -> bool:
    """Validate URL: length, parsability, no credentials, multi-segment host."""
    if len(url) > MAX_URL_LENGTH:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if not parsed.scheme or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    # Require at least two hostname segments (block localhost etc.)
    if len(parsed.hostname.split(".")) < 2:
        return False
    return True


def _is_preapproved_host(hostname: str) -> bool:
    """Check if hostname is in preapproved list."""
    host_lower = hostname.lower()
    if host_lower in PREAPPROVED_DOMAINS:
        return True
    # Strip www. and check again
    if host_lower.startswith("www."):
        return host_lower[4:] in PREAPPROVED_DOMAINS
    return False


def _strip_www(hostname: str) -> str:
    return hostname[4:] if hostname.startswith("www.") else hostname


def _is_permitted_redirect(original_url: str, redirect_url: str) -> bool:
    """Same-origin redirect check (matches TS isPermittedRedirect)."""
    try:
        orig = urlparse(original_url)
        redir = urlparse(redirect_url)
    except Exception:
        return False
    if orig.scheme != redir.scheme:
        return False
    if orig.port != redir.port:
        return False
    if redir.username or redir.password:
        return False
    return _strip_www(orig.hostname or "") == _strip_www(redir.hostname or "")


def _html_to_markdown(html: str) -> str:
    """Convert HTML to Markdown. Uses markdownify if available, else regex fallback."""
    try:
        from markdownify import markdownify as md  # type: ignore[import-untyped]

        return md(html, heading_style="ATX", strip=["script", "style", "img"])
    except ImportError:
        pass
    # Regex fallback (same as original)
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Secondary model prompt (matches TS makeSecondaryModelPrompt)
# ---------------------------------------------------------------------------
def _make_secondary_model_prompt(
    markdown_content: str, prompt: str, is_preapproved: bool
) -> str:
    """Build the prompt string sent to the secondary (small) model."""
    if is_preapproved:
        instructions = (
            "Provide a concise response based on the following web content. "
            "Include code examples and documentation excerpts when relevant."
        )
    else:
        instructions = (
            "Provide a concise response based on the following web content.\n"
            "- Limit direct quotes to 125 characters maximum.\n"
            "- Use quotation marks for any direct quotes.\n"
            "- Do not reproduce song lyrics, poems, or other copyrighted creative content."
        )

    # Truncate content
    if len(markdown_content) > MAX_MARKDOWN_LENGTH:
        markdown_content = markdown_content[:MAX_MARKDOWN_LENGTH] + "\n[Content truncated due to length...]"

    return (
        f"{instructions}\n\n"
        f"<web_content>\n{markdown_content}\n</web_content>\n\n"
        f"User's request: {prompt}"
    )


# ---------------------------------------------------------------------------
# WebFetchTool
# ---------------------------------------------------------------------------
class WebFetchTool(BaseTool):
    name = WEB_FETCH_TOOL_NAME
    user_facing_name_override = "Fetch"
    is_read_only = True
    is_concurrency_safe = True
    should_defer = True
    search_hint = "fetch and extract content from a URL"

    def get_description(self) -> str:
        return FETCH_DESCRIPTION

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="url", type="string", description="The URL to fetch content from"),
            ToolParameter(name="prompt", type="string", description="The prompt to run on the fetched content"),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        url = tool_input.get("url", "")
        prompt = tool_input.get("prompt", "")

        if not url:
            return ToolResult(data="Error: url is required")
        if not prompt:
            return ToolResult(data="Error: prompt is required")

        # Upgrade HTTP to HTTPS
        if url.startswith("http://"):
            url = "https://" + url[7:]

        # Validate URL
        if not _validate_url(url):
            return ToolResult(data=f"Error: Invalid URL: {url}")

        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        is_preapproved = _is_preapproved_host(hostname)

        # Check cache
        cached = _url_cache.get(url)
        if cached is not None:
            return await self._process_content(
                cached.content, cached.content_type, url, prompt, is_preapproved, **kwargs
            )

        # Fetch with redirect handling
        try:
            import httpx

            content_bytes: bytes = b""
            content_type: str = ""
            final_url = url

            current_url = url
            seen_urls: set[str] = set()

            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=FETCH_TIMEOUT_S,
                max_redirects=0,
            ) as client:
                for _ in range(MAX_REDIRECTS):
                    if current_url in seen_urls:
                        return ToolResult(data="Error: Redirect loop detected")
                    seen_urls.add(current_url)

                    resp = await client.get(
                        current_url,
                        headers={
                            "User-Agent": get_app_user_agent(),
                            "Accept": "text/markdown, text/html, */*",
                        },
                    )

                    if resp.status_code in (301, 302, 307, 308):
                        location = resp.headers.get("location")
                        if not location:
                            return ToolResult(data="Error: Redirect missing Location header")

                        # Resolve relative redirects
                        if not location.startswith("http"):
                            from urllib.parse import urljoin

                            location = urljoin(current_url, location)

                        if _is_permitted_redirect(url, location):
                            current_url = location
                            continue
                        else:
                            # Cross-origin redirect — inform the model
                            return ToolResult(
                                data=(
                                    f"The URL redirected to a different host.\n"
                                    f"Original: {url}\n"
                                    f"Redirect: {location} (status {resp.status_code})\n"
                                    f"Make a new WebFetch request with the redirect URL to fetch the content."
                                )
                            )

                    resp.raise_for_status()
                    content_bytes = resp.content
                    content_type = resp.headers.get("content-type", "")
                    final_url = str(resp.url)
                    break
                else:
                    return ToolResult(data=f"Error: Too many redirects (exceeded {MAX_REDIRECTS})")

            # Size check
            if len(content_bytes) > MAX_HTTP_CONTENT_LENGTH:
                return ToolResult(
                    data=f"Error: Response too large ({len(content_bytes):,} bytes, max {MAX_HTTP_CONTENT_LENGTH:,})"
                )

            # Decode content
            text_content = content_bytes.decode("utf-8", errors="replace")

            # HTML → Markdown conversion
            if "text/html" in content_type:
                text_content = _html_to_markdown(text_content)

            # Cache the result
            _url_cache.put(url, text_content, content_type)

            return await self._process_content(
                text_content, content_type, final_url, prompt, is_preapproved, **kwargs
            )

        except Exception as e:
            return ToolResult(data=f"Error fetching URL: {e}")

    async def _process_content(
        self,
        content: str,
        content_type: str,
        url: str,
        prompt: str,
        is_preapproved: bool,
        **kwargs: Any,
    ) -> ToolResult:
        """Process fetched content — optionally through secondary model."""
        # Shortcut: preapproved + markdown + small content → return raw
        if is_preapproved and "text/markdown" in content_type and len(content) < MAX_MARKDOWN_LENGTH:
            return ToolResult(data=f"URL: {url}\n\n{content}")

        # Try to use secondary model (Haiku) if available
        query_haiku = kwargs.get("query_haiku")
        if query_haiku is not None:
            try:
                secondary_prompt = _make_secondary_model_prompt(content, prompt, is_preapproved)
                model_response = await query_haiku(secondary_prompt)
                if model_response:
                    return ToolResult(data=f"URL: {url}\n\n{model_response}")
            except Exception as exc:
                logger.warning("Secondary model call failed: %s", exc)

        # Fallback: truncate and return raw content
        if len(content) > MAX_MARKDOWN_LENGTH:
            content = content[:MAX_MARKDOWN_LENGTH] + "\n\n... (content truncated)"

        return ToolResult(data=f"URL: {url}\n\nContent:\n{content}")
