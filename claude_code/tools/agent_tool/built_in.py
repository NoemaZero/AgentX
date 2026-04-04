"""Built-in agent registry — translation of tools/AgentTool/builtInAgents.ts + built-in/*.ts.

Registers all 6 built-in agents:
  1. general-purpose  — universal task agent (always enabled)
  2. statusline-setup — status bar configuration (always enabled)
  3. Explore          — read-only code exploration (feature-gated)
  4. Plan             — read-only architecture planning (feature-gated)
  5. claude-code-guide— environment help guide (non-SDK only)
  6. verification     — background verification (feature-gated)
"""

from __future__ import annotations

import os
from typing import Any

from claude_code.tools.agent_tool.definitions import (
    AgentColorName,
    AgentSource,
    BaseAgentDefinition,
    BuiltInAgentDefinition,
)

__all__ = [
    "CLAUDE_CODE_GUIDE_AGENT",
    "EXPLORE_AGENT",
    "GENERAL_PURPOSE_AGENT",
    "PLAN_AGENT",
    "STATUSLINE_SETUP_AGENT",
    "VERIFICATION_AGENT",
    "are_explore_plan_agents_enabled",
    "get_built_in_agents",
]


# ---------------------------------------------------------------------------
# Feature gates (translation of GrowthBook checks — stub for now)
# ---------------------------------------------------------------------------


def are_explore_plan_agents_enabled() -> bool:
    """Check if Explore/Plan agents are enabled (GrowthBook ``tengu_amber_stoat``).

    Defaults to ``True`` when the feature flag system is not present.
    Controlled by env ``CLAUDE_EXPLORE_PLAN_AGENTS`` for local override.
    """
    env = os.environ.get("CLAUDE_EXPLORE_PLAN_AGENTS", "").lower()
    if env == "false":
        return False
    return True  # default: enabled


def _is_verification_agent_enabled() -> bool:
    """Check if verification agent is enabled (GrowthBook ``tengu_hive_evidence``)."""
    env = os.environ.get("CLAUDE_VERIFICATION_AGENT", "").lower()
    return env == "true"


def _is_sdk_entry() -> bool:
    """Check if running via SDK entry (non-interactive)."""
    return os.environ.get("CLAUDE_CODE_SDK_ENTRY", "").lower() == "true"


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


# -- disallowed tools for read-only agents (Explore, Plan, verification) --
_READ_ONLY_DISALLOWED_TOOLS = [
    "Write", "Edit", "NotebookEdit", "Bash",
    "EnterWorktree", "ExitWorktree",
    "TodoWrite", "Agent", "Task",
    "SendMessage", "TeamCreate", "TeamDelete",
]


# ---------------------------------------------------------------------------
# 1. General-Purpose Agent
# ---------------------------------------------------------------------------


def _general_purpose_system_prompt(**_kwargs: Any) -> str:
    return (
        f"{_SHARED_PREFIX} When you complete the task, respond with a concise report "
        "covering what was done and any key findings — the caller will relay this to "
        "the user, so it only needs the essentials.\n\n"
        f"{_SHARED_GUIDELINES}"
    )


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
# 2. Explore Agent (read-only code exploration)
# ---------------------------------------------------------------------------


def _explore_system_prompt(**_kwargs: Any) -> str:
    return """\
You are an Explore agent for Claude Code, Anthropic's official CLI for Claude.

=== CRITICAL: READ-ONLY MODE ===
You are in READ-ONLY mode. You MUST NOT modify any files.
You have NO write tools available. Do not attempt to:
- Create files
- Edit files
- Write files
- Run bash commands that modify the filesystem

Your ONLY purpose is to explore, search, read, and analyze code.
=================================

Given the user's question, explore the codebase to find the answer. Use search tools \
(Glob, Grep, Read) to investigate. Be thorough: check multiple locations, consider \
different naming conventions, look for related files.

When you complete the exploration, respond with a concise report of your findings. \
Include relevant file paths and code snippets to support your answer."""


EXPLORE_AGENT = BuiltInAgentDefinition(
    agent_type="Explore",
    when_to_use=(
        "Read-only agent for exploring and understanding code. Quickly searches "
        "across files, reads code, and provides analysis — without modifying anything. "
        "Use when you need to understand how something works, find where things are "
        "defined, or get an overview of code structure."
    ),
    disallowed_tools=_READ_ONLY_DISALLOWED_TOOLS,
    model="haiku",
    omit_claude_md=True,
    source=AgentSource.BUILT_IN,
    base_dir="built-in",
)
EXPLORE_AGENT._get_system_prompt = _explore_system_prompt


# ---------------------------------------------------------------------------
# 3. Plan Agent (read-only architecture planning)
# ---------------------------------------------------------------------------


def _plan_system_prompt(**_kwargs: Any) -> str:
    return """\
You are a Plan agent for Claude Code, Anthropic's official CLI for Claude.

=== CRITICAL: READ-ONLY MODE ===
You are in READ-ONLY mode. You MUST NOT modify any files.
You have NO write tools available. Do not attempt to:
- Create files
- Edit files
- Write files
- Run bash commands that modify the filesystem

Your ONLY purpose is to explore, analyze, and design an implementation plan.
=================================

Given the user's request, explore the codebase and design a detailed implementation plan. \
Use search tools to understand the existing code structure, then produce:

1. A clear summary of the current state of relevant code
2. A step-by-step implementation plan
3. Critical Files for Implementation — list every file that would need to be created or \
modified, with a brief note on what changes are needed

Be specific with file paths and line-level detail where possible. The plan should be \
actionable enough that another agent (or engineer) can execute it without further \
exploration."""


