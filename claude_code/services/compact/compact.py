"""Context compaction service — strict translation of compact logic."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Awaitable

from claude_code.data_types import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (matching TS)
# ---------------------------------------------------------------------------
AUTO_COMPACT_THRESHOLD = 0.85
MIN_MESSAGES_FOR_COMPACT = 6
COMPACT_MAX_OUTPUT_TOKENS = 16_000
AUTOCOMPACT_BUFFER_TOKENS = 13_000
MAX_PTL_RETRIES = 3
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3
POST_COMPACT_MAX_FILES_TO_RESTORE = 5
POST_COMPACT_TOKEN_BUDGET = 50_000
POST_COMPACT_MAX_TOKENS_PER_FILE = 5_000

# Summarize function type
SummarizeFn = Callable[[list[Message]], Awaitable[str]]

# ---------------------------------------------------------------------------
# Prompt templates (matching TS prompt.ts)
# ---------------------------------------------------------------------------
NO_TOOLS_PREAMBLE = """\
CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block."""

BASE_COMPACT_PROMPT = """\
{no_tools_preamble}

Your task is to create a detailed summary of the conversation so far,
paying close attention to the user's explicit requests.

The summary should include the following sections:
1. **Primary Request and Intent** — the user's core request and intent
2. **Key Technical Concepts** — key technical concepts discussed
3. **Files and Code Sections** — files/code sections referenced (with complete code snippets if relevant)
4. **Errors and fixes** — errors encountered and their fixes
5. **Problem Solving** — any problem-solving or debugging steps
6. **All user messages** — all user messages (not tool_result)
7. **Pending Tasks** — tasks the user has asked for but are not yet done
8. **Current Work** — ongoing work (include file names and code snippets)
9. **Optional Next Step** — a suggested next step if applicable (with original references)

Wrap your analysis in <analysis> tags, then your summary in <summary> tags.

{custom_instructions}

