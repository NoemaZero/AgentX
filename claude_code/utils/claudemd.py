"""CLAUDE.md multi-layer loading — strict translation of utils/claudemd.ts.

Loading order (lower → higher priority, model attends more to later content):
1. Managed:  /etc/claude-code/CLAUDE.md
2. User:     ~/.claude/CLAUDE.md  + ~/.claude/rules/*.md
3. Project:  dir-walk root→CWD: CLAUDE.md, .claude/CLAUDE.md, .claude/rules/*.md
4. Local:    dir-walk root→CWD: CLAUDE.local.md

Supports:
- @include directives (MAX_INCLUDE_DEPTH=5)
- Memoization with reset_memory_file_cache()
- Conditional rules (.claude/rules/ with paths frontmatter)
- .claude/rules/ recursive directory traversal
"""

from __future__ import annotations

import logging
import os
import re
from typing import Literal

from pydantic import Field

from claude_code.pydantic_models import FrozenModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — verbatim from TS source
# ---------------------------------------------------------------------------

MEMORY_INSTRUCTION_PROMPT = (
    "Codebase and user instructions are shown below. "
    "Be sure to adhere to these instructions. "
    "IMPORTANT: These instructions OVERRIDE any default behavior "
    "and you MUST follow them exactly as written."
)

MAX_INCLUDE_DEPTH = 5

MemoryType = Literal["User", "Project", "Local", "Managed", "AutoMem", "TeamMem"]

MEMORY_TYPE_DESCRIPTIONS: dict[str, str] = {
    "Project": " (project instructions, checked into the codebase)",
    "Local": " (user's private project instructions, not checked in)",
    "User": " (user's private global instructions for all projects)",
    "Managed": " (managed global instructions)",
    "AutoMem": " (user's auto-memory, persists across conversations)",
    "TeamMem": " (shared team memory, synced across the organization)",
}

# Common text file extensions that @include can reference
TEXT_FILE_EXTENSIONS = frozenset({
    ".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml",
    ".yml", ".toml", ".cfg", ".ini", ".conf", ".sh", ".bash", ".zsh",
    ".fish", ".ps1", ".bat", ".cmd", ".rs", ".go", ".java", ".kt",
    ".scala", ".rb", ".pl", ".pm", ".php", ".c", ".h", ".cpp", ".hpp",
    ".cc", ".cxx", ".cs", ".swift", ".m", ".mm", ".r", ".R", ".jl",
    ".lua", ".vim", ".el", ".clj", ".cljs", ".edn", ".ex", ".exs",
    ".erl", ".hrl", ".hs", ".lhs", ".ml", ".mli", ".fs", ".fsx",
    ".fsi", ".v", ".sv", ".vhd", ".vhdl", ".tcl", ".awk", ".sed",
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".xml", ".xsl",
    ".graphql", ".gql", ".sql", ".proto", ".dockerfile", ".tf", ".hcl",
    ".nix", ".cmake", ".make", ".mk", ".gradle", ".sbt",
    ".properties", ".env", ".gitignore", ".dockerignore", ".editorconfig",
    ".eslintrc", ".prettierrc", ".babelrc",
})

# Frontmatter regex for rules files
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# @include regex — matches @path tokens in leaf text nodes
_INCLUDE_RE = re.compile(r"(?:^|\s)@((?:[^\s\\]|\\ )+)")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class MemoryFileInfo(FrozenModel):
    """Parsed memory file — translation of MemoryFileInfo type."""

    path: str
    content: str
    type: MemoryType
    globs: list[str] = Field(default_factory=list)  # conditional activation
    parent: str | None = None  # the file that @included this one


# ---------------------------------------------------------------------------
# Memoization cache
# ---------------------------------------------------------------------------

_memory_file_cache: list[MemoryFileInfo] | None = None
_memory_file_cache_cwd: str | None = None