PLAN_AGENT = BuiltInAgentDefinition(
    agent_type="Plan",
    when_to_use=(
        "Read-only agent for planning implementations. Explores the codebase and "
        "designs a detailed, actionable implementation plan — including file paths, "
        "code structure analysis, and step-by-step changes needed. Use when you need "
        "to plan before coding."
    ),
    disallowed_tools=_READ_ONLY_DISALLOWED_TOOLS,
    model="inherit",
    omit_claude_md=True,
    source=AgentSource.BUILT_IN,
    base_dir="built-in",
)
PLAN_AGENT._get_system_prompt = _plan_system_prompt


# ---------------------------------------------------------------------------
# 4. Verification Agent (background read-only verification)
# ---------------------------------------------------------------------------


def _verification_system_prompt(**_kwargs: Any) -> str:
    return """\
You are a Verification agent for Claude Code, Anthropic's official CLI for Claude.

=== CRITICAL: READ-ONLY MODE ===
You are in READ-ONLY mode. You MUST NOT modify any files.
=================================

You verify that work was done correctly. Given the description of what was implemented, \
you must:

1. Read the relevant files and understand the changes
2. Check for correctness, completeness, and adherence to requirements
3. Look for edge cases, error handling, and potential issues
4. Verify tests exist and cover the changes (if applicable)
5. Check for consistency with the rest of the codebase

Your response MUST end with exactly one of:
  VERDICT: PASS — All checks passed, implementation is correct and complete
  VERDICT: FAIL — Significant issues found (list them)
  VERDICT: PARTIAL — Mostly correct but has minor issues (list them)

Be thorough but pragmatic. Focus on correctness over style."""


VERIFICATION_AGENT = BuiltInAgentDefinition(
    agent_type="verification",
    when_to_use=(
        "Background agent that verifies implementation correctness. Reads code, "
        "checks for issues, and provides a PASS/FAIL/PARTIAL verdict. Use after "
        "completing an implementation to validate the work."
    ),
    disallowed_tools=_READ_ONLY_DISALLOWED_TOOLS,
    background=True,
    color=AgentColorName.RED,
    critical_system_reminder=(
        "REMINDER: You are a verification agent. Do NOT modify files. "
        "Your job is to READ and VERIFY, then give a VERDICT."
    ),
    source=AgentSource.BUILT_IN,
    base_dir="built-in",
)
VERIFICATION_AGENT._get_system_prompt = _verification_system_prompt


# ---------------------------------------------------------------------------
# 5. Claude Code Guide Agent
# ---------------------------------------------------------------------------


def _claude_code_guide_system_prompt(**_kwargs: Any) -> str:
    return """\
You are a Claude Code Guide agent. You help users understand how to use Claude Code, \
its features, configuration, and capabilities.

You have access to Read, search, and web tools. Use them to find relevant documentation, \
configuration files, and examples.

Provide concise, actionable guidance. If the user asks about something specific, look \
it up rather than guessing. Point users to specific files and settings when relevant."""


CLAUDE_CODE_GUIDE_AGENT = BuiltInAgentDefinition(
    agent_type="claude-code-guide",
    when_to_use=(
        "Helps users understand Claude Code features, configuration, and capabilities. "
        "Use when the user asks 'how do I...' questions about Claude Code itself."
    ),
    tools=["Read", "Glob", "Grep", "WebFetch", "WebSearch", "ToolSearch"],
    model="haiku",
    permission_mode="dontAsk",
    source=AgentSource.BUILT_IN,
    base_dir="built-in",
)
CLAUDE_CODE_GUIDE_AGENT._get_system_prompt = _claude_code_guide_system_prompt


# ---------------------------------------------------------------------------
# 6. Statusline Setup Agent
# ---------------------------------------------------------------------------


def _statusline_setup_system_prompt(**_kwargs: Any) -> str:
    return """\
You are a Statusline Setup agent for Claude Code. You help users configure \
their terminal status line / prompt.

You can read configuration files and edit them to set up the user's preferred \
status line. Focus on:
- Detecting the user's shell (bash, zsh, fish)
- Reading existing shell config files
- Adding or modifying the relevant prompt/statusline configuration

Be careful with existing configurations — preserve what's already there and \
only add what's needed."""


STATUSLINE_SETUP_AGENT = BuiltInAgentDefinition(
    agent_type="statusline-setup",
    when_to_use=(
        "Configures the user's terminal status line / prompt. Use when the "
        "user wants to set up or modify their Claude Code status line display."
    ),
    tools=["Read", "Edit"],
    model="sonnet",
    color=AgentColorName.ORANGE,
    source=AgentSource.BUILT_IN,
    base_dir="built-in",
)
STATUSLINE_SETUP_AGENT._get_system_prompt = _statusline_setup_system_prompt


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_built_in_agents() -> list[BaseAgentDefinition]:
    """Return the list of built-in agents.

    Translation of getBuiltInAgents() from builtInAgents.ts.
    Always includes: general-purpose, statusline-setup.
    Conditionally includes: Explore, Plan, claude-code-guide, verification.
    """
    # Check if built-in agents are disabled (SDK mode)
    if os.environ.get("CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS", "").lower() == "true":
        return []

    agents: list[BaseAgentDefinition] = [
        GENERAL_PURPOSE_AGENT,
        STATUSLINE_SETUP_AGENT,
    ]

    if are_explore_plan_agents_enabled():
        agents.extend([EXPLORE_AGENT, PLAN_AGENT])

    if not _is_sdk_entry():
        agents.append(CLAUDE_CODE_GUIDE_AGENT)

    if _is_verification_agent_enabled():
        agents.append(VERIFICATION_AGENT)

    return agents
