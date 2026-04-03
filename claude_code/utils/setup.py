"""Setup & initialization — strict translation of setup.ts.

Handles:
- Git root discovery (walk up to find .git)
- Project detection and cwd resolution
- Worktree / bare repo handling
- Environment checks
- Claude configuration (.claude/ directory init)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from claude_code.utils.git import is_git_repo, run_git_command

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Git root discovery (matching TS getGitRoot)
# ---------------------------------------------------------------------------


async def get_git_root(cwd: str) -> str | None:
    """Find the git repository root by walking up from cwd.

    Translation of getGitRoot() from setup.ts.
    Returns the top-level git directory, or None if not in a repo.
    """
    try:
        result = await run_git_command(cwd, "rev-parse", "--show-toplevel")
        if result:
            return result
    except Exception:
        pass
    return None


async def get_worktree_root(cwd: str) -> str | None:
    """Get the common dir (worktree root)."""
    try:
        result = await run_git_command(cwd, "rev-parse", "--git-common-dir")
        if result and result != ".git":
            # Common dir is inside the main repo, resolve parent
            return str(Path(result).parent.resolve())
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Project setup
# ---------------------------------------------------------------------------


async def resolve_project_cwd(requested_cwd: str | None = None) -> str:
    """Determine the effective working directory for the session.

    Translation of resolveCwd() from setup.ts.
    Preference: requested_cwd > env CWD > os.getcwd().
    """
    if requested_cwd:
        cwd = os.path.abspath(requested_cwd)
    else:
        cwd = os.getcwd()

    # Validate the directory exists
    if not os.path.isdir(cwd):
        logger.warning("Requested cwd does not exist: %s, falling back", cwd)
        cwd = os.getcwd()

    return cwd


def ensure_claude_dir(cwd: str) -> Path:
    """Ensure ~/.claude/ directory exists with proper structure.

    Creates:
    - ~/.claude/
    - ~/.claude/projects/
    - ~/.claude/settings.json (if missing)
    """
    claude_dir = Path.home() / ".claude"
    claude_dir.mkdir(exist_ok=True)

    projects_dir = claude_dir / "projects"
    projects_dir.mkdir(exist_ok=True)

    # Create default settings if missing
    settings_file = claude_dir / "settings.json"
    if not settings_file.exists():
        settings_file.write_text("{}\n", encoding="utf-8")

    return claude_dir


def ensure_project_claude_dir(cwd: str) -> Path | None:
    """Ensure .claude/ directory in the project root.

    Returns the .claude/ path, or None if not in a project.
    """
    project_claude = Path(cwd) / ".claude"
    try:
        project_claude.mkdir(exist_ok=True)
        return project_claude
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Environment checks
# ---------------------------------------------------------------------------


def check_environment() -> list[str]:
    """Check for required environment setup.

    Returns list of warning messages (empty = all good).
    """
    warnings: list[str] = []

    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        warnings.append(
            "No API key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY environment variable."
        )

    # Check for git
    import shutil

    if not shutil.which("git"):
        warnings.append("git not found in PATH. Some features require git.")

    return warnings


# ---------------------------------------------------------------------------
# Full initialization sequence
# ---------------------------------------------------------------------------


async def initialize_session(
    cwd: str | None = None,
    skip_git: bool = False,
) -> dict[str, Any]:
    """Run the full initialization sequence.

    Returns a dict with session setup info:
    - cwd: resolved working directory
    - git_root: git repo root (or None)
    - claude_dir: ~/.claude/ path
    - warnings: list of warning strings
    - is_git_repo: bool
    """
    # 1. Resolve cwd
    resolved_cwd = await resolve_project_cwd(cwd)

    # 2. Ensure ~/.claude/
    claude_dir = ensure_claude_dir(resolved_cwd)

    # 3. Git detection
    git_root: str | None = None
    in_git = False
    if not skip_git:
        in_git = await is_git_repo(resolved_cwd)
        if in_git:
            git_root = await get_git_root(resolved_cwd)

    # 4. Environment checks
    warnings = check_environment()

    return {
        "cwd": resolved_cwd,
        "git_root": git_root,
        "claude_dir": str(claude_dir),
        "warnings": warnings,
        "is_git_repo": in_git,
    }