def reset_memory_file_cache(reason: str = "") -> None:
    """Clear memory file cache — called on compact or CWD change."""
    global _memory_file_cache, _memory_file_cache_cwd
    _memory_file_cache = None
    _memory_file_cache_cwd = None
    if reason:
        logger.debug("Memory file cache cleared: %s", reason)


# ---------------------------------------------------------------------------
# @include directive processing
# ---------------------------------------------------------------------------


def _extract_include_paths(content: str, base_dir: str) -> list[str]:
    """Extract @include paths from content text.

    Supports: @path, @./relative, @~/home, @/absolute
    Skips code blocks and inline code.
    """
    # Strip fenced code blocks
    stripped = re.sub(r"```[\s\S]*?```", "", content)
    # Strip inline code
    stripped = re.sub(r"`[^`]+`", "", stripped)

    paths: list[str] = []
    for match in _INCLUDE_RE.finditer(stripped):
        raw = match.group(1)
        # Remove trailing fragment
        raw = raw.split("#")[0]
        # Unescape spaces
        raw = raw.replace("\\ ", " ")
        if not raw:
            continue

        # Resolve path
        if raw.startswith("~/"):
            resolved = os.path.expanduser(raw)
        elif raw.startswith("/"):
            resolved = raw
        else:
            resolved = os.path.join(base_dir, raw)

        resolved = os.path.normpath(resolved)

        # Only allow text file extensions
        ext = os.path.splitext(resolved)[1].lower()
        if ext and ext not in TEXT_FILE_EXTENSIONS:
            continue

        paths.append(resolved)

    return paths


