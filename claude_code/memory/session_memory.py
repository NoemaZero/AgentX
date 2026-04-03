"""Session memory — strict translation of services/SessionMemory/.

Provides persistent session-level memory that survives across conversation turns
and is integrated with the compact system. Memory is stored as markdown files
at ~/.claude/session-memory/.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path

from claude_code.pydantic_models import FrozenModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSION_MEMORY_DIR = os.path.join(os.path.expanduser("~"), ".claude", "session-memory")
TEMPLATE_DIR = os.path.join(SESSION_MEMORY_DIR, "config")
TEMPLATE_PATH = os.path.join(TEMPLATE_DIR, "template.md")

MAX_SECTION_TOKENS = 2000
MAX_TOTAL_TOKENS = 12000

# ---------------------------------------------------------------------------
# Default template — verbatim from TS services/SessionMemory/prompts.ts
# ---------------------------------------------------------------------------

DEFAULT_TEMPLATE = """\
# Session Title
_A short and distinctive 5-10 word descriptive title for the session. Super info dense, no filler_

# Current State
_What is actively being worked on right now? Pending tasks not yet completed. Immediate next steps._

# Task specification
_What did the user ask to build? Any design decisions or other explanatory context_

# Files and Functions
_What are the important files? In short, what do they contain and why are they relevant?_

# Workflow
_What bash commands are usually run and in what order? How to interpret their output if not obvious?_

# Errors & Corrections
_Errors encountered and how they were fixed. What did the user correct? What approaches failed and should not be tried again?_

# Codebase and System Documentation
_What are the important system components? How do they work/fit together?_

# Learnings
_What has worked well? What has not? What to avoid? Do not duplicate items from other sections_

# Key results
_If the user asked a specific output such as an answer to a question, a table, or other document, repeat the exact result here_

# Worklog
_Step by step, what was attempted, done? Very terse summary for each step_
"""

# ---------------------------------------------------------------------------
# Update prompt — used by fork agent to update session notes
# ---------------------------------------------------------------------------

SESSION_MEMORY_UPDATE_PROMPT = """\
You are a session memory updater. Your job is to maintain structured notes about
the current coding session.

## Current Notes
{current_notes}

## Instructions
Update the notes file at `{notes_path}` to reflect the latest conversation.

Rules:
- Keep each section under {max_section_tokens} tokens
- Total file should stay under {max_total_tokens} tokens
- Be factual and terse — no filler, no speculation
- Preserve section order from the template
- Merge new information, don't just append
- If a section has no relevant content yet, keep the placeholder text
- Write using the Edit tool to update the notes file
"""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class SessionMemoryState(FrozenModel):
    """Current state of a session's memory."""

    session_id: str
    notes_path: str
    template: str = DEFAULT_TEMPLATE
    last_updated: float = 0.0
    update_count: int = 0


# ---------------------------------------------------------------------------
# Session memory manager
# ---------------------------------------------------------------------------


def _get_session_id(cwd: str) -> str:
    """Generate a stable session ID from CWD (like TS getSessionId)."""
    # Hash the CWD path to create a stable, filesystem-safe ID
    return hashlib.sha256(cwd.encode()).hexdigest()[:16]


def _ensure_dir(path: str) -> None:
    """Create directory if not exists."""
    os.makedirs(path, exist_ok=True)


def get_template() -> str:
    """Load user-customizable template, falling back to default."""
    if os.path.isfile(TEMPLATE_PATH):
        try:
            content = Path(TEMPLATE_PATH).read_text(encoding="utf-8").strip()
            if content:
                return content
        except OSError:
            pass
    return DEFAULT_TEMPLATE


def get_session_memory_path(session_id: str) -> str:
    """Get the file path for a session's memory notes."""
    return os.path.join(SESSION_MEMORY_DIR, f"{session_id}.md")


def init_session_memory(cwd: str) -> SessionMemoryState:
    """Initialize session memory for a working directory."""
    session_id = _get_session_id(cwd)
    notes_path = get_session_memory_path(session_id)
    template = get_template()

    # Create session memory dir
    _ensure_dir(SESSION_MEMORY_DIR)

    # Create notes file from template if it doesn't exist
    if not os.path.isfile(notes_path):
        _ensure_dir(os.path.dirname(notes_path))
        Path(notes_path).write_text(template, encoding="utf-8")

    return SessionMemoryState(
        session_id=session_id,
        notes_path=notes_path,
        template=template,
    )


def read_session_notes(state: SessionMemoryState) -> str:
    """Read current session notes."""
    if os.path.isfile(state.notes_path):
        try:
            return Path(state.notes_path).read_text(encoding="utf-8")
        except OSError:
            pass
    return state.template


def write_session_notes(state: SessionMemoryState, content: str) -> SessionMemoryState:
    """Write session notes (returns new state — immutable pattern)."""
    _ensure_dir(os.path.dirname(state.notes_path))
    Path(state.notes_path).write_text(content, encoding="utf-8")

    return SessionMemoryState(
        session_id=state.session_id,
        notes_path=state.notes_path,
        template=state.template,
        last_updated=time.time(),
        update_count=state.update_count + 1,
    )


def build_update_prompt(state: SessionMemoryState) -> str:
    """Build the prompt for the session memory update agent."""
    current_notes = read_session_notes(state)
    return SESSION_MEMORY_UPDATE_PROMPT.format(
        current_notes=current_notes,
        notes_path=state.notes_path,
        max_section_tokens=MAX_SECTION_TOKENS,
        max_total_tokens=MAX_TOTAL_TOKENS,
    )


def get_session_memory_for_prompt(cwd: str) -> str | None:
    """Load session memory notes for inclusion in system prompt.

    Returns formatted notes or None if no session memory exists.
    """
    session_id = _get_session_id(cwd)
    notes_path = get_session_memory_path(session_id)

    if not os.path.isfile(notes_path):
        return None

    try:
        content = Path(notes_path).read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if not content or content == DEFAULT_TEMPLATE.strip():
        return None

    return f"# Session Memory\n\n{content}"


def ensure_template_dir() -> None:
    """Create template directory and default template if not exists."""
    _ensure_dir(TEMPLATE_DIR)
    if not os.path.isfile(TEMPLATE_PATH):
        Path(TEMPLATE_PATH).write_text(DEFAULT_TEMPLATE, encoding="utf-8")
