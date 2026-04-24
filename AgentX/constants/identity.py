"""Application identity — loaded from environment variables for extensibility.

All "Claude Code" hardcoded references in prompts are replaced with values
from this module so the project can be rebranded without touching prompt text.

Environment variables (all optional, with sensible defaults):
  AGENTX_APP_NAME         — Application/product name (default: "AgentX")
  AGENTX_APP_COMPANY      — Company/creator name (default: ""; was "Anthropic")
  AGENTX_APP_DISPLAY_NAME — Full display name for UI banners (default: same as APP_NAME)
  AGENTX_APP_ISSUES_URL   — URL for reporting issues/feedback
  AGENTX_APP_DOCS_URL     — URL for documentation
  AGENTX_APP_USER_AGENT   — HTTP User-Agent header value
  AGENTX_APP_HELP_NAME    — Name shown in /help text (default: same as APP_NAME)
  AGENTX_APP_PACKAGE_NAME — Pip/package name (default: "AgentX")
  AGENTX_HELP_AGENT_TYPE  — Agent type name for the help/guide agent (default: "AgentX-guide")
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Load from environment (module-level, once at import)
# ---------------------------------------------------------------------------

_APP_NAME = os.environ.get("AGENTX_APP_NAME", "AgentX")
_APP_COMPANY = os.environ.get("AGENTX_APP_COMPANY", "")
_APP_DISPLAY_NAME = os.environ.get("AGENTX_APP_DISPLAY_NAME", "") or _APP_NAME
_APP_ISSUES_URL = os.environ.get("AGENTX_APP_ISSUES_URL", "https://github.com/NoemaZero/AgentX/issues")
_APP_DOCS_URL = os.environ.get("AGENTX_APP_DOCS_URL", "")
_APP_USER_AGENT = os.environ.get("AGENTX_APP_USER_AGENT", "") or f"{_APP_NAME}/0.1"
_APP_HELP_NAME = os.environ.get("AGENTX_APP_HELP_NAME", "") or _APP_NAME
_APP_PACKAGE_NAME = os.environ.get("AGENTX_APP_PACKAGE_NAME", "") or _APP_NAME
_HELP_AGENT_TYPE = os.environ.get("AGENTX_HELP_AGENT_TYPE", "") or f"{_APP_NAME}-guide"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_app_name() -> str:
    """Return the application/product name (e.g. 'AgentX')."""
    return _APP_NAME


def get_app_display_name() -> str:
    """Return the full display name for UI banners."""
    return _APP_DISPLAY_NAME


def get_app_issues_url() -> str:
    """Return the issues/feedback URL."""
    return _APP_ISSUES_URL


def get_app_docs_url() -> str:
    """Return the documentation URL."""
    return _APP_DOCS_URL


def get_app_user_agent() -> str:
    """Return the HTTP User-Agent header value."""
    return _APP_USER_AGENT


def get_app_help_name() -> str:
    """Return the name shown in /help text and help references."""
    return _APP_HELP_NAME


def get_app_package_name() -> str:
    """Return the pip/package name for version output."""
    return _APP_PACKAGE_NAME


def get_help_agent_type() -> str:
    """Return the agent type name for the help/guide agent."""
    return _HELP_AGENT_TYPE


# ---------------------------------------------------------------------------
# Prompt fragments
# ---------------------------------------------------------------------------


def get_agent_intro() -> str:
    """'You are an agent for X' — used in DEFAULT_AGENT_PROMPT and agent tool."""
    if _APP_COMPANY:
        return f"You are an agent for {_APP_NAME}, {_APP_COMPANY}'s official CLI."
    return f"You are an agent for {_APP_NAME}."


def get_typed_agent_intro(article_type: str) -> str:
    """'You are {article_type} agent for X' — used in built-in agent system prompts.

    Args:
        article_type: Full phrase including article, e.g. "an Explore", "a Plan".
    """
    if _APP_COMPANY:
        return (
            f"You are {article_type} agent for {_APP_NAME}, "
            f"{_APP_COMPANY}'s official CLI."
        )
    return f"You are {article_type} agent for {_APP_NAME}."


def get_help_text() -> str:
    """Return the /help guidance text."""
    return (
        f"/help: Get help with using {_APP_HELP_NAME}\n"
        f"To give feedback, users should report issues at {_APP_ISSUES_URL}"
    )
