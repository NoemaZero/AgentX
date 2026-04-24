"""GrepTool — strict translation of tools/GrepTool/."""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any

from AgentX.data_types import GrepOutputMode, ToolParameterType, ToolResult, coerce_str_enum
from AgentX.tools.base import BaseTool, ToolParameter
from AgentX.tools.tool_names import GREP_TOOL_NAME

GREP_DESCRIPTION = """A powerful search tool built on ripgrep

  Usage:
  - ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command. The Grep tool has been optimized for correct permissions and access.
  - Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")
  - Filter files with glob parameter (e.g., "*.js", "**/*.tsx") or type parameter (e.g., "js", "py", "rust")
  - Output modes: "content" shows matching lines, "files_with_matches" shows only file paths (default), "count" shows match counts
  - Use Agent tool for open-ended searches requiring multiple rounds
  - Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping (use `interface\\{\\}` to find `interface{}` in Go code)
  - Multiline matching: By default patterns match within single lines only. For cross-line patterns like `struct \\{[\\s\\S]*?field`, use `multiline: true`"""

# Check for rg (ripgrep) availability
_RG_PATH = shutil.which("rg")


class GrepTool(BaseTool):
    name = GREP_TOOL_NAME
    is_read_only = True
    is_concurrency_safe = True
    should_defer = False

    def get_description(self) -> str:
        return GREP_DESCRIPTION

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="pattern",
                type=ToolParameterType.STRING,
                description="The regular expression pattern to search for in file contents",
            ),
            ToolParameter(
                name="path",
                type=ToolParameterType.STRING,
                description="File or directory to search in (rg PATH). Defaults to current working directory.",
                required=False,
            ),
            ToolParameter(
                name="glob",
                type=ToolParameterType.STRING,
                description='Glob pattern to filter files (e.g. "*.js", "*.{ts,tsx}") - maps to rg --glob',
                required=False,
            ),
            ToolParameter(
                name="output_mode",
                type=ToolParameterType.STRING,
                description=(
                    'Output mode: "content" shows matching lines (supports -A/-B/-C context, -n line numbers, head_limit), '
                    '"files_with_matches" shows file paths (supports head_limit), '
                    '"count" shows match counts (supports head_limit). Defaults to "files_with_matches".'
                ),
                required=False,
                enum=[mode.value for mode in GrepOutputMode],
            ),
            ToolParameter(
                name="-i",
                type=ToolParameterType.BOOLEAN,
                description="Case insensitive search (rg -i)",
                required=False,
            ),
            ToolParameter(
                name="type",
                type=ToolParameterType.STRING,
                description="File type to search (rg --type). Common types: js, py, rust, go, java, etc.",
                required=False,
            ),
            ToolParameter(
                name="head_limit",
                type=ToolParameterType.NUMBER,
                description=(
                    "Limit output to first N lines/entries, equivalent to \"| head -N\". "
                    "Defaults to 250 when unspecified. Pass 0 for unlimited."
                ),
                required=False,
            ),
            ToolParameter(
                name="multiline",
                type=ToolParameterType.BOOLEAN,
                description="Enable multiline mode where . matches newlines and patterns can span lines (rg -U --multiline-dotall). Default: false.",
                required=False,
            ),
            ToolParameter(
                name="-B",
                type=ToolParameterType.NUMBER,
                description='Number of lines to show before each match (rg -B). Requires output_mode: "content", ignored otherwise.',
                required=False,
            ),
            ToolParameter(
                name="-A",
                type=ToolParameterType.NUMBER,
                description='Number of lines to show after each match (rg -A). Requires output_mode: "content", ignored otherwise.',
                required=False,
            ),
            ToolParameter(
                name="-C",
                type=ToolParameterType.NUMBER,
                description="Alias for context.",
                required=False,
            ),
            ToolParameter(
                name="context",
                type=ToolParameterType.NUMBER,
                description='Number of lines to show before and after each match (rg -C). Requires output_mode: "content", ignored otherwise.',
                required=False,
            ),
            ToolParameter(
                name="-n",
                type=ToolParameterType.BOOLEAN,
                description='Show line numbers in output (rg -n). Requires output_mode: "content", ignored otherwise. Defaults to true.',
                required=False,
            ),
            ToolParameter(
                name="offset",
                type=ToolParameterType.NUMBER,
                description='Skip first N lines/entries before applying head_limit, equivalent to "| tail -n +N | head -N". Defaults to 0.',
                required=False,
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        pattern = tool_input.get("pattern", "")
        if not pattern:
            return ToolResult(data="Error: pattern is required")

        search_path = tool_input.get("path") or cwd
        if not os.path.isabs(search_path):
            search_path = os.path.join(cwd, search_path)

        output_mode = coerce_str_enum(
            GrepOutputMode,
            tool_input.get("output_mode"),
            default=GrepOutputMode.FILES_WITH_MATCHES,
        )
        head_limit = tool_input.get("head_limit", 250)
        offset = tool_input.get("offset", 0)

        # Try ripgrep first, fall back to Python grep
        if _RG_PATH:
            return await self._run_ripgrep(tool_input, pattern, search_path, output_mode, head_limit, offset)

        return await self._run_python_grep(pattern, search_path, output_mode, head_limit, offset, tool_input)

    async def _run_ripgrep(
        self,
        tool_input: dict[str, Any],
        pattern: str,
        search_path: str,
        output_mode: GrepOutputMode,
        head_limit: int,
        offset: int,
    ) -> ToolResult:
        args = [_RG_PATH, "--color", "never"]  # type: ignore[list-item]

        if output_mode == GrepOutputMode.FILES_WITH_MATCHES:
            args.append("--files-with-matches")
        elif output_mode == GrepOutputMode.COUNT:
            args.append("--count")

        if tool_input.get("-i"):
            args.append("-i")
        if tool_input.get("multiline"):
            args.extend(["-U", "--multiline-dotall"])
        if tool_input.get("glob"):
            args.extend(["--glob", tool_input["glob"]])
        if tool_input.get("type"):
            args.extend(["--type", tool_input["type"]])

        # Context flags (only for content mode)
        if output_mode == GrepOutputMode.CONTENT:
            show_lines = tool_input.get("-n", True)
            if show_lines is not False:
                args.append("-n")
            if tool_input.get("-B"):
                args.extend(["-B", str(tool_input["-B"])])
            if tool_input.get("-A"):
                args.extend(["-A", str(tool_input["-A"])])
            ctx = tool_input.get("context") or tool_input.get("-C")
            if ctx:
                args.extend(["-C", str(ctx)])

        args.extend(["--", pattern, search_path])

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

            output = stdout.decode("utf-8", errors="replace")

            if proc.returncode == 1:
                return ToolResult(data="No matches found.")
            if proc.returncode and proc.returncode > 1:
                err = stderr.decode("utf-8", errors="replace")
                return ToolResult(data=f"Grep error: {err}")

            lines = output.strip().split("\n")

            # Apply offset and limit
            if offset:
                lines = lines[offset:]
            if head_limit and head_limit > 0:
                lines = lines[:head_limit]

            return ToolResult(data="\n".join(lines) if lines else "No matches found.")

        except asyncio.TimeoutError:
            return ToolResult(data="Grep search timed out after 30s")
        except Exception as e:
            return ToolResult(data=f"Grep error: {e}")

    async def _run_python_grep(
        self,
        pattern: str,
        search_path: str,
        output_mode: GrepOutputMode,
        head_limit: int,
        offset: int,
        tool_input: dict[str, Any],
    ) -> ToolResult:
        """Fallback Python-based grep when ripgrep is not available."""
        import re

        try:
            flags = re.IGNORECASE if tool_input.get("-i") else 0
            if tool_input.get("multiline"):
                flags |= re.DOTALL | re.MULTILINE
            regex = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(data=f"Invalid regex: {e}")

        results: list[str] = []
        glob_pattern = tool_input.get("glob")

        for root, _dirs, files in os.walk(search_path):
            for fname in files:
                if glob_pattern:
                    import fnmatch
                    if not fnmatch.fnmatch(fname, glob_pattern):
                        continue

                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except (OSError, UnicodeDecodeError):
                    continue

                if output_mode == GrepOutputMode.FILES_WITH_MATCHES:
                    if regex.search(content):
                        results.append(fpath)
                elif output_mode == GrepOutputMode.COUNT:
                    count = len(regex.findall(content))
                    if count > 0:
                        results.append(f"{fpath}:{count}")
                else:  # content
                    for i, line in enumerate(content.split("\n"), 1):
                        if regex.search(line):
                            results.append(f"{fpath}:{i}:{line}")

        if offset:
            results = results[offset:]
        if head_limit and head_limit > 0:
            results = results[:head_limit]

        return ToolResult(data="\n".join(results) if results else "No matches found.")
