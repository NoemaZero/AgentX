"""Built-in agent registry — translation of tools/AgentTool/builtInAgents.ts."""

from __future__ import annotations

from typing import Any

from claude_code.tools.agent_tool.definitions import (
    AgentSource,
    BaseAgentDefinition,
    BuiltInAgentDefinition,
)

__all__ = [
    "GENERAL_PURPOSE_AGENT",
    "get_built_in_agents",
]

# ---------------------------------------------------------------------------
# Shared prompt fragments (from generalPurposeAgent.ts)
# ---------------------------------------------------------------------------

_SHARED_PREFIX = (
    "You are an agent for Claude Code, Anthropic's official CLI for Claude. "
    "Given the user's message, you should use the tools available to complete the task. "
    "Complete the task fully—don't gold-plate, but don't leave it half-done."
)

_SHARED_GUIDELINES = """\
Your strengths:
- Searching for code, configurations, and patterns across large codebases
- Analyzing multiple files to understand system architecture
- Investigating complex questions that require exploring many files
- Performing multi-step research tasks

Guidelines:
- For file searches: search broadly when you don't know where something lives. \
Use Read when you know the specific file path.
- For analysis: Start broad and narrow down. Use multiple search strategies if \
the first doesn't yield results.
- Be thorough: Check multiple locations, consider different naming conventions, \
look for related files.
- NEVER create files unless they're absolutely necessary for achieving your goal. \
ALWAYS prefer editing an existing file to creating a new one.
- NEVER proactively create documentation files (*.md) or README files. Only create \
documentation files if explicitly requested."""


def _general_purpose_system_prompt(**_kwargs: Any) -> str:
    return (
        f"{_SHARED_PREFIX} When you complete the task, respond with a concise report "
        "covering what was done and any key findings — the caller will relay this to "
        "the user, so it only needs the essentials.\n\n"
        f"{_SHARED_GUIDELINES}"
    )


# ---------------------------------------------------------------------------
# Singleton built-in agents
# ---------------------------------------------------------------------------

GENERAL_PURPOSE_AGENT = BuiltInAgentDefinition(
    agent_type="general-purpose",
    when_to_use=(
        "General-purpose agent for researching complex questions, searching for code, "
        "and executing multi-step tasks. When you are searching for a keyword or file "
        "and are not confident that you will find the right match in the first few tries "
        "use this agent to perform the search for you."
    ),
    tools=["*"],
    source=AgentSource.BUILT_IN,
    base_dir="built-in",
)
GENERAL_PURPOSE_AGENT._get_system_prompt = _general_purpose_system_prompt


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_built_in_agents() -> list[BaseAgentDefinition]:
    """Return the list of built-in agents.

    Translation of getBuiltInAgents() from builtInAgents.ts.
    Currently only the general-purpose agent; extend as needed.
    """
    return [GENERAL_PURPOSE_AGENT]
