"""BashTool — strict translation of tools/BashTool/."""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

from AgentX.tools.base import BaseTool, ToolParameter
from AgentX.tools.tool_names import BASH_TOOL_NAME
from AgentX.data_types import ToolResult

MAX_TIMEOUT_MS = 600_000  # 10 minutes
DEFAULT_TIMEOUT_MS = 120_000  # 2 minutes

BASH_DESCRIPTION = """Executes a given bash command and returns its output.

The working directory persists between commands, but shell state does not. The shell environment is initialized from the user's profile (bash or zsh).

IMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, `head`, `tail`, `sed`, `awk`, or `echo` commands, unless explicitly instructed or after you have verified that a dedicated tool cannot accomplish your task. Instead, use the appropriate dedicated tool as this will provide a much better experience for the user:

 - File search: Use Glob (NOT find or ls)
 - Content search: Use Grep (NOT grep or rg)
 - Read files: Use Read (NOT cat/head/tail)
 - Edit files: Use Edit (NOT sed/awk)
 - Write files: Use Write (NOT echo >/cat <<EOF)
 - Communication: Output text directly (NOT echo/printf)
While the Bash tool can do similar things, it's better to use the built-in tools as they provide a better user experience and make it easier to review tool calls and give permission.

# Instructions
 - If your command will create new directories or files, first use this tool to run `ls` to verify the parent directory exists and is the correct location.
 - Always quote file paths that contain spaces with double quotes in your command (e.g., cd "path with spaces/file.txt")
 - Try to maintain your current working directory throughout the session by using absolute paths and avoiding usage of `cd`. You may use `cd` if the User explicitly requests it.
 - You may specify an optional timeout in milliseconds (up to 600000ms / 10 minutes). By default, your command will timeout after 120000ms (2 minutes).
 - You can use the `run_in_background` parameter to run the command in the background. Only use this if you don't need the result immediately and are OK being notified when the command completes later. You do not need to check the output right away - you'll be notified when it finishes. You do not need to use '&' at the end of the command when using this parameter.
 - When issuing multiple commands:
  - If the commands are independent and can run in parallel, make multiple Bash tool calls in a single message. Example: if you need to run "git status" and "git diff", send a single message with two Bash tool calls in parallel.
  - If the commands depend on each other and must run sequentially, use a single Bash call with '&&' to chain them together.
  - Use ';' only when you need to run commands sequentially but don't care if earlier commands fail.
  - DO NOT use newlines to separate commands (newlines are ok in quoted strings).
 - For git commands:
  - Prefer to create a new commit rather than amending an existing commit.
  - Before running destructive operations (e.g., git reset --hard, git push --force, git checkout --), consider whether there is a safer alternative that achieves the same goal. Only use destructive operations when they are truly the best approach.
  - Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign, -c commit.gpgsign=false) unless the user has explicitly asked for it. If a hook fails, investigate and fix the underlying issue.
 - Avoid unnecessary `sleep` commands:
  - Do not sleep between commands that can run immediately — just run them.
  - If your command is long running and you would like to be notified when it finishes — use `run_in_background`. No sleep needed.
  - Do not retry failing commands in a sleep loop — diagnose the root cause.
  - If waiting for a background task you started with `run_in_background`, you will be notified when it completes — do not poll.
  - If you must poll an external process, use a check command (e.g. `gh run view`) rather than sleeping first.
  - If you must sleep, keep the duration short (1-5 seconds) to avoid blocking the user."""


DESCRIPTION_PARAM = (
    'Clear, concise description of what this command does in active voice. '
    'Never use words like "complex" or "risk" in the description - just describe what it does.\n\n'
    "For simple commands (git, npm, standard CLI tools), keep it brief (5-10 words):\n"
    '- ls \u2192 "List files in current directory"\n'
    '- git status \u2192 "Show working tree status"\n'
    '- npm install \u2192 "Install package dependencies"\n\n'
    "For commands that are harder to parse at a glance (piped commands, obscure flags, etc.), "
    "add enough context to clarify what it does:\n"
    '- find . -name "*.tmp" -exec rm {} \\; \u2192 "Find and delete all .tmp files recursively"\n'
    '- git reset --hard origin/main \u2192 "Discard all local changes and match remote main"\n'
    "- curl -s url | jq '.data[]' \u2192 \"Fetch JSON from URL and extract data array elements\""
)


class BashTool(BaseTool):
    name = BASH_TOOL_NAME
    is_read_only = False
    is_concurrency_safe = False
    should_defer = False

    def get_description(self) -> str:
        return BASH_DESCRIPTION

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="command", type="string", description="The command to execute"),
            ToolParameter(
                name="timeout",
                type="number",
                description=f"Optional timeout in milliseconds (max {MAX_TIMEOUT_MS})",
                required=False,
            ),
            ToolParameter(
                name="description",
                type="string",
                description=DESCRIPTION_PARAM,
                required=False,
            ),
            ToolParameter(
                name="run_in_background",
                type="boolean",
                description="Set to true to run this command in the background. Use Read to read the output later.",
                required=False,
            ),
        ]

    def check_is_read_only(self, tool_input: dict[str, Any]) -> bool:
        """Check if a bash command is read-only using the classifier."""
        from AgentX.permissions.classifier import is_read_only_bash

        command = tool_input.get("command", "")
        return is_read_only_bash(command)

    def check_is_concurrency_safe(self, tool_input: dict[str, Any]) -> bool:
        """Read-only bash commands are concurrency-safe."""
        return self.check_is_read_only(tool_input)

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        command = tool_input.get("command", "")
        timeout_ms = min(tool_input.get("timeout", DEFAULT_TIMEOUT_MS), MAX_TIMEOUT_MS)
        timeout_s = timeout_ms / 1000.0
        run_in_background = tool_input.get("run_in_background", False)

        if not command.strip():
            return ToolResult(data="Error: No command provided")

        # Background execution via task manager
        if run_in_background:
            task_manager = kwargs.get("task_manager")
            if task_manager is not None:
                desc = tool_input.get("description", command[:80])
                task_id = await task_manager.create_task(
                    description=desc,
                    prompt=command,
                    task_type="local_bash",
                    cwd=cwd,
                )
                return ToolResult(data=f"Background task started: {task_id}\nUse TaskOutput to check results.")

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env={**os.environ},
            )

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except asyncio.TimeoutError:
                proc.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.communicate(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                return ToolResult(data=f"Command timed out after {timeout_ms}ms")

            output_parts: list[str] = []
            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))
            if stderr:
                output_parts.append(stderr.decode("utf-8", errors="replace"))

            output = "\n".join(output_parts).strip()
            if proc.returncode != 0:
                output = f"Exit code: {proc.returncode}\n{output}"

            # Truncate if too large
            if len(output) > self.max_result_size_chars:
                output = output[: self.max_result_size_chars] + "\n... (output truncated)"

            return ToolResult(data=output if output else "(no output)")

        except Exception as e:
            return ToolResult(data=f"Error executing command: {e}")
