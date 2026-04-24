"""FileReadTool — strict translation of tools/FileReadTool/."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from AgentX.tools.base import BaseTool, ToolParameter
from AgentX.tools.tool_names import FILE_READ_TOOL_NAME
from AgentX.data_types import ToolResult

# Binary extensions that should be rejected
BINARY_EXTENSIONS = frozenset({
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".lib",
    ".class", ".jar", ".war", ".ear",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".db", ".sqlite", ".sqlite3",
    ".wasm", ".pyc", ".pyo",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".ico", ".icns",
    ".mp3", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".mkv", ".m4a", ".wav", ".flac",
})

# Image extensions that get special handling
IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
})

# Device files to block
DEVICE_PATHS = frozenset({
    "/dev/zero", "/dev/random", "/dev/urandom", "/dev/null",
    "/dev/stdin", "/dev/stdout", "/dev/stderr",
})

# Max file size in bytes (10MB)
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024


class FileReadTool(BaseTool):
    name = FILE_READ_TOOL_NAME
    is_read_only = True
    is_concurrency_safe = True
    should_defer = False

    # File dedup cache: path -> (mtime, offset, limit, content_hash)
    _read_cache: dict[str, tuple[float, int, int | None, str]] = {}

    def get_description(self) -> str:
        return "Read a file from the local filesystem."

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="file_path", type="string", description="Absolute path to the file to read"),
            ToolParameter(
                name="offset",
                type="number",
                description="Line offset to start reading from (0-based)",
                required=False,
            ),
            ToolParameter(
                name="limit",
                type="number",
                description="Maximum number of lines to read",
                required=False,
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return ToolResult(data="Error: file_path is required")

        # Resolve relative paths
        if not os.path.isabs(file_path):
            file_path = os.path.join(cwd, file_path)

        # Block device files
        if file_path in DEVICE_PATHS:
            return ToolResult(data=f"Error: Cannot read device file: {file_path}")

        if not os.path.exists(file_path):
            # Suggest similar files
            suggestion = _find_similar_file(file_path, cwd)
            msg = f"Error: File not found: {file_path}"
            if suggestion:
                msg += f"\nDid you mean: {suggestion}?"
            return ToolResult(data=msg)

        if os.path.isdir(file_path):
            return ToolResult(data=f"Error: {file_path} is a directory, not a file. Use Glob or Bash ls instead.")

        # Check file size
        file_size = os.path.getsize(file_path)
        if file_size > MAX_FILE_SIZE_BYTES:
            return ToolResult(
                data=f"Error: File is too large ({file_size:,} bytes). Use offset/limit to read portions."
            )

        ext = Path(file_path).suffix.lower()

        # Binary check
        if ext in BINARY_EXTENSIONS:
            return ToolResult(data=f"Error: Cannot read binary file ({ext}): {file_path}")

        # Image handling
        if ext in IMAGE_EXTENSIONS:
            return _read_image_file(file_path)

        # Jupyter notebook handling
        if ext == ".ipynb":
            return _read_notebook_file(file_path)

        # File dedup check
        offset = tool_input.get("offset", 0) or 0
        limit = tool_input.get("limit")
        mtime = os.path.getmtime(file_path)
        cache_key = file_path
        cached = self._read_cache.get(cache_key)

        if cached is not None:
            cached_mtime, cached_offset, cached_limit, _ = cached
            if cached_mtime == mtime and cached_offset == offset and cached_limit == limit:
                return ToolResult(
                    data=f"File {file_path}: content unchanged since last read (mtime={mtime}). "
                    "Use a different offset/limit or re-read if the file was modified."
                )

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except UnicodeDecodeError:
            return ToolResult(data=f"Error: File appears to be binary: {file_path}")
        except Exception as e:
            return ToolResult(data=f"Error reading file: {e}")

        if limit:
            selected = lines[offset: offset + limit]
        else:
            selected = lines[offset:]

        # Format with line numbers
        numbered_lines: list[str] = []
        for i, line in enumerate(selected, start=offset + 1):
            numbered_lines.append(f"{i}\t{line.rstrip()}")

        result = "\n".join(numbered_lines)
        total_lines = len(lines)
        header = f"File: {file_path} ({total_lines} lines total)"

        if offset > 0 or (limit and offset + limit < total_lines):
            shown_end = min(offset + len(selected), total_lines)
            header += f", showing lines {offset + 1}-{shown_end}"

        # Cache this read
        self._read_cache[cache_key] = (mtime, offset, limit, "")

        return ToolResult(data=f"{header}\n{result}")


def _read_image_file(file_path: str) -> ToolResult:
    """Read an image file and return base64 representation."""
    try:
        with open(file_path, "rb") as f:
            data = f.read()

        ext = Path(file_path).suffix.lower()
        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            ".svg": "image/svg+xml",
        }
        mime = mime_map.get(ext, "image/png")
        size_kb = len(data) / 1024

        if ext == ".svg":
            # SVG files are text — return as text content
            content = data.decode("utf-8", errors="replace")
            return ToolResult(data=f"SVG image ({size_kb:.1f}KB):\n{content[:50000]}")

        b64 = base64.b64encode(data).decode("ascii")
        return ToolResult(
            data=f"Image file: {file_path} ({size_kb:.1f}KB, {mime})\nBase64: {b64[:200]}... (use vision API for full content)"
        )
    except Exception as e:
        return ToolResult(data=f"Error reading image: {e}")


def _read_notebook_file(file_path: str) -> ToolResult:
    """Read a Jupyter notebook file and format cells."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            nb = json.loads(f.read())

        cells = nb.get("cells", [])
        parts = [f"Notebook: {file_path} ({len(cells)} cells)"]

        for i, cell in enumerate(cells):
            cell_type = cell.get("cell_type", "?")
            cell_id = cell.get("id", cell.get("metadata", {}).get("id", str(i)))
            source = cell.get("source", [])
            if isinstance(source, list):
                source = "".join(source)

            parts.append(f"\n--- Cell {i} [{cell_type}] (id: {cell_id}) ---")
            parts.append(source[:5000])

            # Show outputs for code cells
            outputs = cell.get("outputs", [])
            if outputs:
                parts.append(f"  ({len(outputs)} output(s))")

        return ToolResult(data="\n".join(parts))
    except Exception as e:
        return ToolResult(data=f"Error reading notebook: {e}")


def _find_similar_file(file_path: str, cwd: str) -> str | None:
    """Find a similar file name in the same directory."""
    directory = os.path.dirname(file_path)
    basename = os.path.basename(file_path).lower()

    if not os.path.isdir(directory):
        return None

    try:
        entries = os.listdir(directory)
    except OSError:
        return None

    # Simple Levenshtein-like matching
    best: str | None = None
    best_score = 0

    for entry in entries[:200]:  # Limit scan
        entry_lower = entry.lower()
        if entry_lower == basename:
            continue
        # Check prefix/suffix overlap
        common = sum(1 for a, b in zip(entry_lower, basename) if a == b)
        score = common / max(len(entry_lower), len(basename))
        if score > 0.6 and score > best_score:
            best_score = score
            best = os.path.join(directory, entry)

    return best
