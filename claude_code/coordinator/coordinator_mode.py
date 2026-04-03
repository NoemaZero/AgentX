"""Coordinator mode — strict translation of coordinator/coordinatorMode.ts.

Coordinator mode turns the main agent into a task orchestrator that spawns
worker sub-agents via Agent, SendMessage, and TaskStop tools.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from claude_code.tools.tool_names import (
    AGENT_TOOL_NAME,
    SEND_MESSAGE_TOOL_NAME,
    TASK_STOP_TOOL_NAME,
)

if TYPE_CHECKING:
    from claude_code.tools.base import BaseTool

# Tools allowed in coordinator mode (matching TS COORDINATOR_MODE_ALLOWED_TOOLS)
COORDINATOR_MODE_ALLOWED_TOOLS: frozenset[str] = frozenset({
    AGENT_TOOL_NAME,
    TASK_STOP_TOOL_NAME,
    SEND_MESSAGE_TOOL_NAME,
})

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def is_coordinator_mode() -> bool:
    """Check if coordinator mode is active.

    Translation of isCoordinatorMode() from coordinatorMode.ts.
    Requires env CLAUDE_CODE_COORDINATOR_MODE=1.
    """
    val = os.environ.get("CLAUDE_CODE_COORDINATOR_MODE", "").strip()
    return val in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Tool filtering
# ---------------------------------------------------------------------------


def filter_tools_for_coordinator(tools: list[BaseTool]) -> list[BaseTool]:
    """Filter tools to only those allowed in coordinator mode.

    Translation of applyCoordinatorToolFilter() from toolPool.ts.
    """
    return [t for t in tools if t.name in COORDINATOR_MODE_ALLOWED_TOOLS]


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def get_coordinator_system_prompt(
    worker_tool_names: list[str] | None = None,
) -> str:
    """Return the coordinator-specific system prompt.

    Translation of getCoordinatorSystemPrompt() from coordinatorMode.ts.
    """
    worker_tools_section = ""
    if worker_tool_names:
        formatted = ", ".join(worker_tool_names)
        worker_tools_section = f"\n\nWorkers have access to these tools: {formatted}"

    return f"""\
You are a coordinator. Your job is to direct workers, synthesize their results, \
and communicate with the user.

## Available Tools
- **Agent**: Launch a new worker sub-agent for a specific task
- **SendMessage**: Send a follow-up message to a running worker
- **TaskStop**: Stop a running worker task

## Workflow
1. **Research**: Spawn read-only workers to investigate the codebase
2. **Synthesis**: Combine findings into a clear plan
3. **Implementation**: Spawn workers to make changes (serialize writes to the same file)
4. **Verification**: Spawn workers to validate changes

## Concurrency Strategy
- Read-only tasks (search, read, analyze) → launch in **parallel**
- Write tasks to the **same file** → serialize (wait for one to finish before starting the next)
- Write tasks to **different files** → can run in parallel
- Verification tasks → can run alongside implementation in different file areas

## Prompt-Writing Rules
Workers start fresh — they have **no access** to this conversation.
1. Make every prompt **self-contained**: include file paths, line numbers, and enough context.
2. **Never delegate understanding** — don't say "based on your findings, fix the bug."
   Synthesize what you've learned and provide concrete instructions.
3. Include the **why** so workers can make judgment calls.

## When to Continue vs. Spawn
- **High context overlap with an existing worker** → use SendMessage to continue
- **Low overlap / new area** → spawn a new Agent
{worker_tools_section}

## Worker Result Format
When a worker completes, you will receive a task notification containing its summary and result.
Synthesize results before reporting to the user.
"""


def get_coordinator_user_context(
    available_tools: list[BaseTool] | None = None,
) -> str:
    """Generate dynamic user-context describing worker capabilities.

    Translation of getCoordinatorUserContext() from coordinatorMode.ts.
    """
    if not available_tools:
        return ""

    tool_names = [t.name for t in available_tools if t.name not in COORDINATOR_MODE_ALLOWED_TOOLS]
    if not tool_names:
        return ""

    formatted = ", ".join(tool_names)
    return f"Workers have these tools available: {formatted}"
