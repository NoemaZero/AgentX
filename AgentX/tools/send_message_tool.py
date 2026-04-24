"""SendMessage tool — strict translation of SendMessageTool.

Supports two modes:
1. Coordinator mode: relay information from agents to the user
2. Agent messaging: send a message to a running/stopped agent
"""

from __future__ import annotations

from typing import Any

from AgentX.data_types import TaskStatus, ToolParameterType, ToolResult
from AgentX.tools.base import BaseTool, ToolParameter
from AgentX.tools.tool_names import SEND_MESSAGE_TOOL_NAME


class SendMessageTool(BaseTool):
    """Send a message to the user or to a running agent."""

    name = SEND_MESSAGE_TOOL_NAME

    def get_description(self) -> str:
        return (
            "Send a message to the user or to a named agent. "
            "In coordinator mode, relays information from agents to the user. "
            "When targeting an agent, the message is queued as a pending message."
        )

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="message",
                type=ToolParameterType.STRING,
                description="The message content to send",
            ),
            ToolParameter(
                name="target_agent",
                type=ToolParameterType.STRING,
                description="Optional: name of an agent to send the message to",
                required=False,
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        message = tool_input.get("message", "")
        target_agent = tool_input.get("target_agent", "")

        if not message:
            return ToolResult(data="Error: message is required")

        # Agent-to-agent messaging
        if target_agent:
            from AgentX.agents.runner import get_agent_registry

            registry = get_agent_registry()
            task = registry.find_by_name(target_agent)

            if task is None:
                return ToolResult(
                    data=f"Error: No agent found with name '{target_agent}'. "
                    f"Active agents: {[a.description for a in registry.active_agents]}"
                )

            if task.status == TaskStatus.RUNNING:
                task.pending_messages.append(message)
                return ToolResult(data=f"Message queued for agent '{target_agent}'")
            else:
                return ToolResult(
                    data=f"Agent '{target_agent}' is {task.status}. Cannot send message."
                )

        # Default: relay to user
        return ToolResult(data=message)
