"""Agent memory snapshots — translation of tools/AgentTool/agentMemorySnapshot.ts.

Manages project-scoped memory snapshots for team sharing:
  - Snapshot dir: ``<cwd>/.claude/agent-memory-snapshots/<agentType>/``
  - Synced metadata: ``<memDir>/.snapshot-synced.json``

Workflow:
  1. ``checkAgentMemorySnapshot`` → ``'none' | 'initialize' | 'prompt-update'``
  2. ``initializeFromSnapshot`` → first-time local copy
  3. ``replaceFromSnapshot`` → delete old .md → copy new from snapshot
  4. ``markSnapshotSynced`` → update synced timestamp only
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Literal

from claude_code.tools.agent_tool.memory import AgentMemoryScope, get_agent_memory_dir

logger = logging.getLogger(__name__)

__all__ = [
    "check_agent_memory_snapshot",
    "get_snapshot_dir_for_agent",
    "initialize_from_snapshot",
    "mark_snapshot_synced",
    "replace_from_snapshot",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SNAPSHOT_BASE = "agent-memory-snapshots"
SNAPSHOT_JSON = "snapshot.json"
SYNCED_JSON = ".snapshot-synced.json"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def get_snapshot_dir_for_agent(agent_type: str, *, cwd: str = "") -> str:
    """Return snapshot directory: ``<cwd>/.claude/agent-memory-snapshots/<type>/``."""
    effective_cwd = cwd or os.getcwd()
    return os.path.join(effective_cwd, ".claude", SNAPSHOT_BASE, agent_type) + os.sep


def _snapshot_json_path(agent_type: str, *, cwd: str = "") -> str:
    return os.path.join(get_snapshot_dir_for_agent(agent_type, cwd=cwd), SNAPSHOT_JSON)


def _synced_json_path(agent_type: str, scope: AgentMemoryScope, *, cwd: str = "") -> str:
    mem_dir = get_agent_memory_dir(agent_type, scope, cwd=cwd)
    return os.path.join(mem_dir, SYNCED_JSON)


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _read_json(path: str) -> dict[str, Any] | None:
    """Read and parse a JSON file, returning ``None`` on any failure."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return None


def _write_json(path: str, data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Snapshot copy
# ---------------------------------------------------------------------------


def _copy_snapshot_to_local(
    agent_type: str,
    scope: AgentMemoryScope,
    *,
    cwd: str = "",
) -> None:
    """Copy all files from the snapshot directory into the local memory directory.

    Skips ``snapshot.json`` itself.
    """
    snapshot_dir = get_snapshot_dir_for_agent(agent_type, cwd=cwd)
    mem_dir = get_agent_memory_dir(agent_type, scope, cwd=cwd)
    os.makedirs(mem_dir, exist_ok=True)

    if not os.path.isdir(snapshot_dir):
        return

    for fname in os.listdir(snapshot_dir):
        if fname == SNAPSHOT_JSON:
            continue
        src = os.path.join(snapshot_dir, fname)
        dst = os.path.join(mem_dir, fname)
        if os.path.isfile(src):
            shutil.copy2(src, dst)


def _save_synced_meta(
    agent_type: str,
    scope: AgentMemoryScope,
    snapshot_timestamp: str,
    *,
    cwd: str = "",
) -> None:
    """Write synced metadata with the snapshot timestamp."""
    path = _synced_json_path(agent_type, scope, cwd=cwd)
    _write_json(path, {"syncedFrom": snapshot_timestamp})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


SnapshotAction = Literal["none", "initialize", "prompt-update"]


def check_agent_memory_snapshot(
    agent_type: str,
    scope: AgentMemoryScope,
    *,
    cwd: str = "",
) -> tuple[SnapshotAction, str | None]:
    """Determine what action to take for the agent's memory snapshot.

    Returns:
        ``(action, snapshot_timestamp)`` where *action* is one of:

        - ``'none'`` — no snapshot exists or already in sync
        - ``'initialize'`` — local memory empty, should copy from snapshot
        - ``'prompt-update'`` — snapshot newer than local synced version
    """
    # 1. Read snapshot metadata
    meta = _read_json(_snapshot_json_path(agent_type, cwd=cwd))
    if not meta:
        return "none", None

    snapshot_ts = meta.get("updatedAt")
    if not snapshot_ts or not isinstance(snapshot_ts, str):
        return "none", None

    # 2. Check if local memory has any .md files
    mem_dir = get_agent_memory_dir(agent_type, scope, cwd=cwd)
    has_local_md = False
    if os.path.isdir(mem_dir):
        has_local_md = any(f.endswith(".md") for f in os.listdir(mem_dir))

    if not has_local_md:
        return "initialize", snapshot_ts

    # 3. Check synced metadata
    synced = _read_json(_synced_json_path(agent_type, scope, cwd=cwd))
    if not synced:
        return "prompt-update", snapshot_ts

    synced_ts = synced.get("syncedFrom")
    if not synced_ts or synced_ts != snapshot_ts:
        return "prompt-update", snapshot_ts

    return "none", None


def initialize_from_snapshot(
    agent_type: str,
    scope: AgentMemoryScope,
    snapshot_timestamp: str,
    *,
    cwd: str = "",
) -> None:
    """First-time initialization: copy snapshot into local memory."""
    _copy_snapshot_to_local(agent_type, scope, cwd=cwd)
    _save_synced_meta(agent_type, scope, snapshot_timestamp, cwd=cwd)
    logger.debug("Initialized agent memory from snapshot: %s", agent_type)


def replace_from_snapshot(
    agent_type: str,
    scope: AgentMemoryScope,
    snapshot_timestamp: str,
    *,
    cwd: str = "",
) -> None:
    """Replace local memory with snapshot (delete old .md files then copy)."""
    mem_dir = get_agent_memory_dir(agent_type, scope, cwd=cwd)

    # Delete existing .md files
    if os.path.isdir(mem_dir):
        for fname in os.listdir(mem_dir):
            if fname.endswith(".md"):
                try:
                    os.unlink(os.path.join(mem_dir, fname))
                except OSError:
                    pass

    _copy_snapshot_to_local(agent_type, scope, cwd=cwd)
    _save_synced_meta(agent_type, scope, snapshot_timestamp, cwd=cwd)
    logger.debug("Replaced agent memory from snapshot: %s", agent_type)


def mark_snapshot_synced(
    agent_type: str,
    scope: AgentMemoryScope,
    snapshot_timestamp: str,
    *,
    cwd: str = "",
) -> None:
    """Mark the current snapshot as synced without changing local memory."""
    _save_synced_meta(agent_type, scope, snapshot_timestamp, cwd=cwd)
