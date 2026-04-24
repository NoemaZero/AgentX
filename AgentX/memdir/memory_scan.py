"""Memory-directory scanning primitives.

Translation of memoryScan.ts. Shared by find_relevant_memories (query-time
recall) and extract_memories (pre-injects the listing so the extraction agent
doesn't spend a turn on `ls`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from AgentX.memdir.memory_types import MemoryType, parse_memory_type

# Frontmatter parsing
import re


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MAX_MEMORY_FILES = 200
FRONTMATTER_MAX_LINES = 30


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class MemoryHeader:
    """Header for a single .md memory file."""

    filename: str  # relative path within memory dir
    filepath: str  # absolute path
    mtime_ms: float
    description: str | None
    type: MemoryType | None


# ---------------------------------------------------------------------------
# Frontmatter parser (lightweight — no external dep)
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^-{3,}\s*\n(.*?)\n-{3,}\s*", re.DOTALL)
_KV_RE = re.compile(r"^(\w[\w -]*?)\s*:\s*(.*?)\s*$", re.MULTILINE)


def parse_frontmatter(content: str) -> dict[str, str]:
    """Extract YAML-like frontmatter from content as a flat dict.

    Only handles simple key: value pairs (no nested structures needed for
    memory frontmatter: name, description, type).
    """
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}
    raw = m.group(1)
    result: dict[str, str] = {}
    for line in raw.splitlines():
        kv = _KV_RE.match(line)
        if kv:
            key = kv.group(1).strip()
            value = kv.group(2).strip()
            # Strip quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def _read_first_n_lines(filepath: str, n: int) -> tuple[str, float]:
    """Read the first *n* lines of a file and return (content, mtime_ms).

    Single-pass: os.stat is called alongside the read rather than a separate
    round-trip.
    """
    st = os.stat(filepath)
    mtime_ms = st.st_mtime * 1000

    lines: list[str] = []
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i >= n:
                break
            lines.append(line)
    return "".join(lines), mtime_ms


async def scan_memory_files(memory_dir: str) -> list[MemoryHeader]:
    """Scan a memory directory for .md files, read their frontmatter, and
    return a header list sorted newest-first (capped at MAX_MEMORY_FILES).

    Single-pass: _read_first_n_lines stats internally.
    """
    try:
        md_files: list[str] = []
        for root, _dirs, files in os.walk(memory_dir):
            for f in files:
                if f.endswith(".md") and f != "MEMORY.md":
                    rel = os.path.relpath(os.path.join(root, f), memory_dir)
                    md_files.append(rel)

        if not md_files:
            return []

        def _make_header(relative_path: str) -> MemoryHeader:
            filepath = os.path.join(memory_dir, relative_path)
            content, mtime_ms = _read_first_n_lines(
                filepath, FRONTMATTER_MAX_LINES,
            )
            fm = parse_frontmatter(content)
            return MemoryHeader(
                filename=relative_path,
                filepath=filepath,
                mtime_ms=mtime_ms,
                description=fm.get("description") or None,
                type=parse_memory_type(fm.get("type")),
            )

        results: list[MemoryHeader] = []
        for rel in md_files:
            try:
                results.append(_make_header(rel))
            except OSError:
                # Individual file read failure — skip
                continue

        results.sort(key=lambda h: h.mtime_ms, reverse=True)
        return results[:MAX_MEMORY_FILES]

    except OSError:
        return []


# ---------------------------------------------------------------------------
# Manifest formatting
# ---------------------------------------------------------------------------


def format_memory_manifest(memories: list[MemoryHeader]) -> str:
    """Format memory headers as a text manifest: one line per file with
    [type] filename (timestamp): description."""
    lines: list[str] = []
    for m in memories:
        tag = f"[{m.type}] " if m.type else ""
        ts = datetime.fromtimestamp(m.mtime_ms / 1000, tz=timezone.utc).isoformat()
        if m.description:
            lines.append(f"- {tag}{m.filename} ({ts}): {m.description}")
        else:
            lines.append(f"- {tag}{m.filename} ({ts})")
    return "\n".join(lines)
