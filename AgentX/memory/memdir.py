"""Memory system — CLAUDE.md and session memory management.

Integrates CLAUDE.md multi-layer loading with session memory for
a unified memory interface used by the query engine.
"""

from __future__ import annotations

import logging
from typing import Any

from AgentX.memory.session_memory import get_session_memory_for_prompt
from AgentX.utils.claudemd import (
    MemoryFileInfo,
    format_memory_files,
    get_memory_files,
    reset_memory_file_cache,
)

logger = logging.getLogger(__name__)

MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25 * 1024  # 25KB


async def load_memory(cwd: str) -> dict[str, Any]:
    """Load full memory context from all sources.

    Returns dict with:
    - memory_files: list[MemoryFileInfo] — raw parsed memory files
    - claude_md: str | None — formatted CLAUDE.md content for prompt
    - session_memory: str | None — session memory notes
    """
    memory_files = await get_memory_files(cwd=cwd)
    claude_md = format_memory_files(memory_files)
    session_memory = get_session_memory_for_prompt(cwd)

    return {
        "memory_files": memory_files,
        "claude_md": claude_md,
        "session_memory": session_memory,
    }


def format_memory_for_prompt(memory: dict[str, Any]) -> str:
    """Format loaded memory into a prompt section."""
    parts: list[str] = []

    # CLAUDE.md content
    claude_md = memory.get("claude_md")
    if claude_md:
        parts.append(claude_md)

    # Session memory
    session_memory = memory.get("session_memory")
    if session_memory:
        parts.append(session_memory)

    return "\n\n".join(parts)
