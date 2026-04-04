"""Dynamic prompt generation — translation of tools/AgentTool/prompt.ts.

Generates the AgentTool description that includes available agent types,
usage guidelines, and examples.
"""

from __future__ import annotations

from typing import Any

from claude_code.tools.agent_tool.constants import AGENT_TOOL_NAME
from claude_code.tools.agent_tool.definitions import BaseAgentDefinition
from claude_code.tools.tool_names import (
    FILE_READ_TOOL_NAME,
    FILE_WRITE_TOOL_NAME,
    GLOB_TOOL_NAME,
    SEND_MESSAGE_TOOL_NAME,
)

__all__ = ["format_agent_line", "get_prompt"]


def _get_tools_description(agent: BaseAgentDefinition) -> str:
    """Format the tools available to an agent for display."""
    tools = agent.tools
    disallowed = agent.disallowed_tools
    has_allowlist = tools is not None and len(tools) > 0
    has_denylist = disallowed is not None and len(disallowed) > 0

    if has_allowlist and has_denylist:
        deny_set = set(disallowed)  # type: ignore[arg-type]
        effective = [t for t in tools if t not in deny_set]  # type: ignore[union-attr]
        return ", ".join(effective) if effective else "None"
    if has_allowlist:
        return ", ".join(tools)  # type: ignore[arg-type]
    if has_denylist:
        return f"All tools except {', '.join(disallowed)}"  # type: ignore[arg-type]
    return "All tools"


def format_agent_line(agent: BaseAgentDefinition) -> str:
    """Format one agent line: ``- type: whenToUse (Tools: ...)``."""
    tools_desc = _get_tools_description(agent)
    return f"- {agent.agent_type}: {agent.when_to_use} (Tools: {tools_desc})"


def get_prompt(
    agent_definitions: list[BaseAgentDefinition] | None = None,
    is_coordinator: bool = False,
    allowed_agent_types: list[str] | None = None,
) -> str:
    """Generate the full AgentTool prompt/description.

    Translation of getPrompt from prompt.ts.
    When *agent_definitions* is ``None`` the built-in agents are loaded
    automatically so the prompt is always usable.
    """
    if agent_definitions is None:
        from claude_code.tools.agent_tool.built_in import get_built_in_agents

        agent_definitions = get_built_in_agents()

    effective_agents = (
        [a for a in agent_definitions if a.agent_type in allowed_agent_types]
        if allowed_agent_types
        else agent_definitions
    )

    agent_list = "\n".join(format_agent_line(a) for a in effective_agents)

    shared = f"""Launch a new agent to handle complex, multi-step tasks autonomously.

The {AGENT_TOOL_NAME} tool launches specialized agents (subprocesses) that autonomously \
handle complex tasks. Each agent type has specific capabilities and tools available to it.

Available agent types and the tools they have access to:
{agent_list}

When using the {AGENT_TOOL_NAME} tool, specify a subagent_type parameter to select which \
agent type to use. If omitted, the general-purpose agent is used."""

    if is_coordinator:
        return shared

    when_not_to_use = f"""
When NOT to use the {AGENT_TOOL_NAME} tool:
- If you want to read a specific file path, use the {FILE_READ_TOOL_NAME} tool or \
the {GLOB_TOOL_NAME} tool instead of the {AGENT_TOOL_NAME} tool, to find the match \
more quickly
- If you are searching for a specific class definition like "class Foo", use the \
{GLOB_TOOL_NAME} tool instead, to find the match more quickly
- If you are searching for code within a specific file or set of 2-3 files, use the \
{FILE_READ_TOOL_NAME} tool instead of the {AGENT_TOOL_NAME} tool, to find the match \
more quickly
- Other tasks that are not related to the agent descriptions above
"""

    usage_notes = f"""
Usage notes:
- Always include a short description (3-5 words) summarizing what the agent will do
- Launch multiple agents concurrently whenever possible, to maximize performance; to \
do that, use a single message with multiple tool uses
- When the agent is done, it will return a single message back to you. The result \
returned by the agent is not visible to the user. To show the user the result, you \
should send a text message back to the user with a concise summary of the result.
- You can optionally run agents in the background using the run_in_background parameter. \
When an agent runs in the background, you will be automatically notified when it \
completes — do NOT sleep, poll, or proactively check on its progress. Continue with \
other work or respond to the user instead.
- **Foreground vs background**: Use foreground (default) when you need the agent's \
results before you can proceed — e.g., research agents whose findings inform your \
next steps. Use background when you have genuinely independent work to do in parallel.
- To continue a previously spawned agent, use {SEND_MESSAGE_TOOL_NAME} with the agent's \
ID or name as the `to` field. The agent resumes with its full context preserved. Each \
Agent invocation starts fresh — provide a complete task description.
- The agent's outputs should generally be trusted
- Clearly tell the agent whether you expect it to write code or just to do research \
(search, file reads, web fetches, etc.), since it is not aware of the user's intent
- If the user specifies that they want you to run agents "in parallel", you MUST send \
a single message with multiple {AGENT_TOOL_NAME} tool use content blocks."""

    writing_prompt = f"""
## Writing the prompt

Brief the agent like a smart colleague who just walked into the room — it hasn't seen \
this conversation, doesn't know what you've tried, doesn't understand why this task \
matters.
- Explain what you're trying to accomplish and why.
- Describe what you've already learned or ruled out.
- Give enough context about the surrounding problem that the agent can make judgment \
calls rather than just following a narrow instruction.
- If you need a short response, say so ("report in under 200 words").
- Lookups: hand over the exact command. Investigations: hand over the question — \
prescribed steps become dead weight when the premise is wrong.

Terse command-style prompts produce shallow, generic work.

**Never delegate understanding.** Don't write "based on your findings, fix the bug" \
or "based on the research, implement it." Those phrases push synthesis onto the agent \
instead of doing it yourself. Write prompts that prove you understood: include file \
paths, line numbers, what specifically to change."""

    examples = f"""
Example usage:

<example_agent_descriptions>
"test-runner": use this agent after you are done writing code to run tests
"greeting-responder": use this agent to respond to user greetings with a friendly joke
</example_agent_descriptions>

<example>
user: "Please write a function that checks if a number is prime"
assistant: I'm going to use the {FILE_WRITE_TOOL_NAME} tool to write the following code:
<code>
function isPrime(n) {{
  if (n <= 1) return false
  for (let i = 2; i * i <= n; i++) {{
    if (n % i === 0) return false
  }}
  return true
}}
</code>
<commentary>
Since a significant piece of code was written and the task was completed, now use the \
test-runner agent to run the tests
</commentary>
assistant: Uses the {AGENT_TOOL_NAME} tool to launch the test-runner agent
</example>

<example>
user: "Hello"
<commentary>
Since the user is greeting, use the greeting-responder agent to respond with a friendly joke
</commentary>
assistant: "I'm going to use the {AGENT_TOOL_NAME} tool to launch the greeting-responder agent"
</example>
"""

    return f"{shared}{when_not_to_use}{usage_notes}{writing_prompt}\n{examples}"
