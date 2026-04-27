"""Text utilities — shared functions for text processing.

Translation of common text patterns from TypeScript.
"""

from __future__ import annotations


# Default truncation length for tool result previews
TOOL_RESULT_PREVIEW_LENGTH = 500


def truncate_content(content: str, max_len: int = TOOL_RESULT_PREVIEW_LENGTH) -> str:
    """Truncate content to max_len characters, appending '...' if truncated.

    Translation of truncate pattern used in query.ts and tools.
    """
    if len(content) > max_len:
        return content[:max_len] + "..."
    return content
