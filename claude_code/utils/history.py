"""Session history & persistence — strict translation of history.ts + sessionStorage.ts.

Two subsystems:
1. Prompt history   — JSONL at ~/.claude/history.jsonl (up-arrow / Ctrl+R)
2. Session storage  — JSONL at ~/.claude/projects/<sanitized-cwd>/<session-id>.jsonl

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

from claude_code.data_types import (
    AssistantMessage,
    Message,
    ToolResultMessage,
    UserMessage,
)
from claude_code.pydantic_models import FrozenModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_HISTORY_ITEMS = 100
CLAUDE_DIR = Path.home() / ".claude"
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
            self._pending.append(SessionEntry(
                entry_type="assistant",
                data={
                    "content": msg.content or "",
                    "tool_calls": msg.tool_calls,
                },
                session_id=self.session_id,
            ))
        elif isinstance(msg, ToolResultMessage):
            self._pending.append(SessionEntry(
                entry_type="tool_result",
                data={
                    "tool_call_id": msg.tool_call_id,
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
                ))
            elif entry.entry_type == "tool_result":
                messages.append(ToolResultMessage(
                    tool_call_id=entry.data.get("tool_call_id", ""),
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
