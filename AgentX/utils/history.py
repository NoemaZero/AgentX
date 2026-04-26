"""Session history & persistence — strict translation of history.ts + sessionStorage.ts.

Two subsystems:
1. Prompt history   — JSONL at ~/.agentx/history.jsonl (up-arrow / Ctrl+R)
2. Session storage  — JSONL at ~/.agentx/projects/<sanitized-cwd>/<session-id>.jsonl

Translation covers: LogEntry, read/write, dedup, resume, sanitizePath.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import Field

from AgentX.data_types import (
    AssistantMessage,
    Message,
    ToolResultMessage,
    UserMessage,
)
from AgentX.pydantic_models import FrozenModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_HISTORY_ITEMS = 100
CLAUDE_DIR = Path.home() / ".agentx"
HISTORY_FILE = CLAUDE_DIR / "history.jsonl"
PROJECTS_DIR = CLAUDE_DIR / "projects"


# ---------------------------------------------------------------------------
# Prompt history (up-arrow / Ctrl+R)
# ---------------------------------------------------------------------------
class PromptLogEntry(FrozenModel):
    """One line of prompt history — translation of LogEntry."""

    display: str
    timestamp: float
    project: str = ""
    session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "display": self.display,
            "timestamp": self.timestamp,
            "project": self.project,
            "sessionId": self.session_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PromptLogEntry:
        return cls(
            display=d.get("display", ""),
            timestamp=d.get("timestamp", 0.0),
            project=d.get("project", ""),
            session_id=d.get("sessionId", ""),
        )


def _ensure_history_dir() -> None:
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)


def read_prompt_history(session_id: str | None = None) -> list[PromptLogEntry]:
    """Read prompt history from JSONL file.

    Returns entries with current session first, then others (deduped by display).
    """
    if not HISTORY_FILE.exists():
        return []

    entries: list[PromptLogEntry] = []
    try:
        with HISTORY_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(PromptLogEntry.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError):
                    continue
    except OSError:
        return []

    # Dedup by display text, keeping latest
    seen: dict[str, PromptLogEntry] = {}
    for entry in reversed(entries):
        if entry.display not in seen:
            seen[entry.display] = entry
    deduped = list(reversed(seen.values()))

    # Sort: current session first
    if session_id:
        session_entries = [e for e in deduped if e.session_id == session_id]
        other_entries = [e for e in deduped if e.session_id != session_id]
        return (session_entries + other_entries)[-MAX_HISTORY_ITEMS:]

    return deduped[-MAX_HISTORY_ITEMS:]


def write_prompt_history(entry: PromptLogEntry) -> None:
    """Append a prompt history entry."""
    _ensure_history_dir()
    try:
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict()) + "\n")
    except OSError as exc:
        logger.warning("Failed to write prompt history: %s", exc)


# ---------------------------------------------------------------------------
# Session storage (transcript persistence)
# ---------------------------------------------------------------------------
def sanitize_path(path: str) -> str:
    """Convert a filesystem path to a safe directory name.

    Translation of sanitizePath() from sessionStorage.ts.
    """
    # Replace path separators and special chars
    safe = re.sub(r"[/\\:*?\"<>|]", "_", path)
    safe = safe.strip("_")
    # Truncate if too long, append hash
    if len(safe) > 200:
        h = hashlib.sha256(path.encode()).hexdigest()[:8]
        safe = safe[:192] + "_" + h
    return safe


def get_session_dir(cwd: str) -> Path:
    """Get the session storage directory for a given working directory."""
    return PROJECTS_DIR / sanitize_path(cwd)


def generate_session_id() -> str:
    """Generate a new session ID."""
    return str(uuid.uuid4())


class SessionEntry(FrozenModel):
    """One entry in the session transcript."""

    entry_type: str  # 'user' | 'assistant' | 'tool_result' | 'system' | 'custom-title' | 'ai-title' | ...
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = 0.0
    parent_uuid: str = ""
    session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.entry_type,
            "data": self.data,
            "timestamp": self.timestamp or time.time(),
            "parentUuid": self.parent_uuid,
            "sessionId": self.session_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SessionEntry:
        return cls(
            entry_type=d.get("type", ""),
            data=d.get("data", {}),
            timestamp=d.get("timestamp", 0.0),
            parent_uuid=d.get("parentUuid", ""),
            session_id=d.get("sessionId", ""),
        )


class SessionStorage:
    """Manages session transcript persistence.

    Translation of sessionStorage.ts Project class.
    """

    def __init__(self, cwd: str, session_id: str | None = None) -> None:
        self.cwd = cwd
        self.session_id = session_id or generate_session_id()
        self._session_dir = get_session_dir(cwd)
        self._session_file = self._session_dir / f"{self.session_id}.jsonl"
        self._pending: list[SessionEntry] = []
        self._materialized = False

    @property
    def session_file(self) -> Path:
        return self._session_file

    def append_message(self, msg: Message) -> None:
        """Append a message to the session transcript."""
        if isinstance(msg, UserMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            self._pending.append(SessionEntry(
                entry_type="user",
                data={"content": content},
                session_id=self.session_id,
            ))
        elif isinstance(msg, AssistantMessage):
            data: dict[str, Any] = {
                "content": msg.content or "",
                "tool_calls": msg.tool_calls,
            }
            if msg.reasoning_content:
                data["reasoning_content"] = msg.reasoning_content
            self._pending.append(SessionEntry(
                entry_type="assistant",
                data=data,
                session_id=self.session_id,
            ))
        elif isinstance(msg, ToolResultMessage):
            self._pending.append(SessionEntry(
                entry_type="tool_result",
                data={
                    "tool_call_id": msg.tool_call_id,
                    "name": msg.name,
                    "duration_ms": msg.duration_ms,
                    "content": msg.content[:5000],  # Truncate large results
                },
                session_id=self.session_id,
            ))

    def set_title(self, title: str, is_ai: bool = True) -> None:
        """Set session title."""
        entry_type = "ai-title" if is_ai else "custom-title"
        self._pending.append(SessionEntry(
            entry_type=entry_type,
            data={"title": title},
            session_id=self.session_id,
        ))

    def set_mode(self, mode: str) -> None:
        """Record session mode (coordinator | normal)."""
        self._pending.append(SessionEntry(
            entry_type="mode",
            data={"mode": mode},
            session_id=self.session_id,
        ))

    def flush(self) -> None:
        """Write pending entries to disk."""
        if not self._pending:
            return

        if not self._materialized:
            self._session_dir.mkdir(parents=True, exist_ok=True)
            self._materialized = True

        try:
            with self._session_file.open("a", encoding="utf-8") as f:
                for entry in self._pending:
                    f.write(json.dumps(entry.to_dict()) + "\n")
            self._pending.clear()
        except OSError as exc:
            logger.warning("Failed to flush session: %s", exc)

    def load_transcript(self) -> list[SessionEntry]:
        """Load all entries from the session transcript file."""
        if not self._session_file.exists():
            return []

        entries: list[SessionEntry] = []
        try:
            with self._session_file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(SessionEntry.from_dict(json.loads(line)))
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError:
            pass
        return entries

    def rebuild_messages(self) -> list[Message]:
        """Rebuild the message list from the session transcript.

        Used for session resume.
        """
        entries = self.load_transcript()
        messages: list[Message] = []

        for entry in entries:
            if entry.entry_type == "user":
                messages.append(UserMessage(content=entry.data.get("content", "")))
            elif entry.entry_type == "assistant":
                messages.append(AssistantMessage(
                    content=entry.data.get("content", ""),
                    tool_calls=entry.data.get("tool_calls", []),
                    reasoning_content=entry.data.get("reasoning_content"),
                ))
            elif entry.entry_type == "tool_result":
                messages.append(ToolResultMessage(
                    tool_call_id=entry.data.get("tool_call_id", ""),
                    name=entry.data.get("name", ""),
                    duration_ms=entry.data.get("duration_ms", 0.0),
                    content=entry.data.get("content", ""),
                ))

        return messages


# ---------------------------------------------------------------------------
# Session listing & resume helpers
# ---------------------------------------------------------------------------


def list_sessions(cwd: str) -> list[dict[str, Any]]:
    """List available sessions for a working directory."""
    session_dir = get_session_dir(cwd)
    if not session_dir.exists():
        return []

    sessions: list[dict[str, Any]] = []
    for f in sorted(session_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        session_id = f.stem
        # Read lite metadata: last few entries for title
        title = ""
        try:
            # Read last 4KB for metadata
            content = f.read_bytes()
            tail = content[-4096:].decode("utf-8", errors="replace")
            for line in reversed(tail.strip().split("\n")):
                try:
                    entry = json.loads(line)
                    if entry.get("type") in ("ai-title", "custom-title"):
                        title = entry.get("data", {}).get("title", "")
                        break
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass

        sessions.append({
            "session_id": session_id,
            "title": title or f"Session {session_id[:8]}",
            "file": str(f),
            "mtime": f.stat().st_mtime,
        })

    return sessions[:50]  # Limit to 50 most recent


def resume_session(cwd: str, session_id: str) -> SessionStorage | None:
    """Resume an existing session by ID."""
    session_dir = get_session_dir(cwd)
    session_file = session_dir / f"{session_id}.jsonl"

    if not session_file.exists():
        return None

    storage = SessionStorage(cwd=cwd, session_id=session_id)
    storage._materialized = True
    return storage


# ---------------------------------------------------------------------------
# Agent output transcript helpers
# ---------------------------------------------------------------------------

_TASK_OUTPUT_BASE_ENV = "CLAUDE_TASK_OUTPUT_DIR"
_TASK_OUTPUT_DEFAULT_DIR = Path("/tmp", "claude-tasks")


def get_task_output_dir() -> str:
    """Return the task output directory: ``<tmpdir>/claude-tasks/<pid>/``.

    Translation of getTaskOutputDir: respects ``CLAUDE_TASK_OUTPUT_DIR`` env
    with a default of ``/tmp/claude-tasks/<pid>``.
    """
    base = os.environ.get(_TASK_OUTPUT_BASE_ENV)
    if base:
        return base
    return str(Path("/tmp", "claude-tasks", str(os.getpid())))


def get_task_output_path(task_id: str) -> str:
    """Return the JSONL output file path for a task or agent."""
    output_dir = get_task_output_dir()
    os.makedirs(output_dir, exist_ok=True)
    return os.path.join(output_dir, f"{task_id}.jsonl")


def _find_task_file(agent_id: str) -> Path | None:
    """Search for a task file across all PID subdirs.

    Useful when the agent was spawned by a different process.
    """
    default_dir = Path(get_task_output_dir()) / f"{agent_id}.jsonl"
    if default_dir.exists():
        return default_dir

    if _TASK_OUTPUT_DEFAULT_DIR.exists():
        for pid_dir in _TASK_OUTPUT_DEFAULT_DIR.iterdir():
            candidate = pid_dir / f"{agent_id}.jsonl"
            if candidate.exists():
                return candidate
    return None


async def load_agent_transcript(agent_id: str) -> dict[str, Any] | None:
    """Load a previously-recorded agent transcript from the output file.

    Translation of getAgentTranscript — reads events from the JSONL
    output file and reconstructs a minimal transcript with messages.

    Returns ``{"messages": [...], "contentReplacements": {...}}`` or ``None``.
    """
    output_file = _find_task_file(agent_id)
    if output_file is None:
        return None

    messages: list[dict[str, Any]] = []
    try:
        with output_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    etype = event.get("type", "")
                    data = event.get("data", {})
                    if etype == "message" or etype == "assistant_message":
                        messages.append({"type": "assistant", **data})
                    elif etype == "tool_result":
                        messages.append({"type": "tool_result", **data})
                    elif etype == "user_message":
                        messages.append({"type": "user", **data})
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None

    return {"messages": messages, "contentReplacements": {}}


async def read_agent_metadata(agent_id: str) -> dict[str, Any] | None:
    """Read saved agent metadata from the first event in the output JSONL file.

    Translation of readAgentMetadata from resumeAgent.ts.
    The first event usually carries metadata (agentType, description, worktreePath).
    """
    output_file = _find_task_file(agent_id)
    if output_file is None:
        return None

    try:
        with output_file.open("r", encoding="utf-8") as f:
            first_line = f.readline().strip()
            if not first_line:
                return None
            event = json.loads(first_line)
            meta = event.get("metadata", {})
            if meta:
                return meta
            # Fallback: try top-level fields
            result: dict[str, Any] = {}
            for key in ("agentType", "agent_type", "description", "worktreePath", "worktreeBranch"):
                if key in event:
                    result[key] = event[key]
            return result if result else None
    except (json.JSONDecodeError, OSError):
        return None
