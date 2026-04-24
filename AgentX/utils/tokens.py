"""Token counting and context window management — translation of tokens.ts.

Provides functions for extracting token usage from messages, calculating context
window sizes, and estimating token counts for threshold checks (autocompact,
session memory, etc.).

This module handles both message representations:
1. Class-based messages (SystemMessage, UserMessage, AssistantMessage, ToolResultMessage)
2. Dict-based messages with 'type' and 'message' fields (used in agent tools)

All functions accept either representation and normalize internally.
"""

from __future__ import annotations

import json
from typing import Any, cast

from AgentX.data_types import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from AgentX.pydantic_models import FrozenModel

# Constants
SYNTHETIC_MODEL = "synthetic"
SYNTHETIC_MESSAGES = {
    "(agent produced no output)",
    "[compact]",
    "[compact placeholder]",
    # Add other synthetic messages as needed
}

# Type aliases
MessageLike = Message | dict[str, Any]
MessageList = list[MessageLike]


def _normalize_message(msg: MessageLike) -> dict[str, Any]:
    """Normalize a message to a dict with 'type' and 'message' fields.

    For class-based messages, convert to the dict representation used by
    agent tools and TypeScript compatibility.
    """
    if isinstance(msg, dict):
        return msg

    # Class-based message → dict representation
    result: dict[str, Any] = {}

    if isinstance(msg, SystemMessage):
        result["type"] = "system"
        result["message"] = {"role": "system", "content": msg.content}
    elif isinstance(msg, UserMessage):
        result["type"] = "user"
        result["message"] = {"role": "user", "content": msg.content}
    elif isinstance(msg, AssistantMessage):
        result["type"] = "assistant"
        message_dict: dict[str, Any] = {"role": "assistant"}
        if msg.content:
            message_dict["content"] = msg.content
        if msg.tool_calls:
            message_dict["tool_calls"] = msg.tool_calls
        if msg.reasoning_content:
            message_dict["reasoning_content"] = msg.reasoning_content
        result["message"] = message_dict
    elif isinstance(msg, ToolResultMessage):
        result["type"] = "tool"
        result["message"] = {
            "role": "tool",
            "tool_call_id": msg.tool_call_id,
            "content": msg.content,
        }
    else:
        # Unknown type, treat as empty assistant
        result["type"] = "assistant"
        result["message"] = {"role": "assistant"}

    return result


def _get_message_type(msg: MessageLike) -> str:
    """Get the message type as a string."""
    if isinstance(msg, dict):
        return str(msg.get("type", ""))

    if isinstance(msg, SystemMessage):
        return "system"
    if isinstance(msg, UserMessage):
        return "user"
    if isinstance(msg, AssistantMessage):
        return "assistant"
    if isinstance(msg, ToolResultMessage):
        return "tool"

    return "unknown"


def _get_message_dict(msg: MessageLike) -> dict[str, Any]:
    """Get the inner message dict."""
    if isinstance(msg, dict):
        return msg.get("message", {})

    # For class-based messages, construct dict
    if isinstance(msg, SystemMessage):
        return {"role": "system", "content": msg.content}
    if isinstance(msg, UserMessage):
        return {"role": "user", "content": msg.content}
    if isinstance(msg, AssistantMessage):
        result: dict[str, Any] = {"role": "assistant"}
        if msg.content:
            result["content"] = msg.content
        if msg.tool_calls:
            result["tool_calls"] = msg.tool_calls
        if msg.reasoning_content:
            result["reasoning_content"] = msg.reasoning_content
        return result
    if isinstance(msg, ToolResultMessage):
        return {
            "role": "tool",
            "tool_call_id": msg.tool_call_id,
            "content": msg.content,
        }

    return {}


def get_token_usage(msg: MessageLike) -> Usage | None:
    """Extract token usage from a message if it's a real (non-synthetic) assistant message.

    Returns None for:
    - Non-assistant messages
    - Synthetic messages (compact placeholders, etc.)
    - Messages without usage data
    - Messages from synthetic models
    """
    msg_type = _get_message_type(msg)
    if msg_type != "assistant":
        return None

    msg_dict = _get_message_dict(msg)

    # Check for usage field
    usage = msg_dict.get("usage")
    if not usage:
        return None

    # Check for synthetic message content
    content = msg_dict.get("content")
    if isinstance(content, list):
        # Handle content block list (Anthropic format)
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text in SYNTHETIC_MESSAGES:
                    return None
    elif isinstance(content, str):
        if content in SYNTHETIC_MESSAGES:
            return None

    # Check for synthetic model
    model = msg_dict.get("model")
    if model is not None and str(model) == SYNTHETIC_MODEL:
        return None

    # Convert dict to Usage object
    if isinstance(usage, Usage):
        return usage

    if isinstance(usage, dict):
        return Usage(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
        )

    return None


