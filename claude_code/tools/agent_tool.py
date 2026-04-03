"""AgentTool — strict translation of tools/AgentTool/."""

from __future__ import annotations

from typing import Any

from claude_code.data_types import AgentContextMode, AgentModel, ToolParameterType, ToolResult
from claude_code.tools.base import BaseTool, ToolParameter
from claude_code.tools.tool_names import AGENT_TOOL_NAME, LEGACY_AGENT_TOOL_NAME

AGENT_DESCRIPTION = """Launch a new agent to handle complex, multi-step tasks autonomously.

The Agent tool launches specialized agents (subprocesses) that autonomously handle complex tasks. Each agent type has specific capabilities and tools available to it.

When NOT to use the Agent tool:
- If you want to read a specific file path, use the Read tool or the Glob tool instead of the Agent tool, to find the match more quickly
- If you are searching for a specific class definition like "class Foo", use the Glob tool instead, to find the match more quickly
- If you are searching for code within a specific file or set of 2-3 files, use the Read tool instead of the Agent tool, to find the match more quickly
- Other tasks that are not related to the agent descriptions above


Usage notes:
- Always include a short description (3-5 words) summarizing what the agent will do
- Launch multiple agents concurrently whenever possible, to maximize performance; to do that, use a single message with multiple tool uses
- When the agent is done, it will return a single message back to you. The result returned by the agent is not visible to the user. To show the user the result, you should send a text message back to the user with a concise summary of the result.
- You can optionally run agents in the background using the run_in_background parameter. When an agent runs in the background, you will be automatically notified when it completes \u2014 do NOT sleep, poll, or proactively check on its progress. Continue with other work or respond to the user instead.
- **Foreground vs background**: Use foreground (default) when you need the agent's results before you can proceed \u2014 e.g., research agents whose findings inform your next steps. Use background when you have genuinely independent work to do in parallel.
- Each Agent invocation starts fresh \u2014 provide a complete task description.
- The agent's outputs should generally be trusted
- Clearly tell the agent whether you expect it to write code or just to do research (search, file reads, web fetches, etc.), since it is not aware of the user's intent
- If the user specifies that they want you to run agents "in parallel", you MUST send a single message with multiple Agent tool use content blocks.

## Writing the prompt

Brief the agent like a smart colleague who just walked into the room \u2014 it hasn't seen this conversation, doesn't know what you've tried, doesn't understand why this task matters.
- Explain what you're trying to accomplish and why.
- Describe what you've already learned or ruled out.
- Give enough context about the surrounding problem that the agent can make judgment calls rather than just following a narrow instruction.
- If you need a short response, say so ("report in under 200 words").
- Lookups: hand over the exact command. Investigations: hand over the question \u2014 prescribed steps become dead weight when the premise is wrong.

Terse command-style prompts produce shallow, generic work.

**Never delegate understanding.** Don't write "based on your findings, fix the bug" or "based on the research, implement it." Those phrases push synthesis onto the agent instead of doing it yourself. Write prompts that prove you understood: include file paths, line numbers, what specifically to change."""


class AgentTool(BaseTool):
    name = AGENT_TOOL_NAME
    aliases = [LEGACY_AGENT_TOOL_NAME]
    is_read_only = False
    is_concurrency_safe = False
    should_defer = False

    def get_description(self) -> str:
        return AGENT_DESCRIPTION

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="description",
                type=ToolParameterType.STRING,
                description="A short (3-5 word) description of the task",
            ),
            ToolParameter(
                name="prompt",
                type=ToolParameterType.STRING,
                description="The task for the agent to perform",
            ),
            ToolParameter(
                name="subagent_type",
                type=ToolParameterType.STRING,
                description="The type of specialized agent to use for this task",
                required=False,
            ),
            ToolParameter(
                name="model",
                type=ToolParameterType.STRING,
                description=(
                    "Optional model override for this agent. Takes precedence over "
                    "the agent definition's model frontmatter. If omitted, uses the "
                    "agent definition's model, or inherits from the parent."
                ),
                required=False,
                enum=[AgentModel.SONNET.value, AgentModel.OPUS.value, AgentModel.HAIKU.value],
            ),
            ToolParameter(
                name="run_in_background",
                type=ToolParameterType.BOOLEAN,
                description="Set to true to run this agent in the background. You will be notified when it completes.",
                required=False,
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        prompt = tool_input.get("prompt", "")
        description = tool_input.get("description", "")
        subagent_type = tool_input.get("subagent_type", "")
        run_in_background = tool_input.get("run_in_background", False)

        if not prompt:
            return ToolResult(data="Error: prompt is required")

        # Import here to avoid circular dependency
        from claude_code.engine.query_engine import QueryEngine

        engine: QueryEngine | None = kwargs.get("engine")
        if engine is None:
            return ToolResult(data="Error: Agent tool requires an engine instance")

        # Resolve agent definition if subagent_type specified
        agent_definition = None
        if subagent_type:
            from claude_code.tools.agent_definitions import get_all_agent_definitions

            definitions = get_all_agent_definitions(cwd=cwd)
            for defn in definitions:
                if defn.name == subagent_type:
                    agent_definition = defn
                    break

        # Determine if this is a fork agent
        is_fork = bool(
            agent_definition and getattr(agent_definition, "context", None) == AgentContextMode.FORK
        )

        try:
            if run_in_background:
                # Background agent — launch async, return immediately
                from claude_code.agents.runner import run_agent_background

                result = await run_agent_background(
                    prompt=prompt,
                    description=description,
                    cwd=cwd,
                    parent_engine=engine,
                    agent_definition=agent_definition,
                    tool_use_id=kwargs.get("tool_use_id", ""),
                )
                return ToolResult(data=result)
            else:
                # Foreground agent
                from claude_code.agents.runner import run_agent_foreground

                result = await run_agent_foreground(
                    prompt=prompt,
                    description=description,
                    cwd=cwd,
                    parent_engine=engine,
                    is_fork=is_fork,
                    agent_definition=agent_definition,
                    parent_messages=engine.messages if is_fork else None,
                )
                return ToolResult(data=result)
        except Exception as e:
            return ToolResult(data=f"Agent error: {e}")
