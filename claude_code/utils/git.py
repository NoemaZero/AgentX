"""Git utilities — translation of utils/git/."""

from __future__ import annotations

import asyncio


async def run_git_command(cwd: str, *args: str) -> str:
    """Run a git command, return stdout."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        return stdout.decode("utf-8", errors="replace").strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


async def is_git_repo(cwd: str) -> bool:
    result = await run_git_command(cwd, "rev-parse", "--is-inside-work-tree")
    return result == "true"


async def get_current_branch(cwd: str) -> str:
    return await run_git_command(cwd, "branch", "--show-current") or "(detached HEAD)"


async def get_default_branch(cwd: str) -> str:
    return await run_git_command(cwd, "config", "--get", "init.defaultBranch") or "main"