def get_assistant_message_id(msg: MessageLike) -> str | None:
    """Get the API response id for an assistant message with real (non-synthetic) usage.

    Used to identify split assistant records that came from the same API response —
    when parallel tool calls are streamed, each content block becomes a separate
    AssistantMessage record, but they all share the same message.id.
    """
    msg_type = _get_message_type(msg)
    if msg_type != "assistant":
        return None

    msg_dict = _get_message_dict(msg)
    model = msg_dict.get("model")
    if model is not None and str(model) == SYNTHETIC_MODEL:
        return None

    return msg_dict.get("id")


def _get_usage_dict(msg: MessageLike) -> dict[str, Any] | None:
    """Get raw usage dict from message, if any."""
    msg_type = _get_message_type(msg)
    if msg_type != "assistant":
        return None

    msg_dict = _get_message_dict(msg)
    usage = msg_dict.get("usage")
    if isinstance(usage, Usage):
        # Convert Usage object to dict
        return {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens,
            "cache_read_input_tokens": usage.cache_read_input_tokens,
        }
    if isinstance(usage, dict):
        return usage
    return None


def get_token_count_from_usage(usage: Usage) -> int:
    """Calculate total context window tokens from an API response's usage data.

    Includes input_tokens + cache tokens + output_tokens.

    This represents the full context size at the time of that API call.
    Use token_count_with_estimation() when you need context size from messages.
    """
    return (
        usage.input_tokens
        + usage.cache_creation_input_tokens
        + usage.cache_read_input_tokens
        + usage.output_tokens
    )


def token_count_from_last_api_response(messages: MessageList) -> int:
    """Get total token count from the most recent API response with usage data."""
    for i in range(len(messages) - 1, -1, -1):
        usage = get_token_usage(messages[i])
        if usage:
            return get_token_count_from_usage(usage)
    return 0


def final_context_tokens_from_last_response(messages: MessageList) -> int:
    """Final context window size from the last API response's usage.iterations[-1].

    Used for task_budget.remaining computation across compaction boundaries —
    the server's budget countdown is context-based, so remaining decrements by
    the pre-compact final window, not billing spend.

    Falls back to top-level input_tokens + output_tokens when iterations is
    absent (no server-side tool loops, so top-level usage IS the final window).
    Both paths exclude cache tokens to match the server-side formula.
    """
    for i in range(len(messages) - 1, -1, -1):
        usage_dict = _get_usage_dict(messages[i])
        if usage_dict:
            # Check for iterations field
            iterations = usage_dict.get("iterations")
            if isinstance(iterations, list) and len(iterations) > 0:
                last = iterations[-1]
                input_tokens = last.get("input_tokens", 0)
                output_tokens = last.get("output_tokens", 0)
                return input_tokens + output_tokens

            # No iterations → no server tool loop → top-level usage IS the final window
            input_tokens = usage_dict.get("input_tokens", 0)
            output_tokens = usage_dict.get("output_tokens", 0)
            return input_tokens + output_tokens

    return 0


def message_token_count_from_last_api_response(messages: MessageList) -> int:
    """Get only the output_tokens from the last API response.

    This excludes input context (system prompt, tools, prior messages).

    WARNING: Do NOT use this for threshold comparisons (autocompact, session memory).
    Use token_count_with_estimation() instead, which measures full context size.
    This function is only useful for measuring how many tokens Claude generated
    in a single response, not how full the context window is.
    """
    for i in range(len(messages) - 1, -1, -1):
        usage = get_token_usage(messages[i])
        if usage:
            return usage.output_tokens
    return 0


def get_current_usage(messages: MessageList) -> dict[str, int] | None:
    """Get detailed usage from the most recent API response."""
    for i in range(len(messages) - 1, -1, -1):
        usage = get_token_usage(messages[i])
        if usage:
            return {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_creation_input_tokens": usage.cache_creation_input_tokens,
                "cache_read_input_tokens": usage.cache_read_input_tokens,
            }
    return None


def does_most_recent_assistant_message_exceed_200k(messages: MessageList) -> bool:
    """Check if the most recent assistant message exceeds 200k tokens."""
    THRESHOLD = 200_000

    # Find last assistant message
    for i in range(len(messages) - 1, -1, -1):
        if _get_message_type(messages[i]) == "assistant":
            usage = get_token_usage(messages[i])
            if usage:
                return get_token_count_from_usage(usage) > THRESHOLD
            break

    return False


def get_assistant_message_content_length(msg: AssistantMessage | dict[str, Any]) -> int:
    """Calculate the character content length of an assistant message.

    Used for spinner token estimation (characters / 4 ≈ tokens).
    This is used when subagent streaming events are filtered out and we
    need to count content from completed messages instead.

    Counts the same content that would be counted via deltas:
    - text (text_delta)
    - thinking (thinking_delta)
    - redacted_thinking data
    - tool_use input (input_json_delta)
    Note: signature_delta is excluded from streaming counts (not model output).
    """
    if isinstance(msg, dict):
        # Dict representation with 'message' field
        msg_dict = msg.get("message", {}) if "message" in msg else msg
        content = msg_dict.get("content", [])
    else:
        # AssistantMessage instance
        content = msg.content or ""

    content_length = 0

    if isinstance(content, str):
        # Plain text content
        content_length += len(content)
    elif isinstance(content, list):
        # Content block list (Anthropic format)
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type")
                if block_type == "text":
                    text = block.get("text", "")
                    content_length += len(text)
                elif block_type == "thinking":
                    thinking = block.get("thinking", "")
                    content_length += len(thinking)
                elif block_type == "redacted_thinking":
                    data = block.get("data", "")
                    content_length += len(data)
                elif block_type == "tool_use":
                    # Use json.dumps to get character length of input
                    tool_input = block.get("input", {})
                    content_length += len(json.dumps(tool_input))
    elif content is not None:
        # Fallback: convert to string
        content_length += len(str(content))

    return content_length


