"""Resume background agents — translation of tools/AgentTool/resumeAgent.ts.

Provides ``resume_agent_background()`` which loads a previous agent's
transcript from disk, cleans messages (filter orphaned tool_use blocks,
whitespace-only assistant messages), reconstructs content replacement
state, and relaunches the agent via ``run_async_agent_lifecycle()``.

Handles:
  - Fork agent resume (inherits parent system prompt)
  - Worktree resume (validates directory still exists, bumps mtime)
  - Agent definition lookup (fork → type match → fallback to general)
  - Transcript loading + message cleaning (3-pass filter)
  - Content replacement state reconstruction
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, NamedTuple

from AgentX.data_types import (
    AgentModel,
    Message,
    UserMessage,
)
from AgentX.tools.agent_tool.definitions import (
    BaseAgentDefinition,
    is_built_in_agent,
)
from AgentX.tools.agent_tool.fork import FORK_AGENT
from AgentX.tools.agent_tool.run_agent import filter_incomplete_tool_calls
from AgentX.tools.agent_tool.utils import _get_task_output_path, run_async_agent_lifecycle
from AgentX.utils.history import (
    load_agent_transcript,
    read_agent_metadata,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ResumeAgentResult",
    "resume_agent_background",
]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class ResumeAgentResult(NamedTuple):
    """Return type for ``resume_agent_background``."""

    agent_id: str
    description: str
    output_file: str


# ---------------------------------------------------------------------------
# Message filtering helpers (translation of utils/messages.ts filters)
# ---------------------------------------------------------------------------




def _filter_orphaned_thinking_only_messages(messages: list[Message]) -> list[Message]:
    """Remove assistant messages that contain only thinking blocks.

    Translation of filterOrphanedThinkingOnlyMessages.
    """
    result: list[Message] = []
    for msg in messages:
        msg_type = getattr(msg, "type", None)
        if msg_type == "assistant":
            content = getattr(getattr(msg, "message", None), "content", [])
            if isinstance(content, list):
                non_thinking = [
                    b for b in content
                    if not (isinstance(b, dict) and b.get("type") == "thinking")
                ]
                if not non_thinking:
                    continue
        result.append(msg)
    return result


def _filter_whitespace_only_assistant_messages(messages: list[Message]) -> list[Message]:
    """Remove assistant messages that consist only of whitespace text blocks.

    Translation of filterWhitespaceOnlyAssistantMessages.
    """
    result: list[Message] = []
    for msg in messages:
        msg_type = getattr(msg, "type", None)
        if msg_type == "assistant":
            content = getattr(getattr(msg, "message", None), "content", [])
            if isinstance(content, list):
                has_substance = False
                for block in content:
                    if isinstance(block, dict):
                        btype = block.get("type", "")
                        if btype == "text":
                            text = block.get("text", "")
                            if text.strip():
                                has_substance = True
                                break
                        elif btype != "text":
                            has_substance = True
                            break
                    else:
                        has_substance = True
                        break
                if not has_substance:
                    continue
        result.append(msg)
    return result


# ---------------------------------------------------------------------------
# Transcript / metadata loading stubs
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Core resume function
# ---------------------------------------------------------------------------


async def resume_agent_background(
    *,
    agent_id: str,
    prompt: str,
    parent_engine: Any,
    active_agents: list[BaseAgentDefinition] | None = None,
    parent_system_prompt: str | None = None,
) -> ResumeAgentResult:
    """Resume a previously-running background agent.

    Translation of resumeAgentBackground from resumeAgent.ts.

    Steps:
      1. Load transcript + metadata
      2. Clean messages (3-pass filter)
      3. Validate worktree (if any)
      4. Resolve agent definition (fork → type → fallback)
      5. Resolve fork parent system prompt (if fork resume)
      6. Build worker tools + runAgentParams
      7. Register async agent + launch lifecycle

    Parameters
    ----------
    agent_id:
        ID of the agent to resume.
    prompt:
        Continuation prompt to append.
    parent_engine:
        Parent query engine (for config, tools, permissions).
    active_agents:
        List of active agent definitions to search for the agent type.
    parent_system_prompt:
        Pre-built system prompt for fork resume.

    Returns
    -------
    ResumeAgentResult
        The agent_id, description, and output file path.
    """
    from AgentX.tools.agent_tool.built_in import GENERAL_PURPOSE_AGENT
    from AgentX.tools.agent_tool.run_agent import run_agent

    start_time = time.time()

    # ── Step 1: Load transcript + metadata ──
    transcript, meta = await asyncio.gather(
        load_agent_transcript(agent_id),
        read_agent_metadata(agent_id),
    )
    if not transcript:
        raise RuntimeError(f"No transcript found for agent ID: {agent_id}")

    # ── Step 2: Clean messages ──
    raw_messages: list[Message] = transcript.get("messages", [])
    resumed_messages = _filter_whitespace_only_assistant_messages(
        _filter_orphaned_thinking_only_messages(
            filter_incomplete_tool_calls(raw_messages)
        )
    )

    # ── Step 3: Validate worktree ──
    resumed_worktree_path: str | None = None
    if meta and meta.get("worktreePath"):
        wt_path = meta["worktreePath"]
        if os.path.isdir(wt_path):
            resumed_worktree_path = wt_path
            # Bump mtime so stale-worktree cleanup doesn't delete a just-resumed worktree
            now = time.time()
            try:
                os.utime(wt_path, (now, now))
            except OSError:
                pass
        else:
            logger.debug(
                "Resumed worktree %s no longer exists; falling back to parent cwd",
                wt_path,
            )

    # ── Step 4: Resolve agent definition ──
    is_resumed_fork = False
    if meta and meta.get("agentType") == FORK_AGENT.agent_type:
        selected_agent: BaseAgentDefinition = FORK_AGENT
        is_resumed_fork = True
    elif meta and meta.get("agentType"):
        agent_type = meta["agentType"]
        found = None
        if active_agents:
            found = next(
                (a for a in active_agents if a.agent_type == agent_type),
                None,
            )
        selected_agent = found if found else GENERAL_PURPOSE_AGENT
    else:
        selected_agent = GENERAL_PURPOSE_AGENT

    ui_description = (meta.get("description") if meta else None) or "(resumed)"

    # ── Step 5: Fork parent system prompt ──
    fork_parent_system_prompt: str | None = None
    if is_resumed_fork:
        if parent_system_prompt:
            fork_parent_system_prompt = parent_system_prompt
        else:
            # Fallback: recompute from default + env.
            # May diverge from parent's cached bytes if state changed.
            from AgentX.constants.prompts import DEFAULT_SYSTEM_PROMPT

            fork_parent_system_prompt = DEFAULT_SYSTEM_PROMPT

        if not fork_parent_system_prompt:
            raise RuntimeError(
                "Cannot resume fork agent: unable to reconstruct parent system prompt"
            )

    # ── Step 6: Resolve model (for metadata) ──
    config = parent_engine._config
    resolved_agent_model = selected_agent.model or config.model
    if resolved_agent_model == AgentModel.INHERIT.value:
        resolved_agent_model = config.model

    # ── Step 7: Launch via run_async_agent_lifecycle ──
    metadata = {
        "prompt": prompt,
        "resolved_agent_model": resolved_agent_model,
        "is_built_in_agent": is_built_in_agent(selected_agent),
        "start_time": start_time,
        "agent_type": selected_agent.agent_type,
        "is_async": True,
    }

    # Append continuation prompt to cleaned messages
    resume_messages: list[Message] = [
        *resumed_messages,
        UserMessage(content=prompt),
    ]

    async def _make_stream(
        abort_event: asyncio.Event | None = None,
    ):
        """Create the resumed agent stream."""
        async for event in run_agent(
            prompt=prompt,
            parent_engine=parent_engine,
            agent_definition=selected_agent,
            is_async=True,
            is_fork=is_resumed_fork,
            parent_messages=resume_messages if is_resumed_fork else None,
            parent_system_prompt=fork_parent_system_prompt if is_resumed_fork else None,
            worktree_path=resumed_worktree_path,
            abort_event=abort_event,
            use_exact_tools=is_resumed_fork,
        ):
            yield event

    # Run lifecycle (fire-and-forget — caller reads output file)
    abort_event = asyncio.Event()

    async def _lifecycle():
        try:
            await run_async_agent_lifecycle(
                agent_id=agent_id,
                description=ui_description,
                make_stream=_make_stream,
                metadata=metadata,
                abort_event=abort_event,
                output_file=_get_task_output_path(agent_id),
            )
        except Exception:
            logger.error("Resume lifecycle for %s failed", agent_id, exc_info=True)

    asyncio.ensure_future(_lifecycle())

    return ResumeAgentResult(
        agent_id=agent_id,
        description=ui_description,
        output_file=_get_task_output_path(agent_id),
    )
