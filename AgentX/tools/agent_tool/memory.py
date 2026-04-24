"""Agent memory system — translation of tools/AgentTool/agentMemory.ts.

Supports three memory scopes:
  - user:    ~/.agentx/agent-memory/<agentType>/
  - project: <cwd>/.agentx/agent-memory/<agentType>/
  - local:   <cwd>/.agentx/agent-memory-local/<agentType>/
             (or $CLAUDE_CODE_REMOTE_MEMORY_DIR/projects/<gitRoot>/agent-memory-local/ )
"""

from __future__ import annotations

import logging
import os
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "AgentMemoryScope",
    "get_agent_memory_dir",
    "get_agent_memory_entrypoint",
    "get_memory_scope_display",
    "is_agent_memory_path",
    "load_agent_memory_prompt",
]


class AgentMemoryScope(StrEnum):
    """Persistent agent memory scope."""

    USER = "user"
    PROJECT = "project"
    LOCAL = "local"


def _sanitize_agent_type(agent_type: str) -> str:
    """Sanitize agent type for directory name — replace ``:`` with ``-``."""
    return agent_type.replace(":", "-")


def _find_canonical_git_root(cwd: str) -> str | None:
    """Walk up from *cwd* looking for a ``.git`` directory."""
    current = Path(cwd).resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return str(current)
        current = current.parent
    return None


def _get_local_agent_memory_dir(dir_name: str, *, cwd: str = "") -> str:
    """Local-scope memory dir — supports ``CLAUDE_CODE_REMOTE_MEMORY_DIR``."""
    effective_cwd = cwd or os.getcwd()
    remote_dir = os.environ.get("CLAUDE_CODE_REMOTE_MEMORY_DIR")

    if remote_dir:
        git_root = _find_canonical_git_root(effective_cwd)
        if git_root:
            sanitized = git_root.replace(os.sep, "_").strip("_")
            return os.path.join(
                remote_dir, "projects", sanitized,
                "agent-memory-local", dir_name,
            ) + os.sep

    return os.path.join(
        effective_cwd, ".agentx", "agent-memory-local", dir_name,
    ) + os.sep


def _memory_base_dir() -> str:
    return os.path.join(str(Path.home()), ".agentx")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_agent_memory_dir(
    agent_type: str,
    scope: AgentMemoryScope,
    *,
    cwd: str = "",
) -> str:
    """Return the agent memory directory for *agent_type* and *scope*."""
    dir_name = _sanitize_agent_type(agent_type)
    effective_cwd = cwd or os.getcwd()

    if scope is AgentMemoryScope.USER:
        return os.path.join(_memory_base_dir(), "agent-memory", dir_name) + os.sep
    if scope is AgentMemoryScope.PROJECT:
        return os.path.join(effective_cwd, ".agentx", "agent-memory", dir_name) + os.sep
    return _get_local_agent_memory_dir(dir_name, cwd=effective_cwd)


def get_agent_memory_entrypoint(
    agent_type: str,
    scope: AgentMemoryScope,
    *,
    cwd: str = "",
) -> str:
    """Return the ``MEMORY.md`` path for an agent."""
    return os.path.join(get_agent_memory_dir(agent_type, scope, cwd=cwd), "MEMORY.md")


def is_agent_memory_path(absolute_path: str, *, cwd: str = "") -> bool:
    """Check if *absolute_path* is within any agent memory directory.

    Normalises paths to prevent traversal attacks.
    """
    normalized = os.path.normpath(absolute_path)
    effective_cwd = cwd or os.getcwd()

    for base in (
        os.path.normpath(os.path.join(_memory_base_dir(), "agent-memory")) + os.sep,
        os.path.normpath(os.path.join(effective_cwd, ".agentx", "agent-memory")) + os.sep,
        os.path.normpath(os.path.join(effective_cwd, ".agentx", "agent-memory-local")) + os.sep,
    ):
        if normalized.startswith(base):
            return True

    remote_dir = os.environ.get("CLAUDE_CODE_REMOTE_MEMORY_DIR")
    if remote_dir and normalized.startswith(os.path.normpath(remote_dir) + os.sep):
        return True

    return False


def get_memory_scope_display(memory: AgentMemoryScope) -> str:
    """Human-readable label for the memory scope."""
    return {
        AgentMemoryScope.USER: "user (global)",
        AgentMemoryScope.PROJECT: "project (shared via VCS)",
        AgentMemoryScope.LOCAL: "local (machine-specific)",
    }.get(memory, str(memory))


# ---------------------------------------------------------------------------
# Memory prompt building
# ---------------------------------------------------------------------------


def _ensure_memory_dir(memory_dir: str) -> None:
    try:
        os.makedirs(memory_dir, exist_ok=True)
    except OSError as exc:
        logger.debug("Failed to create memory dir %s: %s", memory_dir, exc)


def _build_memory_prompt(
    *,
    display_name: str,
    memory_dir: str,
    scope_note: str = "",
    extra_guidelines: str = "",
) -> str:
    parts: list[str] = [f"## {display_name}\n"]
    if scope_note:
        parts.append(f"Scope guidance:\n{scope_note}\n")
    if extra_guidelines:
        parts.append(f"Extra guidelines:\n{extra_guidelines}\n")
    parts.append(f"Memory directory: {memory_dir}\n")

    if os.path.isdir(memory_dir):
        for fname in sorted(os.listdir(memory_dir)):
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(memory_dir, fname)
            if not os.path.isfile(fpath):
                continue
            try:
                with open(fpath, encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    parts.append(f"### {fname}\n{content}\n")
            except OSError:
                pass

    return "\n".join(parts)


def load_agent_memory_prompt(
    agent_type: str,
    scope: AgentMemoryScope,
    *,
    cwd: str = "",
) -> str:
    """Load persistent memory and return a prompt with all markdown contents."""
    scope_notes = {
        AgentMemoryScope.USER: (
            "Since this memory is user-scope, keep learnings general "
            "since they apply across all projects"
        ),
        AgentMemoryScope.PROJECT: (
            "Since this memory is project-scope and shared with your team "
            "via version control, tailor your memories to this project"
        ),
        AgentMemoryScope.LOCAL: (
            "Since this memory is local-scope (not checked into version control), "
            "tailor your memories to this project and machine"
        ),
    }
    memory_dir = get_agent_memory_dir(agent_type, scope, cwd=cwd)
    _ensure_memory_dir(memory_dir)
    extra = os.environ.get("CLAUDE_COWORK_MEMORY_EXTRA_GUIDELINES", "")
    return _build_memory_prompt(
        display_name="Persistent Agent Memory",
        memory_dir=memory_dir,
        scope_note=scope_notes[scope],
        extra_guidelines=extra,
    )