REMINDER: Do NOT call any tools. Respond with plain text only —
an <analysis> block followed by a <summary> block.
Tool calls will be rejected and you will fail the task."""

NO_TOOLS_TRAILER = """\
REMINDER: Do NOT call any tools. Respond with plain text only — \
an <analysis> block followed by a <summary> block. \
Tool calls will be rejected and you will fail the task."""


# ---------------------------------------------------------------------------
# Image stripping (matching TS stripImagesFromMessages)
# ---------------------------------------------------------------------------
def strip_images_from_messages(messages: list[Message]) -> list[Message]:
    """Strip image and document blocks from messages before summarization."""
    result: list[Message] = []
    for msg in messages:
        if isinstance(msg, UserMessage):
            content = msg.content
            if isinstance(content, list):
                new_blocks: list[Any] = []
                for block in content:
                    if isinstance(block, dict):
                        btype = block.get("type", "")
                        if btype == "image":
                            new_blocks.append({"type": "text", "text": "[image]"})
                        elif btype == "document":
                            new_blocks.append({"type": "text", "text": "[document]"})
                        elif btype == "tool_result":
                            # Recursively strip from tool_result content
                            inner = block.get("content", [])
                            if isinstance(inner, list):
                                stripped_inner: list[Any] = []
                                for ib in inner:
                                    if isinstance(ib, dict) and ib.get("type") in ("image", "document"):
                                        stripped_inner.append({"type": "text", "text": f"[{ib.get('type', 'media')}]"})
                                    else:
                                        stripped_inner.append(ib)
                                new_blocks.append({**block, "content": stripped_inner})
                            else:
                                new_blocks.append(block)
                        else:
                            new_blocks.append(block)
                    else:
                        new_blocks.append(block)
                result.append(UserMessage(content=new_blocks))
            else:
                result.append(msg)
        else:
            result.append(msg)
    return result


# ---------------------------------------------------------------------------
# Summary formatting (matching TS formatCompactSummary)
# ---------------------------------------------------------------------------
def format_compact_summary(raw_output: str) -> str:
    """Remove <analysis> block and extract <summary> content."""
    # Strip analysis block
    text = re.sub(r"<analysis>[\s\S]*?</analysis>", "", raw_output)

    # Extract summary content
    match = re.search(r"<summary>([\s\S]*?)</summary>", text)
    if match:
        summary_content = match.group(1).strip()
        text = re.sub(r"<summary>[\s\S]*?</summary>", f"Summary:\n{summary_content}", text)

    # Clean extra whitespace
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def get_compact_user_summary_message(
    summary: str,
    suppress_follow_up: bool = False,
) -> str:
    """Wrap summary in the standard continuation message."""
    msg = (
        "This session is being continued from a previous conversation that ran out of context.\n"
        "The summary below covers the earlier portion of the conversation.\n\n"
        f"{summary}\n"
    )
    if suppress_follow_up:
        msg += (
            "\nContinue the conversation from where it left off without asking the user any further questions. "
            "Resume directly — do not acknowledge the summary, do not recap what was happening, "
            'do not preface with "I\'ll continue" or similar. '
            "Pick up the last task as if the break never happened."
        )
    return msg


# ---------------------------------------------------------------------------
# PTL retry (matching TS truncateHeadForPTLRetry)
# ---------------------------------------------------------------------------
def _group_messages_by_api_round(messages: list[Message]) -> list[list[Message]]:
    """Group messages by API round (each assistant message starts a new group)."""
    groups: list[list[Message]] = []
    current: list[Message] = []
    for msg in messages:
        if isinstance(msg, AssistantMessage) and current:
            groups.append(current)
            current = []
        current.append(msg)
    if current:
        groups.append(current)
    return groups


def truncate_head_for_ptl_retry(
    messages: list[Message],
    token_gap: int | None = None,
) -> list[Message]:
    """Drop message groups from the head to reduce token count for PTL retry."""
    groups = _group_messages_by_api_round(messages)
    if len(groups) <= 1:
        return messages

    if token_gap and token_gap > 0:
        # Drop groups from head until we've covered the gap
        tokens_freed = 0
        drop_count = 0
        for group in groups[:-1]:  # Keep at least the last group
            group_tokens = sum(_estimate_message_tokens(m) for m in group)
            tokens_freed += group_tokens
            drop_count += 1
            if tokens_freed >= token_gap:
                break
    else:
        # No gap info: drop 20% of groups
        drop_count = max(1, len(groups) // 5)

    # Keep at least one group
    drop_count = min(drop_count, len(groups) - 1)

    remaining_groups = groups[drop_count:]
    result: list[Message] = []
    for group in remaining_groups:
        result.extend(group)

    # If first message is assistant, prepend synthetic user message
    if result and isinstance(result[0], AssistantMessage):
        result.insert(0, UserMessage(content="[earlier conversation truncated for compaction retry]"))

    return result


# ---------------------------------------------------------------------------
# Main compact function
# ---------------------------------------------------------------------------
async def compact_messages(
    messages: list[Message],
    system_prompt: str,
    summarize_fn: SummarizeFn | None = None,
    max_context_tokens: int = 128_000,
    custom_instructions: str | None = None,
    suppress_follow_up: bool = False,
) -> list[Message]:
    """Compact the message history by summarizing older messages.

    Translation of compact service from services/compact/compact.ts.

    Args:
        messages: Full message history.
        system_prompt: The system prompt (kept as-is).
        summarize_fn: Async function(messages) -> summary string (LLM call).
        max_context_tokens: Maximum context window size.
        custom_instructions: Extra instructions from hooks/user.
        suppress_follow_up: If True, tell model to continue without questions.

    Returns:
        New (shorter) message list with a summary replacing older messages.
    """
    if len(messages) < MIN_MESSAGES_FOR_COMPACT:
        return messages

    # Keep the last few messages intact
    keep_recent = min(4, len(messages) // 2)
    old_messages = messages[:-keep_recent]
    recent_messages = messages[-keep_recent:]

    # Strip images before summarization
    stripped_messages = strip_images_from_messages(old_messages)

    # Build summary
    if summarize_fn is not None:
        summary_text = await _llm_summarize_with_ptl_retry(
            stripped_messages, summarize_fn, custom_instructions
        )
    else:
        summary_text = _basic_summary(stripped_messages)

    # Format the summary (strip analysis, extract summary tags)
    formatted = format_compact_summary(summary_text)

    # Wrap in continuation message
    wrapped = get_compact_user_summary_message(formatted, suppress_follow_up)

    # Build compacted message list
    compact_msg = UserMessage(content=wrapped)

    # Reset memory file cache so CLAUDE.md is re-read after compact
    from claude_code.utils.claudemd import reset_memory_file_cache
    reset_memory_file_cache(reason="compact")

    return [compact_msg, *recent_messages]


async def _llm_summarize_with_ptl_retry(
    messages: list[Message],
    summarize_fn: SummarizeFn,
    custom_instructions: str | None = None,
) -> str:
    """Call the summarize function with PTL retry logic."""
    current_messages = messages

    for attempt in range(MAX_PTL_RETRIES + 1):
        try:
            summary = await summarize_fn(current_messages)
            return summary
        except Exception as exc:
            error_str = str(exc).lower()
            if "prompt_too_long" in error_str or "prompt too long" in error_str:
                if attempt < MAX_PTL_RETRIES:
                    logger.warning(
                        "Compact PTL retry %d/%d — truncating head",
                        attempt + 1, MAX_PTL_RETRIES,
                    )
                    current_messages = truncate_head_for_ptl_retry(current_messages)
                    continue
            # Non-PTL error or max retries
            logger.warning("Compact LLM summarize failed: %s", exc)
            return _basic_summary(messages)

    return _basic_summary(messages)


def _basic_summary(messages: list[Message]) -> str:
    """Create a basic summary from messages without LLM."""
    parts: list[str] = []
    for msg in messages:
        if isinstance(msg, UserMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            parts.append(f"User: {content[:500]}")
        elif isinstance(msg, AssistantMessage):
            if msg.content:
                parts.append(f"Assistant: {msg.content[:500]}")
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    fn = tc.get("function", {})
                    parts.append(f"Tool call: {fn.get('name', '?')}({fn.get('arguments', '')[:200]})")
    return "\n".join(parts[:50])


def build_compact_prompt(custom_instructions: str | None = None) -> str:
    """Build the full compact prompt template."""
    ci = ""
    if custom_instructions:
        ci = f"\n\nAdditional Instructions:\n{custom_instructions}"

    return BASE_COMPACT_PROMPT.format(
        no_tools_preamble=NO_TOOLS_PREAMBLE,
        custom_instructions=ci,
    )


# ---------------------------------------------------------------------------
# Auto-compact
# ---------------------------------------------------------------------------
def should_auto_compact(
    messages: list[Message],
    max_context_tokens: int = 128_000,
    max_output_tokens: int = 16_000,
    estimated_tokens_per_message: int = 200,
) -> bool:
    """Determine if auto-compact should be triggered.

    Translation of autoCompact.ts shouldAutoCompact().
    """
    if len(messages) < MIN_MESSAGES_FOR_COMPACT:
        return False

    effective_window = max_context_tokens - min(max_output_tokens, 20_000)
    threshold = effective_window - AUTOCOMPACT_BUFFER_TOKENS

    estimated_total = sum(
        _estimate_message_tokens(msg, estimated_tokens_per_message)
        for msg in messages
    )

    return estimated_total >= threshold


def _estimate_message_tokens(msg: Message, default: int = 200) -> int:
    """Rough token estimate for a message."""
    if isinstance(msg, (UserMessage, SystemMessage)):
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        return max(len(content) // 4, default)
    if isinstance(msg, AssistantMessage):
        text_len = len(msg.content or "")
        tool_len = sum(
            len(str(tc.get("function", {}).get("arguments", "")))
            for tc in msg.tool_calls
        )
        return max((text_len + tool_len) // 4, default)
    return default
