"""Unified memory prompt builder — verbatim translation of memdir.ts.

Load the typed-memory behavioral instructions (without MEMORY.md content)
and optionally append the existing MEMORY.md index.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from AgentX.memdir.memory_types import (
    MEMORY_FRONTMATTER_EXAMPLE,
    TRUSTING_RECALL_SECTION,
    TYPES_SECTION_INDIVIDUAL,
    WHAT_NOT_TO_SAVE_SECTION,
    WHEN_TO_ACCESS_SECTION,
)
from AgentX.memdir.paths import (
    AUTO_MEM_DISPLAY_NAME,
    DIR_EXISTS_GUIDANCE,
    DIRS_EXIST_GUIDANCE,
    ENTRYPOINT_NAME,
    MAX_ENTRYPOINT_BYTES,
    MAX_ENTRYPOINT_LINES,
    ensure_memory_dir_exists,
    get_auto_mem_daily_log_path,
    get_auto_mem_entrypoint,
    get_auto_mem_path,
    is_auto_memory_enabled,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


@dataclass
class EntrypointTruncation:
    """Result of truncating MEMORY.md content to the line AND byte caps."""

    content: str
    line_count: int
    byte_count: int
    was_line_truncated: bool
    was_byte_truncated: bool


def truncate_entrypoint_content(raw: str) -> EntrypointTruncation:
    """Truncate MEMORY.md content to the line AND byte caps, appending a warning.

    Line-truncates first (natural boundary), then byte-truncates at the last
    newline before the cap so we don't cut mid-line.
    """
    trimmed = raw.strip()
    content_lines = trimmed.split("\n")
    line_count = len(content_lines)
    byte_count = len(trimmed)

    was_line_truncated = line_count > MAX_ENTRYPOINT_LINES
    # Check original byte count — long lines are the failure mode the byte cap
    # targets, so post-line-truncation size would understate the warning.
    was_byte_truncated = byte_count > MAX_ENTRYPOINT_BYTES

    if not was_line_truncated and not was_byte_truncated:
        return EntrypointTruncation(
            content=trimmed,
            line_count=line_count,
            byte_count=byte_count,
            was_line_truncated=False,
            was_byte_truncated=False,
        )

    truncated = (
        "\n".join(content_lines[:MAX_ENTRYPOINT_LINES])
        if was_line_truncated
        else trimmed
    )

    if len(truncated) > MAX_ENTRYPOINT_BYTES:
        cut_at = truncated.rfind("\n", 0, MAX_ENTRYPOINT_BYTES)
        truncated = truncated[: cut_at if cut_at > 0 else MAX_ENTRYPOINT_BYTES]

    format_size = _format_size

    if was_byte_truncated and not was_line_truncated:
        reason = f"{format_size(byte_count)} (limit: {format_size(MAX_ENTRYPOINT_BYTES)}) — index entries are too long"
    elif was_line_truncated and not was_byte_truncated:
        reason = f"{line_count} lines (limit: {MAX_ENTRYPOINT_LINES})"
    else:
        reason = f"{line_count} lines and {format_size(byte_count)}"

    return EntrypointTruncation(
        content=(
            truncated
            + f"\n\n> WARNING: {ENTRYPOINT_NAME} is {reason}. Only part of it was loaded. Keep index entries to one line under ~200 chars; move detail into topic files."
        ),
        line_count=line_count,
        byte_count=byte_count,
        was_line_truncated=was_line_truncated,
        was_byte_truncated=was_byte_truncated,
    )


def _format_size(n: int) -> str:
    """Human-readable file size."""
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / (1024 * 1024):.1f}MB"


# ---------------------------------------------------------------------------
# buildMemoryLines
# ---------------------------------------------------------------------------


def build_memory_lines(
    display_name: str,
    memory_dir: str,
    extra_guidelines: list[str] | None = None,
    skip_index: bool = False,
) -> list[str]:
    """Build the typed-memory behavioral instructions (without MEMORY.md content).

    Used by both build_memory_prompt (agent memory, includes content) and
    load_memory_prompt (system prompt, content injected via user context instead).
    """
    how_to_save = (
        [
            "## How to save memories",
            "",
            "Write each memory to its own file (e.g., `user_role.md`, `feedback_testing.md`) using this frontmatter format:",
            "",
            *MEMORY_FRONTMATTER_EXAMPLE,
            "",
            "- Keep the name, description, and type fields in memory files up-to-date with the content",
            "- Organize memory semantically by topic, not chronologically",
            "- Update or remove memories that turn out to be wrong or outdated",
            "- Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one.",
        ]
        if skip_index
        else [
            "## How to save memories",
            "",
            "Saving a memory is a two-step process:",
            "",
            "**Step 1** — write the memory to its own file (e.g., `user_role.md`, `feedback_testing.md`) using this frontmatter format:",
            "",
            *MEMORY_FRONTMATTER_EXAMPLE,
            "",
            f"**Step 2** — add a pointer to that file in `{ENTRYPOINT_NAME}`. `{ENTRYPOINT_NAME}` is an index, not a memory — each entry should be one line, under ~150 characters: `- [Title](file.md) — one-line hook`. It has no frontmatter. Never write memory content directly into `{ENTRYPOINT_NAME}`.",
            "",
            f"- `{ENTRYPOINT_NAME}` is always loaded into your conversation context — lines after {MAX_ENTRYPOINT_LINES} will be truncated, so keep the index concise",
            "- Keep the name, description, and type fields in memory files up-to-date with the content",
            "- Organize memory semantically by topic, not chronologically",
            "- Update or remove memories that turn out to be wrong or outdated",
            "- Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one.",
        ]
    )

    lines: list[str] = [
        f"# {display_name}",
        "",
        f"You have a persistent, file-based memory system at `{memory_dir}`. {DIR_EXISTS_GUIDANCE}",
        "",
        "You should build up this memory system over time so that future conversations can have a complete picture of who the user is, how they'd like to collaborate with you, what behaviors to avoid or repeat, and the context behind the work the user gives you.",
        "",
        "If the user explicitly asks you to remember something, save it immediately as whichever type fits best. If they ask you to forget something, find and remove the relevant entry.",
        "",
        *TYPES_SECTION_INDIVIDUAL,
        *WHAT_NOT_TO_SAVE_SECTION,
        "",
        *how_to_save,
        "",
        *WHEN_TO_ACCESS_SECTION,
        "",
        *TRUSTING_RECALL_SECTION,
        "",
        "## Memory and other forms of persistence",
        "Memory is one of several persistence mechanisms available to you as you assist the user in a given conversation. The distinction is often that memory can be recalled in future conversations and should not be used for persisting information that is only useful within the scope of the current conversation.",
        "- When to use or update a plan instead of memory: If you are about to start a non-trivial implementation task and would like to reach alignment with the user on your approach you should use a Plan rather than saving this information to memory. Similarly, if you already have a plan within the conversation and you have changed your approach persist that change by updating the plan rather than saving a memory.",
        "- When to use or update tasks instead of memory: When you need to break your work in current conversation into discrete steps or keep track of your progress use tasks instead of saving to memory. Tasks are great for persisting information about the work that needs to be done in the current conversation, but memory should be reserved for information that will be useful in future conversations.",
        "",
        *(extra_guidelines or []),
        "",
    ]

    lines.extend(build_searching_past_context_section(memory_dir))

    return lines


# ---------------------------------------------------------------------------
# buildMemoryPrompt
# ---------------------------------------------------------------------------


def build_memory_prompt(
    display_name: str,
    memory_dir: str,
    extra_guidelines: list[str] | None = None,
) -> str:
    """Build the typed-memory prompt with MEMORY.md content included.
    Used by agent memory (which has no getClaudeMds() equivalent).
    """
    entrypoint = os.path.join(memory_dir, ENTRYPOINT_NAME)

    # Read existing memory entrypoint
    entrypoint_content = ""
    try:
        with open(entrypoint, "r", encoding="utf-8") as f:
            entrypoint_content = f.read()
    except FileNotFoundError:
        pass
    except OSError:
        logger.debug("Cannot read memory entrypoint at %s", entrypoint)

    lines = build_memory_lines(display_name, memory_dir, extra_guidelines)

    if entrypoint_content.strip():
        t = truncate_entrypoint_content(entrypoint_content)
        lines.extend(["## " + ENTRYPOINT_NAME, "", t.content])
    else:
        lines.extend(
            [
                "## " + ENTRYPOINT_NAME,
                "",
                f"Your {ENTRYPOINT_NAME} is currently empty. When you save new memories, they will appear here.",
            ]
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# buildAssistantDailyLogPrompt
# ---------------------------------------------------------------------------


def build_assistant_daily_log_prompt(skip_index: bool = False) -> str:
    """Assistant-mode daily-log prompt.

    Assistant sessions are effectively perpetual, so the agent writes memories
    append-only to a date-named log file rather than maintaining MEMORY.md as
    a live index. A separate nightly /dream skill distills logs into topic
    files + MEMORY.md.
    """
    memory_dir = get_auto_mem_path()
    # Describe the path as a pattern rather than inlining today's literal path:
    # this prompt is cached and NOT invalidated on date change.
    log_path_pattern = os.path.join(
        memory_dir, "logs", "YYYY", "MM", "YYYY-MM-DD.md",
    )

    lines: list[str] = [
        "# auto memory",
        "",
        f"You have a persistent, file-based memory system found at: `{memory_dir}`",
        "",
        "This session is long-lived. As you work, record anything worth remembering by **appending** to today's daily log file:",
        "",
        f"`{log_path_pattern}`",
        "",
        "Substitute today's date (from `currentDate` in your context) for `YYYY-MM-DD`. When the date rolls over mid-session, start appending to the new day's file.",
        "",
        "Write each entry as a short timestamped bullet. Create the file (and parent directories) on first write if it does not exist. Do not rewrite or reorganize the log — it is append-only. A separate nightly process distills these logs into `MEMORY.md` and topic files.",
        "",
        "## What to log",
        "- User corrections and preferences (\"use bun, not npm\"; \"stop summarizing diffs\")",
        "- Facts about the user, their role, or their goals",
        "- Project context that is not derivable from the code (deadlines, incidents, decisions and their rationale)",
        "- Pointers to external systems (dashboards, Linear projects, Slack channels)",
        "- Anything the user explicitly asks you to remember",
        "",
        *WHAT_NOT_TO_SAVE_SECTION,
    ]

    if not skip_index:
        lines.extend(
            [
                f"## {ENTRYPOINT_NAME}",
                f"`{ENTRYPOINT_NAME}` is the distilled index (maintained nightly from your logs) and is loaded into your context automatically. Read it for orientation, but do not edit it directly — record new information in today's log instead.",
                "",
            ]
        )

    lines.extend(build_searching_past_context_section(memory_dir))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# build_searching_past_context_section
# ---------------------------------------------------------------------------


def build_searching_past_context_section(auto_mem_dir: str) -> list[str]:
    """Build the "Searching past context" section."""
    from AgentX.constants.tool_names import GREP_TOOL_NAME

    mem_search = f'{GREP_TOOL_NAME} with pattern="<search term>" path="{auto_mem_dir}" glob="*.md"'
    project_dir = _get_project_dir()
    transcript_search = f'{GREP_TOOL_NAME} with pattern="<search term>" path="{project_dir}/" glob="*.jsonl"'

    return [
        "## Searching past context",
        "",
        "When looking for past context:",
        "1. Search topic files in your memory directory:",
        "```",
        mem_search,
        "```",
        "2. Session transcript logs (last resort — large files, slow):",
        "```",
        transcript_search,
        "```",
        "Use narrow search terms (error messages, file paths, function names) rather than broad keywords.",
        "",
    ]


def _get_project_dir(original_cwd: str | None = None) -> str:
    """Get the project directory for transcript search."""
    import os

    if original_cwd:
        return original_cwd
    return os.getcwd()


# ---------------------------------------------------------------------------
# load_memory_prompt
# ---------------------------------------------------------------------------


async def load_memory_prompt(
    extra_guidelines: list[str] | None = None,
    skip_index: bool = False,
) -> str | None:
    """Load the unified memory prompt for inclusion in the system prompt.

    Dispatches based on which memory systems are enabled:
    - auto only: memory lines (single directory)

    Returns None when auto memory is disabled.
    """
    auto_enabled = is_auto_memory_enabled()

    cowork_extra = os.environ.get("NEXUS_COWORK_MEMORY_EXTRA_GUIDELINES")
    extra = (
        [cowork_extra]
        if cowork_extra and cowork_extra.strip() and extra_guidelines is None
        else extra_guidelines
    )

    if auto_enabled:
        auto_dir = get_auto_mem_path()
        ensure_memory_dir_exists(auto_dir)
        return "\n".join(
            build_memory_lines(
                "auto memory", auto_dir, extra, skip_index,
            )
        )

    return None


# ---------------------------------------------------------------------------
# Convenience: get_memory_dir & ensure
# ---------------------------------------------------------------------------


def get_memory_dir() -> str:
    """Canonical entry point for callers that just want the path."""
    return get_auto_mem_path()


def ensure_memory_dir() -> str:
    """Ensure the memory directory exists and return its path."""
    path = get_auto_mem_path()
    ensure_memory_dir_exists(path)
    return path
