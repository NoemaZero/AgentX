"""Auto-memory directory paths.

Translation of paths.ts (the subset relevant for Python:
isAutoMemoryEnabled, getMemoryBaseDir, validateMemoryPath,
getAutoMemPath, getAutoMemEntrypoint, isAutoMemPath,
getAutoMemDailyLogPath, DIR_EXISTS_GUIDANCE).

Security: path validation rejects relative, root-near-root, UNC,
null-byte, and other dangerous paths.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from posixpath import normpath


# ---------------------------------------------------------------------------
# Feature flags / runtime config (mirrors TS `feature()` gates)
# ---------------------------------------------------------------------------

_FEATURE_EXTRACT_MEMORIES: bool = (
    os.environ.get("NEXUS_ENABLE_EXTRACT_MEMORIES", "0") in ("1", "true")
)
_FEATURE_NON_INTERACTIVE: bool = (
    os.environ.get("NEXUS_NON_INTERACTIVE", "0") in ("1", "true")
)


# ---------------------------------------------------------------------------
# Auto-memory enable
# ---------------------------------------------------------------------------


def _env_truthy(val: str | None) -> bool:
    return val is not None and val.lower() in ("1", "true", "yes")


def _env_defined_falsy(val: str | None) -> bool:
    """Returns True when env is explicitly set to a falsy value (0, false, no)."""
    return val is not None and val.lower() in ("0", "false", "no")


def is_auto_memory_enabled() -> bool:
    """Whether auto-memory features are enabled. Priority chain (first defined wins):
    1. NEXUS_DISABLE_AUTO_MEMORY env (1/true -> OFF, 0/false -> ON)
    2. NEXUS_SIMPLE (--bare) -> OFF
    3. NEXUS_REMOTE without NEXUS_REMOTE_MEMORY_DIR -> OFF
    4. Default: enabled
    """
    env_val = os.environ.get("NEXUS_DISABLE_AUTO_MEMORY")
    if _env_truthy(env_val):
        return False
    if _env_defined_falsy(env_val):
        return True
    if _env_truthy(os.environ.get("NEXUS_SIMPLE")):
        return False
    if (
        _env_truthy(os.environ.get("NEXUS_REMOTE"))
        and not os.environ.get("NEXUS_REMOTE_MEMORY_DIR")
    ):
        return False
    return True


# ---------------------------------------------------------------------------
# Base directory
# ---------------------------------------------------------------------------


def get_memory_base_dir() -> str:
    """Returns the base directory for persistent memory storage.
    1. NEXUS_REMOTE_MEMORY_DIR env var (explicit override)
    2. ~/.agentx (default config home)
    """
    override = os.environ.get("NEXUS_REMOTE_MEMORY_DIR")
    if override:
        return override
    return os.path.expanduser("~/.agentx")


# ---------------------------------------------------------------------------
# Path validation (SECURITY)
# ---------------------------------------------------------------------------


def _validate_memory_path(
    raw: str | None,
    expand_tilde: bool,
) -> str | None:
    """Normalize and validate a candidate auto-memory directory path.

    SECURITY: Rejects paths that would be dangerous as a read-allowlist root
    or that normpath doesn't fully resolve:
    - relative (!is_absolute): "../foo"
    - root/near-root (length < 3): "/" -> "" after strip; "/a" too short
    - Windows drive-root (C: regex): "C:\" -> "C:" after strip
    - UNC paths (\\\\server\\share): network paths
    - null byte: survives normpath, can truncate in syscalls

    Returns the normalized path with exactly one trailing separator,
    or None if the path is unset/empty/rejected.
    """
    if not raw:
        return None

    candidate = raw

    # ~/ expansion for user-friendly settings paths
    if expand_tilde and (candidate.startswith("~/") or candidate.startswith("~\\")):
        rest = candidate[2:]
        rest_norm = normpath(rest or ".")
        if rest_norm in (".", ".."):
            return None
        candidate = os.path.join(Path.home(), rest)

    # normpath may preserve trailing separator; strip then add exactly one
    normalized = os.path.normpath(candidate).rstrip(os.sep)
    # Also strip trailing forward slash (for cross-platform robustness)
    if os.sep != "/":
        normalized = normalized.rstrip("/")

    if (
        not os.path.isabs(normalized)
        or len(normalized) < 3
        or (
            len(normalized) == 2
            and normalized[0].isalpha()
            and normalized[1] == ":"
        )
        or normalized.startswith("\\\\")
        or normalized.startswith("//")
        or "\0" in normalized
    ):
        return None

    return os.path.normpath(normalized) + os.sep


# ---------------------------------------------------------------------------
# getAutoMemPath
# ---------------------------------------------------------------------------

_AUTO_MEM_DIRNAME = "memory"
_AUTO_MEM_ENTRYPOINT_NAME = "MEMORY.md"
DIR_EXISTS_GUIDANCE = (
    "This directory already exists — write to it directly with the Write tool "
    "(do not run mkdir or check for its existence)."
)
DIRS_EXIST_GUIDANCE = (
    "Both directories already exist — write to them directly with the Write tool "
    "(do not run mkdir or check for their existence)."
)
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25_000
AUTO_MEM_DISPLAY_NAME = "auto memory"
ENTRYPOINT_NAME = "MEMORY.md"


def _get_auto_mem_path_override() -> str | None:
    """Direct override from env var (used by Cowork/SDK)."""
    return _validate_memory_path(
        os.environ.get("NEXUS_COWORK_MEMORY_PATH_OVERRIDE"),
        expand_tilde=False,
    )


def _get_auto_mem_base() -> str:
    """Returns the canonical git repo root if available, otherwise current working dir."""
    import subprocess

    try:
        cwd = os.getcwd()
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, cwd=cwd, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    return cwd


def has_auto_mem_path_override() -> bool:
    """Check if CLAUDE_COWORK_MEMORY_PATH_OVERRIDE is set to a valid override."""
    return _get_auto_mem_path_override() is not None


@lru_cache(maxsize=4)
def get_auto_mem_path() -> str:
    """Returns the auto-memory directory path.

    Resolution order:
    1. NEXUS_COWORK_MEMORY_PATH_OVERRIDE env var
    2. Default: <memoryBase>/projects/<sanitized-cwd>/memory/

    Memoized per cwd (session-stable in production).
    """
    override = _get_auto_mem_path_override()
    if override:
        return override

    memory_base = get_memory_base_dir()
    projects_dir = os.path.join(memory_base, "projects")

    # Sanitize the cwd for use as a path segment
    cwd = _get_auto_mem_base()
    sanitized = sanitize_path_segment(cwd)

    return os.path.join(projects_dir, sanitized, _AUTO_MEM_DIRNAME) + os.sep


def sanitize_path_segment(path: str) -> str:
    """Sanitize a file path to be used as a directory name.

    Convert path separators to underscores and normalize.
    E.g. "/Users/morgan/my-project" -> "_Users_morgan_my-project"
    """
    # Replace path separators with underscores
    result = path.replace(os.sep, "_")
    # Also replace forward slashes
    result = result.replace("/", "_")
    # Remove null bytes
    result = result.replace("\0", "")
    # Collapse multiple underscores
    while "__" in result:
        result = result.replace("__", "_")
    # Strip leading/trailing underscores if any
    result = result.strip("_")
    return result


def get_auto_mem_entrypoint() -> str:
    """Returns the auto-memory entrypoint (MEMORY.md inside the auto-memory dir)."""
    return os.path.join(get_auto_mem_path(), _AUTO_MEM_ENTRYPOINT_NAME)


def get_auto_mem_daily_log_path(date=None) -> str:
    """Returns the daily log file path for the given date (defaults to today).
    Shape: <autoMemPath>/logs/YYYY/MM/YYYY-MM-DD.md
    """
    from datetime import date as date_type
    from datetime import datetime

    if date is None:
        date = datetime.now().date()

    yyyy = date.strftime("%Y")
    mm = date.strftime("%m")
    dd = date.strftime("%d")
    return os.path.join(
        get_auto_mem_path(), "logs", yyyy, mm, f"{yyyy}-{mm}-{dd}.md",
    )


def is_auto_mem_path(absolute_path: str) -> bool:
    """Check if an absolute path is within the auto-memory directory.

    SECURITY: Normalize to prevent path traversal bypasses via .. segments.
    """
    normalized = os.path.normpath(absolute_path)
    return normalized.startswith(get_auto_mem_path())


def is_extract_mode_active() -> bool:
    """Whether the extract-memories background agent will run this session.

    Requires feature('EXTRACT_MEMORIES') gate to be already checked by caller.
    """
    if not _FEATURE_EXTRACT_MEMORIES:
        return False
    return (
        not _FEATURE_NON_INTERACTIVE
        or _env_truthy(os.environ.get("NEXUS_EXTRACT_NON_INTERACTIVE"))
    )


def ensure_memory_dir_exists(memory_dir: str) -> None:
    """Ensure a memory directory exists. Idempotent and recursive."""
    try:
        os.makedirs(memory_dir, exist_ok=True)
    except OSError as e:
        # Real problem (EACCES/EPERM/EROFS) — log for debugging
        import logging
        logger = logging.getLogger(__name__)
        logger.debug("ensure_memory_dir_exists failed for %s: %s", memory_dir, e)
