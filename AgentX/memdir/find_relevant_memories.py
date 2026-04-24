"""Find memory files relevant to a query.

Translation of findRelevantMemories.ts. Scans memory file headers and asks a
side-query LLM to select the most relevant ones.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from AgentX.config import Config
from AgentX.constants.identity import get_app_help_name
from AgentX.memdir.memory_scan import (
    MemoryHeader,
    format_memory_manifest,
    scan_memory_files,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_APP = get_app_help_name()

SELECT_MEMORIES_SYSTEM_PROMPT = (
    f"You are selecting memories that will be useful to {_APP} as it "
    "processes a user's query. You will be given the user's query and a list "
    "of available memory files with their filenames and descriptions.\n\n"
    "Return a list of filenames for the memories that will clearly be useful "
    f"to {_APP} as it processes the user's query (up to 5). Only include "
    "memories that you are certain will be helpful based on their name and "
    "description.\n"
    "- If you are unsure if a memory will be useful in processing the user's "
    "query, then do not include it in your list. Be selective and discerning.\n"
    "- If there are no memories in the list that would clearly be useful, "
    "feel free to return an empty list.\n"
    "- If a list of recently-used tools is provided, do not select memories "
    "that are usage reference or API documentation for those tools ({_APP} "
    "is already exercising them). DO still select memories containing "
    "warnings, gotchas, or known issues about those tools -- active use is "
    "exactly when those matter.\n"
)

MAX_RELEVANT_MEMORIES = 5
MAX_SELCTION_TOKENS = 256


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class RelevantMemory:
    """A memory file selected as relevant to a query."""

    path: str  # absolute filepath
    mtime_ms: float


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def find_relevant_memories(
    query: str,
    memory_dir: str,
    config: Config | None = None,
    *,
    recent_tools: list[str] | None = None,
    already_surfaced: set[str] | None = None,
) -> list[RelevantMemory]:
    """Find memory files relevant to a query by scanning memory file headers
    and asking an LLM side-query to select the most relevant ones.

    Returns absolute file paths + mtime of the most relevant memories
    (up to MAX_RELEVANT_MEMORIES). Excludes MEMORY.md.

    ``already_surfaced`` filters paths shown in prior turns before the
    selection call, so the selector spends its budget on fresh candidates.
    """
    recent_tools = recent_tools or []
    already_surfaced = already_surfaced or set()

    memories = [
        m for m in await scan_memory_files(memory_dir)
        if m.filename not in already_surfaced
    ]
    if not memories:
        return []

    selected_filenames = await _select_relevant_memories(
        query, memories, config, recent_tools,
    )
    by_filename = {m.filename: m for m in memories}
    selected = [by_filename[f] for f in selected_filenames if f in by_filename]

    return [
        RelevantMemory(path=m.filepath, mtime_ms=m.mtime_ms)
        for m in selected
    ]


# ---------------------------------------------------------------------------
# Internal — LLM side-query
# ---------------------------------------------------------------------------


async def _select_relevant_memories(
    query: str,
    memories: list[MemoryHeader],
    config: Config | None,
    recent_tools: list[str],
) -> list[str]:
    valid_filenames = {m.filename for m in memories}
    manifest = format_memory_manifest(memories)

    tools_section = (
        f"\n\nRecently used tools: {', '.join(recent_tools)}"
        if recent_tools
        else ""
    )

    try:
        result = await _run_side_query(
            query=f"Query: {query}\n\nAvailable memories:\n{manifest}{tools_section}",
            system=SELECT_MEMORIES_SYSTEM_PROMPT,
            config=config,
        )

        selected = _parse_selection_result(result, valid_filenames)
        return selected

    except Exception:
        logger.warning(
            "[memdir] select_relevant_memories failed", exc_info=True,
        )
        return []


def _parse_selection_result(result: str, valid: set[str]) -> list[str]:
    """Parse the JSON response and filter to valid filenames."""
    try:
        parsed = json.loads(result)
        selected = parsed.get("selected_memories", [])
        if not isinstance(selected, list):
            return []
        return [f for f in selected if isinstance(f, str) and f in valid]
    except (json.JSONDecodeError, TypeError):
        return []


async def _run_side_query(
    query: str,
    system: str,
    config: Config | None,
) -> str:
    """Run a lightweight side-query via the available LLM provider.

    This is a synchronous-style helper that the caller can integrate with
    their LLM infrastructure. Uses the same config as the main session.
    """
    from AgentX.services.llm import build_provider
    from AgentX.services.llm.types import Messages

    api_key = config.api_key if config else ""
    base_url = config.base_url if config else ""
    model = config.model if config else "deepseek-chat"
    provider_str = config.provider if config else "deepseek"

    messages: Messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": query},
    ]

    provider = build_provider(str(provider_str), api_key=api_key, base_url=base_url)

    response = await provider.invoke(
        model=model,
        messages=messages,
        temperature=0.0,
        max_tokens=MAX_SELCTION_TOKENS,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "memory_selection",
                "schema": {
                    "type": "object",
                    "properties": {
                        "selected_memories": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["selected_memories"],
                    "additionalProperties": False,
                },
            },
        },
    )

    return response.answer if hasattr(response, "answer") else response.thinking if hasattr(response, "thinking") else str(response)
