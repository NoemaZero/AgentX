"""Path validator — validate file paths against allowed directories."""

from __future__ import annotations

import os
from pathlib import Path


class PathValidator:
    """Validate that file operations stay within allowed directories.

    Translation of path validation logic from the TS codebase.
    """

    def __init__(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
    ) -> None:
        self._cwd = Path(cwd).resolve()
        self._allowed: list[Path] = [self._cwd]
        if additional_directories:
            self._allowed.extend(Path(d).resolve() for d in additional_directories if d)

    @property
    def cwd(self) -> Path:
        return self._cwd

    def is_allowed(self, file_path: str) -> bool:
        """Check if a file path is within the allowed directories."""
        try:
            resolved = Path(file_path).resolve()
        except (OSError, ValueError):
            return False

        for allowed_dir in self._allowed:
            try:
                resolved.relative_to(allowed_dir)
                return True
            except ValueError:
                continue

        return False

    def validate(self, file_path: str) -> tuple[bool, str]:
        """Validate a file path. Returns (is_valid, error_message)."""
        if not file_path:
            return False, "File path is empty"

        path = Path(file_path)
        if not path.is_absolute():
            return False, f"File path must be absolute, got: {file_path}"

        if not self.is_allowed(file_path):
            return False, (
                f"Path {file_path} is outside the allowed directories. "
                f"Allowed: {', '.join(str(d) for d in self._allowed)}"
            )

        return True, ""

    def add_directory(self, directory: str) -> None:
        """Add a new allowed directory."""
        resolved = Path(directory).resolve()
        if resolved not in self._allowed:
            self._allowed = [*self._allowed, resolved]

    def resolve_path(self, file_path: str) -> str:
        """Resolve a file path, making relative paths absolute against cwd."""
        path = Path(file_path)
        if path.is_absolute():
            return str(path.resolve())
        return str((self._cwd / path).resolve())