def _estimate_message_tokens(msg: MessageLike, default: int = 200) -> int:
    """Rough token estimate for a message.

    Adapted from services/compact/compact.py:_estimate_message_tokens
    but works with both message representations.
    """
    # Normalize to class-based message if possible
    if isinstance(msg, (SystemMessage, UserMessage, AssistantMessage, ToolResultMessage)):
        # Use the existing logic from compact.py
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

    # Dict representation
    msg_type = _get_message_type(msg)
    msg_dict = _get_message_dict(msg)

    if msg_type in ("system", "user"):
        content = msg_dict.get("content", "")
        if isinstance(content, list):
            # Content block list - extract text
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            content = "".join(text_parts)
        content_str = str(content)
        return max(len(content_str) // 4, default)

    if msg_type == "assistant":
        content = msg_dict.get("content", "")
        tool_calls = msg_dict.get("tool_calls", [])

        text_len = 0
        if isinstance(content, str):
            text_len = len(content)
        elif isinstance(content, list):
            # Content block list
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type")
                    if block_type == "text":
                        text_len += len(block.get("text", ""))
                    elif block_type == "thinking":
                        text_len += len(block.get("thinking", ""))
                    elif block_type == "redacted_thinking":
                        text_len += len(block.get("data", ""))
                    elif block_type == "tool_use":
                        # Already counted in tool_calls
                        pass

        tool_len = 0
        for tc in tool_calls:
            if isinstance(tc, dict):
                func = tc.get("function", {})
                if isinstance(func, dict):
                    args = func.get("arguments", "")
                    tool_len += len(str(args))

        return max((text_len + tool_len) // 4, default)

    return default


def token_count_with_estimation(messages: MessageList) -> int:
    """Get the current context window size in tokens.

    This is the CANONICAL function for measuring context size when checking
    thresholds (autocompact, session memory init, etc.). Uses the last API
    response's token count (input + output + cache) plus estimates for any
    messages added since.

    Always use this instead of:
    - Cumulative token counting (which double-counts as context grows)
    - message_token_count_from_last_api_response (which only counts output_tokens)
    - token_count_from_last_api_response (which doesn't estimate new messages)

    Implementation note on parallel tool calls: when the model makes multiple
    tool calls in one response, the streaming code emits a SEPARATE assistant
    record per content block (all sharing the same message.id and usage), and
    the query loop interleaves each tool_result immediately after its tool_use.
    So the messages array looks like:
      [..., assistant(id=A), user(result), assistant(id=A), user(result), ...]
    If we stop at the LAST assistant record, we only estimate the one tool_result
    after it and miss all the earlier interleaved tool_results — which will ALL
    be in the next API request. To avoid undercounting, after finding a usage-
    bearing record we walk back to the FIRST sibling with the same message.id
    so every interleaved tool_result is included in the rough estimate.
    """
    # Find the most recent assistant message with usage data
    i = len(messages) - 1
    while i >= 0:
        msg = messages[i]
        usage = get_token_usage(msg)
        if usage is not None:
            # Walk back past any earlier sibling records split from the same API
            # response (same message.id) so interleaved tool_results between them
            # are included in the estimation slice.
            response_id = get_assistant_message_id(msg)
            if response_id:
                j = i - 1
                while j >= 0:
                    prior = messages[j]
                    prior_id = get_assistant_message_id(prior)
                    if prior_id == response_id:
                        # Earlier split of the same API response — anchor here instead.
                        i = j
                    elif prior_id is not None:
                        # Hit a different API response — stop walking.
                        break
                    # prior_id === None: a user/tool_result/attachment message,
                    # possibly interleaved between splits — keep walking.
                    j -= 1

            # Estimate tokens for messages after the anchor point
            estimated_tokens = 0
            for k in range(i + 1, len(messages)):
                estimated_tokens += _estimate_message_tokens(messages[k])

            return get_token_count_from_usage(usage) + estimated_tokens
        i -= 1

    # No usage found — estimate all messages
    total = 0
    for msg in messages:
        total += _estimate_message_tokens(msg)
    return total


# Public API
__all__ = [
    "get_token_usage",
    "get_assistant_message_id",
    "get_token_count_from_usage",
    "token_count_from_last_api_response",
    "final_context_tokens_from_last_response",
    "message_token_count_from_last_api_response",
    "get_current_usage",
    "does_most_recent_assistant_message_exceed_200k",
    "get_assistant_message_content_length",
    "token_count_with_estimation",
]