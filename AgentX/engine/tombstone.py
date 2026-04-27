"""Tombstone messages — translation of tombstone logic in query.ts.

Used to clear orphan messages (UI + transcript) during fallback/error recovery.
"""

from __future__ import annotations

import logging
from typing import Any

from AgentX.data_types import Message, UserMessage, AssistantMessage, ToolResultMessage

logger = logging.getLogger(__name__)


class TombstoneMessage(UserMessage):
    """Special message to mark orphaned content for cleanup.

    Translation of tombstone messages in query.ts.
    Used during fallback/error recovery to clear orphaned UI messages.
    """

    tombstone_reason: str = ""
    original_message_type: str | None = None

    @classmethod
    def create(cls, reason: str, original_message: Message | None = None) -> "TombstoneMessage":
        """Factory method to create a TombstoneMessage."""
        return cls(
            content=f"[TOMBSTONE: {reason}]",
            tombstone_reason=reason,
            original_message_type=type(original_message).__name__ if original_message else None,
        )


def yield_tombstone_messages(
    assistant_messages: list[AssistantMessage],
    tool_results: list[ToolResultMessage],
    tool_use_blocks: list[dict[str, Any]],
    reason: str = "Model fallback triggered",
) -> list[UserMessage]:
    """Yield tombstone messages to clear orphaned content.

    Translation of yieldMissingToolResultBlocks() in query.ts.
    Returns list of tombstone messages to inject into conversation.
    """
    tombstones: list[UserMessage] = []

    # Clear assistant messages that won't be completed
    for msg in assistant_messages:
        tombstone = TombstoneMessage(reason=reason, original_message=msg)
        tombstones.append(tombstone)
        logger.debug("Created tombstone for assistant message: %s", reason)

    # Clear tool results that won't be used
    for result in tool_results:
        tombstone = TombstoneMessage(reason=reason, original_message=result)
        tombstones.append(tombstone)
        logger.debug("Created tombstone for tool result: %s", reason)

    # Clear tool use blocks that won't be executed
    for block in tool_use_blocks:
        tool_name = block.get("function", {}).get("name", "?")
        tombstone = UserMessage(content=f"[TOMBSTONE: {reason} - tool {tool_name}]")
        tombstones.append(tombstone)
        logger.debug("Created tombstone for tool use block: %s", tool_name)

    return tombstones


def clear_orphaned_state(
    assistant_messages: list[AssistantMessage],
    tool_results: list[ToolResultMessage],
    tool_use_blocks: list[dict[str, Any]],
) -> None:
    """Clear orphaned state during recovery.

    Translation of clearing assistantMessages/toolResults/toolUseBlocks in query.ts.
    Modifies lists in-place to clear orphaned content.
    """
    assistant_messages.clear()
    tool_results.clear()
    tool_use_blocks.clear()
    logger.info("Cleared orphaned state (assistant_messages, tool_results, tool_use_blocks)")
