"""Context building — strict translation of context.ts."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

MAX_STATUS_CHARS = 2000


async def _run_git(cwd: str, *args: str) -> str:
    """Run a git command and return stdout, or empty string on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            return stdout.decode("utf-8", errors="replace").strip()
    except Exception:
        pass
    return ""


async def get_git_status(cwd: str) -> str | None:
    """Strict translation of context.ts getGitStatus().

    Format matches original exactly — do not modify.
    """
    # Check if in a git repo
    check = await _run_git(cwd, "rev-parse", "--is-inside-work-tree")
    if check != "true":
        return None

    # Run 5 git commands in parallel (matches original)
    branch_task = _run_git(cwd, "branch", "--show-current")
    default_branch_task = _run_git(cwd, "config", "--get", "init.defaultBranch")
    status_task = _run_git(cwd, "status", "--short")
    log_task = _run_git(cwd, "log", "--oneline", "-n", "5")
    user_task = _run_git(cwd, "config", "user.name")

    branch, default_branch, status, log, user_name = await asyncio.gather(
        branch_task, default_branch_task, status_task, log_task, user_task,
    )

    if not branch:
        branch = "(detached HEAD)"
    if not default_branch:
        default_branch = "main"

    # Truncate status if too long
    if len(status) > MAX_STATUS_CHARS:
        status = status[:MAX_STATUS_CHARS] + "\n... (status truncated, too many changes to display)"

    if not status:
        status = "(clean)"

    if not log:
        log = "(no commits)"

    parts = [
        "This is the git status at the start of the conversation. "
        "Note that this status is a snapshot in time, and will not update during the conversation.",
        "",
        f"Current branch: {branch}",
        "",
        f"Main branch (you will usually use this for PRs): {default_branch}",
        "",
        f"Git user: {user_name}" if user_name else "Git user: (not configured)",
        "",
        "Status:",
        status,
        "",
        "Recent commits:",
        log,
    ]
    return "\n".join(parts)


async def get_user_context(cwd: str) -> dict[str, str]:
    """Strict translation of context.ts getUserContext().

    Uses 6-layer CLAUDE.md loading with memoized results.
    """
    from AgentX.utils.claudemd import get_claude_mds

    disable_claude_md = os.environ.get("CLAUDE_CODE_DISABLE_CLAUDE_MDS", "").lower() in (
        "1", "true", "yes",
    )

    claude_md: str | None = None
    if not disable_claude_md:
        claude_md = await get_claude_mds(cwd)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    result: dict[str, str] = {}
    if claude_md:
        result["claudeMd"] = claude_md
    result["currentDate"] = f"Today's date is {today}."
    return result


async def get_system_context(cwd: str) -> dict[str, str]:
    """Strict translation of context.ts getSystemContext()."""
    result: dict[str, str] = {}
    git_status = await get_git_status(cwd)
    if git_status:
        result["gitStatus"] = git_status
    return result
