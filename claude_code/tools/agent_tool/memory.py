"""Agent memory system — translation of tools/AgentTool/agentMemory.ts.

Supports three memory scopes:
  - user:    ~/.claude/agent-memory/<agentType>/
  - project: <cwd>/.claude/agent-memory/<agentType>/
  - local:   <cwd>/.claude/agent-memory-local/<agentType>/
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
    "is_agent_memory_path",
    "load_agent_memory_prompt",
]


class AgentMemoryScope(StrEnum):
    """Persistent agent memory scope."""

    USER = "user"
    PROJECT = "project"
    LOCAL = "local"


def _sanitize_agent_type(agent_type: str) -> str:
    """Sanitize agent type name for use as a directory name.

    Replaces colons (invalid on Windows, used in plugin-namespaced agent
    types like ``my-plugin:my-agent``) with dashes.
    """
    return agent_type.replace(":", "-")


def get_agent_memory_dir(agent_type: str, scope: AgentMemoryScope, *, cwd: str = "") -> str:
    """Return the agent memory directory for a given agent type and scope."""
    dir_name = _sanitize_agent_type(agent_type)
    effective_cwd = cwd or os.getcwd()

    if scope is AgentMemoryScope.USER:
        return os.path.join(Path.home(), ".claude", "agent-memory", dir_name) + os.sep
    if scope is AgentMemoryScope.PROJECT:
        return os.path.join(effective_cwd, ".claude", "agent-memory", dir_name) + os.sep
    # LOCAL
    return os.path.join(effective_cwd, ".claude", "agent-memory-local", dir_name) + os.sep


def get_agent_memory_entrypoint(agent_type: str, scope: AgentMemoryScope, *, cwd: str = "") -> str:
    """Return the primary memory file path for an agent."""
    return os.path.join(get_agent_memory_dir(agent_type, scope, cwd=cwd), "MEMORY.md")


def is_agent_memory_path(absolute_path: str, *, cwd: str = "") -> bool:
    """Check if a file is within any agent memory directory."""
    normalized = os.path.normpath(absolute_path)
    effective_cwd = cwd or os.getcwd()

    # User scope
    user_base = os.path.join(Path.home(), ".claude", "agent-memory") + os.sep
    if normalized.startswith(user_base):
        return True

    # Project scope
    project_base = os.path.join(effective_cwd, ".claude", "agent-memory") + os.sep
    if normalized.startswith(project_base):
        return True

    # Local scope
    local_base = os.path.join(effective_cwd, ".claude", "agent-memory-local") + os.sep
    if normalized.startswith(local_base):
        return True

    return False


def _ensure_memory_dir(memory_dir: str) -> None:
    """Create memory directory if it doesn't exist (fire-and-forget)."""
    try:
        os.makedirs(memory_dir, exist_ok=True)
    except OSError as exc:
        logger.debug("Failed to create memory dir %s: %s", memory_dir, exc)


def _build_memory_prompt(
    *,
    display_name: str,
    memory_dir: str,
    extra_guidelines: list[str] | None = None,
) -> str:
    """Build a structured memory prompt from the memory directory contents."""
    parts: list[str] = [f"## {display_name}\n"]

    if extra_guidelines:
        parts.append("Guidelines:")
        for g in extra_guidelines:
            parts.append(f"  {g}")
        parts.append("")

    parts.append(f"Memory directory: {memory_dir}\n")

    # Read all .md files in the memory directory
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


def load_agent_memory_prompt(agent_type: str, scope: AgentMemoryScope, *, cwd: str = "") -> str:
    """Load persistent memory for an agent.

    Creates the memory directory if needed and returns a prompt with memory contents.
    """
    scope_notes = {
        AgentMemoryScope.USER: (
            "- Since this memory is user-scope, keep learnings general "
            "since they apply across all projects"
        ),
        AgentMemoryScope.PROJECT: (
            "- Since this memory is project-scope and shared with your team "
            "via version control, tailor your memories to this project"
        ),
        AgentMemoryScope.LOCAL: (
            "- Since this memory is local-scope (not checked into version control), "
            "tailor your memories to this project and machine"
        ),
    }

    memory_dir = get_agent_memory_dir(agent_type, scope, cwd=cwd)

    # Fire-and-forget: create dir so agent can write to it
    _ensure_memory_dir(memory_dir)

    return _build_memory_prompt(
        display_name="Persistent Agent Memory",
        memory_dir=memory_dir,
        extra_guidelines=[scope_notes[scope]],
    )
