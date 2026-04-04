"""Resume a previously-running background agent — translation of resumeAgent.ts.

Key responsibilities:
  1. Read transcript + metadata from saved agent state
  2. Clean messages (orphaned thinking, unresolved tool uses)
  3. Resolve agent type (fork-worker vs. named agent)
  4. Restore worktree if applicable
  5. Re-launch via ``run_async_agent_lifecycle``
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from claude_code.data_types import Message, UserMessage
from claude_code.tools.agent_tool.constants import AGENT_TOOL_NAME
from claude_code.tools.agent_tool.definitions import BaseAgentDefinition
from claude_code.tools.agent_tool.fork import FORK_AGENT, is_in_fork_child
from claude_code.tools.agent_tool.utils import run_async_agent_lifecycle

logger = logging.getLogger(__name__)

__all__ = ["resume_agent_background"]


# ---------------------------------------------------------------------------
# Transcript-related helpers
# ---------------------------------------------------------------------------

_FORK_BOILERPLATE_TAG = "fork-boilerplate"


def _clean_transcript_messages(messages: list[dict[str, Any]]) -> list[Message]:
    """Clean a saved transcript for resumption.

    1. Strip leading/trailing whitespace from text blocks.
    2. Remove orphaned ``thinking`` blocks that precede nothing.
    3. Remove assistant messages whose tool_use blocks have no matching result.
    """
    # Build set of tool_use_ids that have a result
    result_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tid = block.get("tool_use_id", "")
                if tid:
                    result_ids.add(tid)

    cleaned: list[Message] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content")

        # Skip assistant messages with unresolved tool_use
        if role == "assistant" and isinstance(content, list):
            has_orphaned = any(
                isinstance(b, dict)
                and b.get("type") == "tool_use"
                and b.get("id", "") not in result_ids
                for b in content
            )
            if has_orphaned:
                continue

        # Skip empty messages
        if not content:
            continue

        # Reconstruct as Message (simplified: use UserMessage for user, dict for assistant)
        cleaned.append(msg)  # type: ignore[arg-type]

    return cleaned


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------


def _read_agent_metadata(metadata_path: Path) -> dict[str, Any]:
    """Read saved agent metadata JSON."""
    if not metadata_path.exists():
        return {}
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read agent metadata %s: %s", metadata_path, exc)
        return {}


def _read_agent_transcript(transcript_path: Path) -> list[dict[str, Any]]:
    """Read saved agent transcript JSON."""
    if not transcript_path.exists():
        return []
    try:
        data = json.loads(transcript_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read transcript %s: %s", transcript_path, exc)
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def resume_agent_background(
    *,
    agent_id: str,
    state_dir: Path,
    parent_engine: Any,
    agent_registry: dict[str, BaseAgentDefinition] | None = None,
) -> None:
    """Resume a background agent from saved state.

    Translation of resumeAgentBackground from resumeAgent.ts.

    Args:
        agent_id: Unique identifier for the agent being resumed.
        state_dir: Directory containing ``transcript.json`` and ``metadata.json``.
        parent_engine: The parent QueryEngine for context.
        agent_registry: Map of agent_type → AgentDefinition for lookup.
    """
    transcript_path = state_dir / "transcript.json"
    metadata_path = state_dir / "metadata.json"

    # 1. Read saved state
    raw_messages = _read_agent_transcript(transcript_path)
    metadata = _read_agent_metadata(metadata_path)

    if not raw_messages:
        logger.warning("No transcript for agent %s — cannot resume", agent_id)
        return

    # 2. Clean messages
    messages = _clean_transcript_messages(raw_messages)

    # 3. Determine agent type
    agent_type: str = metadata.get("agent_type", "")
    is_fork = metadata.get("is_fork", False)
    worktree_path = metadata.get("worktree_path")
    prompt = metadata.get("prompt", "")
    tool_use_id = metadata.get("tool_use_id", "")

    # Resolve definition
    agent_def: BaseAgentDefinition | None = None
    if is_fork:
        agent_def = FORK_AGENT
    elif agent_type and agent_registry:
        agent_def = agent_registry.get(agent_type)

    if agent_def is None and agent_type:
        logger.warning(
            "Agent type %r not found in registry, using default", agent_type
        )

    # 4. Worktree recovery
    if worktree_path:
        wp = Path(worktree_path)
        if not wp.exists():
            logger.warning(
                "Worktree %s no longer exists, running in parent cwd", worktree_path
            )
            worktree_path = None

    # 5. Append resume notice to messages
    resume_notice = UserMessage(
        content=(
            "[Agent was interrupted and is now resuming. "
            "Continue where you left off. "
            "If you were in the middle of a task, check the current state and proceed.]"
        )
    )
    messages.append(resume_notice)  # type: ignore[arg-type]

    # 6. Re-launch via async lifecycle
    logger.info("Resuming agent %s (type=%s, fork=%s)", agent_id, agent_type, is_fork)

    await run_async_agent_lifecycle(
        agent_id=agent_id,
        prompt=prompt,
        tool_use_id=tool_use_id,
        parent_engine=parent_engine,
        agent_definition=agent_def,
        is_fork=is_fork,
        worktree_path=worktree_path,
        initial_messages=messages,
    )