async def _process_memory_file(
    file_path: str,
    mem_type: MemoryType,
    processed_paths: set[str],
    include_external: bool = False,
    depth: int = 0,
    parent: str | None = None,
    cwd: str = "",
) -> list[MemoryFileInfo]:
    """Process a single memory file and its @includes recursively.

    Translation of processMemoryFile from claudemd.ts.
    """
    resolved = os.path.normpath(os.path.abspath(file_path))

    # Prevent circular references
    if resolved in processed_paths:
        return []

    # Check file exists
    if not os.path.isfile(resolved):
        return []

    # External file check — only for @include'd files, not direct memory files
    if parent and cwd and not include_external:
        # Allow if the included file is under the same directory tree as its parent
        parent_dir = os.path.dirname(parent)
        try:
            rel_to_parent = os.path.relpath(resolved, parent_dir)
            is_under_parent = not rel_to_parent.startswith("..")
        except ValueError:
            is_under_parent = False

        if not is_under_parent:
            # Also allow if under CWD or home
            try:
                rel = os.path.relpath(resolved, cwd)
                if rel.startswith(".."):
                    home = os.path.expanduser("~")
                    if not resolved.startswith(home):
                        return []
            except ValueError:
                pass

    processed_paths.add(resolved)

    try:
        with open(resolved, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return []

    if not content.strip():
        return []

    # Parse optional frontmatter for conditional rules (globs)
    globs: list[str] = []
    display_content = content
    fm_match = _FRONTMATTER_RE.match(content)
    if fm_match:
        fm_text = fm_match.group(1)
        display_content = content[fm_match.end():]
        # Extract paths/globs from frontmatter
        for line in fm_text.split("\n"):
            line_s = line.strip()
            if line_s.startswith("- ") and globs is not None:
                globs.append(line_s[2:].strip().strip("'\""))
            elif ":" in line_s:
                key, _, val = line_s.partition(":")
                key = key.strip().lower()
                if key in ("paths", "applytop", "applyto"):
                    val = val.strip()
                    if val:
                        globs = [v.strip().strip("'\"") for v in val.split(",")]
                    else:
                        globs = []  # list items follow

    result: list[MemoryFileInfo] = []

    # Main file entry
    result.append(MemoryFileInfo(
        path=resolved,
        content=display_content.strip(),
        type=mem_type,
        globs=globs,
        parent=parent,
    ))

    # Process @include directives (depth limited)
    if depth < MAX_INCLUDE_DEPTH:
        base_dir = os.path.dirname(resolved)
        include_paths = _extract_include_paths(content, base_dir)
        for inc_path in include_paths:
            included = await _process_memory_file(
                inc_path,
                mem_type,
                processed_paths,
                include_external,
                depth + 1,
                resolved,
                cwd,
            )
            result.extend(included)

    return result


# ---------------------------------------------------------------------------
# Rules directory processing
# ---------------------------------------------------------------------------


async def _process_rules_dir(
    rules_dir: str,
    mem_type: MemoryType,
    processed_paths: set[str],
    include_external: bool = False,
    conditional: bool = False,
    cwd: str = "",
) -> list[MemoryFileInfo]:
    """Process .claude/rules/ directory recursively.

    Translation of processMdRules from claudemd.ts.
    conditional=False → unconditional rules (no paths frontmatter)
    conditional=True  → conditional rules (with paths frontmatter)
    """
    if not os.path.isdir(rules_dir):
        return []

    result: list[MemoryFileInfo] = []

    try:
        entries = sorted(os.listdir(rules_dir))
    except OSError:
        return []

    for entry_name in entries:
        entry_path = os.path.join(rules_dir, entry_name)

        if os.path.isdir(entry_path):
            sub_results = await _process_rules_dir(
                entry_path, mem_type, processed_paths,
                include_external, conditional, cwd,
            )
            result.extend(sub_results)
        elif os.path.isfile(entry_path) and entry_name.endswith(".md"):
            files = await _process_memory_file(
                entry_path, mem_type, processed_paths,
                include_external, cwd=cwd,
            )
            for f in files:
                has_globs = bool(f.globs)
                if conditional and has_globs:
                    result.append(f)
                elif not conditional and not has_globs:
                    result.append(f)

    return result


# ---------------------------------------------------------------------------
# Core loading — getMemoryFiles()
# ---------------------------------------------------------------------------


async def get_memory_files(
    cwd: str = "",
    additional_dirs: list[str] | None = None,
    force_reload: bool = False,
) -> list[MemoryFileInfo]:
    """Load all memory files from standard locations.

    Strict translation of getMemoryFiles() from claudemd.ts.
    Results are memoized — call reset_memory_file_cache() to clear.
    """
    global _memory_file_cache, _memory_file_cache_cwd

    if not force_reload and _memory_file_cache is not None and _memory_file_cache_cwd == cwd:
        return list(_memory_file_cache)

    cwd = cwd or os.getcwd()
    result: list[MemoryFileInfo] = []
    processed_paths: set[str] = set()
    include_external = bool(additional_dirs)

    # ── 1. Managed memory ──
    managed_path = "/etc/claude-code/CLAUDE.md"
    managed_files = await _process_memory_file(
        managed_path, "Managed", processed_paths,
        include_external=True, cwd=cwd,
    )
    result.extend(managed_files)

    managed_rules_dir = "/etc/claude-code/rules"
    managed_rules = await _process_rules_dir(
        managed_rules_dir, "Managed", processed_paths,
        include_external=True, cwd=cwd,
    )
    result.extend(managed_rules)

    # ── 2. User memory ──
    home = os.path.expanduser("~")
    user_claude_md = os.path.join(home, ".claude", "CLAUDE.md")
    user_files = await _process_memory_file(
        user_claude_md, "User", processed_paths,
        include_external=True, cwd=cwd,
    )
    result.extend(user_files)

    user_rules_dir = os.path.join(home, ".claude", "rules")
    user_rules = await _process_rules_dir(
        user_rules_dir, "User", processed_paths,
        include_external=True, cwd=cwd,
    )
    result.extend(user_rules)

    # ── 3. Directory walk: root → CWD ──
    dirs_to_cwd: list[str] = []
    current = os.path.abspath(cwd)

    while True:
        dirs_to_cwd.append(current)
        parent_dir = os.path.dirname(current)
        if parent_dir == current:
            break
        current = parent_dir

    # Reverse: root first (lowest priority), CWD last (highest priority)
    dirs_to_cwd.reverse()

    for d in dirs_to_cwd:
        # Project: CLAUDE.md
        project_md = os.path.join(d, "CLAUDE.md")
        pf = await _process_memory_file(
            project_md, "Project", processed_paths,
            include_external=include_external, cwd=cwd,
        )
        result.extend(pf)

        # Project: .claude/CLAUDE.md
        dot_claude_md = os.path.join(d, ".claude", "CLAUDE.md")
        dcf = await _process_memory_file(
            dot_claude_md, "Project", processed_paths,
            include_external=include_external, cwd=cwd,
        )
        result.extend(dcf)

        # Project rules: .claude/rules/*.md (unconditional)
        rules_dir = os.path.join(d, ".claude", "rules")
        uncond_rules = await _process_rules_dir(
            rules_dir, "Project", processed_paths,
            include_external=include_external, conditional=False, cwd=cwd,
        )
        result.extend(uncond_rules)

        # Local: CLAUDE.local.md
        local_md = os.path.join(d, "CLAUDE.local.md")
        lf = await _process_memory_file(
            local_md, "Local", processed_paths,
            include_external=include_external, cwd=cwd,
        )
        result.extend(lf)

    # ── 4. Additional directories ──
    for add_dir in additional_dirs or []:
        add_dir = os.path.abspath(add_dir)
        for fname in ("CLAUDE.md", ".claude/CLAUDE.md"):
            add_md = os.path.join(add_dir, fname)
            af = await _process_memory_file(
                add_md, "Project", processed_paths,
                include_external=True, cwd=cwd,
            )
            result.extend(af)

        add_rules = os.path.join(add_dir, ".claude", "rules")
        arf = await _process_rules_dir(
            add_rules, "Project", processed_paths,
            include_external=True, conditional=False, cwd=cwd,
        )
        result.extend(arf)

    # ── 5. AutoMem (memory.md) ──
    auto_mem_path = os.path.join(home, ".claude", "memory.md")
    am_files = await _process_memory_file(
        auto_mem_path, "AutoMem", processed_paths,
        include_external=True, cwd=cwd,
    )
    result.extend(am_files)

    # Cache results
    _memory_file_cache = list(result)
    _memory_file_cache_cwd = cwd

    return result


# ---------------------------------------------------------------------------
# Format for prompt — getClaudeMds()
# ---------------------------------------------------------------------------


def format_memory_files(
    memory_files: list[MemoryFileInfo],
    *,
    filter_type: MemoryType | None = None,
) -> str | None:
    """Format memory files into a single prompt string.

    Translation of getClaudeMds() from claudemd.ts.
    """
    files = memory_files
    if filter_type:
        files = [f for f in files if f.type == filter_type]

    if not files:
        return None

    memories: list[str] = []
    for mf in files:
        description = MEMORY_TYPE_DESCRIPTIONS.get(mf.type, "")
        memories.append(f"Contents of {mf.path}{description}:\n\n{mf.content}")

    return f"{MEMORY_INSTRUCTION_PROMPT}\n\n" + "\n\n".join(memories)


# ---------------------------------------------------------------------------
# Convenience — backward-compatible get_claude_mds()
# ---------------------------------------------------------------------------


async def get_claude_mds(cwd: str, additional_dirs: list[str] | None = None) -> str | None:
    """Load and format all CLAUDE.md memory files.

    Drop-in replacement for the old 2-source loader. Now supports full 6-layer loading.
    """
    memory_files = await get_memory_files(cwd=cwd, additional_dirs=additional_dirs)
    return format_memory_files(memory_files)
