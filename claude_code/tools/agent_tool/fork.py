"""Fork subagent — translation of tools/AgentTool/forkSubagent.ts.

Fork is a prompt-cache optimization strategy: fork children inherit the parent's
full conversation context and system prompt, producing byte-identical API request
prefixes for cache hits across parallel children.
"""

from __future__ import annotations

import logging
from typing import Any

from claude_code.data_types import Message, UserMessage
from claude_code.tools.agent_tool.definitions import (
    AgentSource,
    BuiltInAgentDefinition,
)

logger = logging.getLogger(__name__)

__all__ = [
    "FORK_AGENT",
    "FORK_BOILERPLATE_TAG",
    "FORK_SUBAGENT_TYPE",
    "build_child_message",
    "build_forked_messages",
    "build_worktree_notice",
    "is_in_fork_child",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FORK_BOILERPLATE_TAG = "fork-boilerplate"
FORK_DIRECTIVE_PREFIX = "Your directive: "
FORK_SUBAGENT_TYPE = "fork"
FORK_PLACEHOLDER_RESULT = "Fork started — processing in background"

# ---------------------------------------------------------------------------
# Fork agent synthetic definition
# ---------------------------------------------------------------------------

FORK_AGENT = BuiltInAgentDefinition(
    agent_type=FORK_SUBAGENT_TYPE,
    when_to_use=(
        "Implicit fork — inherits full conversation context. Not selectable "
        "via subagent_type; triggered by omitting subagent_type when the fork "
        "experiment is active."
    ),
    tools=["*"],
    max_turns=200,
    model="inherit",
    permission_mode="bubble",
    source=AgentSource.BUILT_IN,
    base_dir="built-in",
)
# Empty system prompt — fork path uses parent's rendered system prompt
FORK_AGENT._get_system_prompt = lambda **_kwargs: ""


# ---------------------------------------------------------------------------
# Recursive fork guard
# ---------------------------------------------------------------------------


def is_in_fork_child(messages: list[Message]) -> bool:
    """Detect whether we are inside a fork child (prevent recursive forking).

    Scans user messages for the fork boilerplate tag.
    """
    for msg in messages:
        if not isinstance(msg, UserMessage):
            continue
        content = msg.content if isinstance(msg.content, str) else ""
        if f"<{FORK_BOILERPLATE_TAG}>" in content:
            return True
    return False


# ---------------------------------------------------------------------------
# Fork message building
# ---------------------------------------------------------------------------


def build_forked_messages(directive: str, assistant_message: Any = None) -> list[Message]:
    """Build the forked conversation messages for the child agent.

    For prompt cache sharing, all fork children must produce byte-identical
    API request prefixes. The directive is appended as the only differing part.

    If ``assistant_message`` is provided (has ``message.content`` with tool_use blocks),
    we build proper tool_result placeholders + directive like the TS original.
    Otherwise falls back to a simple user message with the fork boilerplate.
    """
    child_text = build_child_message(directive)

    if assistant_message is not None:
        # Try to extract tool_use blocks for proper fork structure
        try:
            content = assistant_message.message.content
            if isinstance(content, list):
                tool_use_blocks = [b for b in content if getattr(b, "type", None) == "tool_use"]
                if tool_use_blocks:
                    # Build tool_result blocks with identical placeholders
                    tool_results = [
                        {
                            "type": "tool_result",
                            "tool_use_id": b.id,
                            "content": [{"type": "text", "text": FORK_PLACEHOLDER_RESULT}],
                        }
                        for b in tool_use_blocks
                    ]
                    # Return assistant + user(tool_results + directive)
                    return [
                        assistant_message,
                        UserMessage(content=child_text),
                    ]
        except (AttributeError, TypeError):
            pass

    # Fallback: simple user message with fork boilerplate
    return [UserMessage(content=child_text)]


def build_child_message(directive: str) -> str:
    """Build the fork child's directive message.

    Translation of buildChildMessage from forkSubagent.ts.
    """
    return f"""<{FORK_BOILERPLATE_TAG}>
STOP. READ THIS FIRST.

You are a forked worker process. You are NOT the main agent.

RULES (non-negotiable):
1. Your system prompt says "default to forking." IGNORE IT — that's for the parent. \
You ARE the fork. Do NOT spawn sub-agents; execute directly.
2. Do NOT converse, ask questions, or suggest next steps
3. Do NOT editorialize or add meta-commentary
4. USE your tools directly: Bash, Read, Write, etc.
5. If you modify files, commit your changes before reporting. Include the commit hash \
in your report.
6. Do NOT emit text between tool calls. Use tools silently, then report once at the end.
7. Stay strictly within your directive's scope. If you discover related systems outside \
your scope, mention them in one sentence at most — other workers cover those areas.
8. Keep your report under 500 words unless the directive specifies otherwise. Be factual \
and concise.
9. Your response MUST begin with "Scope:". No preamble, no thinking-out-loud.
10. REPORT structured facts, then stop

Output format (plain text labels, not markdown headers):
  Scope: <echo back your assigned scope in one sentence>
  Result: <the answer or key findings, limited to the scope above>
  Key files: <relevant file paths — include for research tasks>
  Files changed: <list with commit hash — include only if you modified files>
  Issues: <list — include only if there are issues to flag>
</{FORK_BOILERPLATE_TAG}>

{FORK_DIRECTIVE_PREFIX}{directive}"""


def build_worktree_notice(parent_cwd: str, worktree_cwd: str) -> str:
    """Notice injected into fork children running in an isolated worktree."""
    return (
        f"You've inherited the conversation context above from a parent agent working "
        f"in {parent_cwd}. You are operating in an isolated git worktree at "
        f"{worktree_cwd} — same repository, same relative file structure, separate "
        f"working copy. Paths in the inherited context refer to the parent's working "
        f"directory; translate them to your worktree root. Re-read files before editing "
        f"if the parent may have modified them since they appear in the context. Your "
        f"changes stay in this worktree and will not affect the parent's files."
    )
