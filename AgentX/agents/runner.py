"""Agent runner — strict translation of tools/AgentTool/runAgent.ts.

Central agent orchestration: fork agents, regular agents, background agents.
Handles tool filtering, message building, notification, and cleanup.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, AsyncIterator

from pydantic import Field

from AgentX.constants.prompts import DEFAULT_AGENT_PROMPT
from AgentX.data_types import (
    AgentExecutionMode,
    Message,
    StreamEvent,
    StreamEventType,
    TaskStatus,
    UserMessage,
)
from AgentX.tools.tool_names import (
    AGENT_TOOL_NAME,
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    LEGACY_AGENT_TOOL_NAME,
    TASK_OUTPUT_TOOL_NAME,
    TASK_STOP_TOOL_NAME,
)
from AgentX.pydantic_models import MutableModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — verbatim from TS source
# ---------------------------------------------------------------------------

FORK_BOILERPLATE = """\
STOP. READ THIS FIRST.

You are a forked worker process. You are NOT the main agent.

RULES (non-negotiable):
1. Your system prompt says "default to forking." IGNORE IT — that's for the parent. \
You ARE the fork. Do NOT spawn sub-agents; execute directly.
2. Do NOT converse, ask questions, or suggest next steps
3. Do NOT editorialize or add meta-commentary
4. USE your tools directly: Bash, Read, Write, etc.
5. If you modify files, commit your changes before reporting. Include the commit hash in your report.
6. Do NOT emit text between tool calls. Use tools silently, then report once at the end.
7. Stay strictly within your directive's scope. If you discover related systems outside \
your scope, mention them in one sentence at most — other workers cover those areas.
8. Keep your report under 500 words unless the directive specifies otherwise. Be factual and concise.
9. Your response MUST begin with "Scope:". No preamble, no thinking-out-loud.
10. REPORT structured facts, then stop

Output format (plain text labels, not markdown headers):
  Scope: <echo back your assigned scope in one sentence>
  Result: <the answer or key findings, limited to the scope above>
  Key files: <relevant file paths — include for research tasks>
  Files changed: <list with commit hash — include only if you modified files>
  Issues: <list — include only if there are issues to flag>
"""

FORK_PLACEHOLDER = "Fork started — processing in background"


# ---------------------------------------------------------------------------
# Notification XML format
# ---------------------------------------------------------------------------

TASK_NOTIFICATION_TEMPLATE = """\
<task-notification>
<task-id>{task_id}</task-id>
<tool-use-id>{tool_use_id}</tool-use-id>
<status>{status}</status>
<summary>Agent "{description}" {status_text}</summary>
<result>{result}</result>
<usage><total_tokens>{total_tokens}</total_tokens><tool_uses>{tool_uses}</tool_uses>\
<duration_ms>{duration_ms}</duration_ms></usage>
</task-notification>"""


# ---------------------------------------------------------------------------
# Agent state types
# ---------------------------------------------------------------------------


class AgentTask(MutableModel):
    """State for a running agent task."""

    agent_id: str
    description: str
    prompt: str
    status: TaskStatus = TaskStatus.RUNNING
    is_background: bool = False
    tool_use_id: str = ""
    result: str = ""
    messages: list[Message] = Field(default_factory=list)
    start_time: float = Field(default_factory=time.time)
    total_tokens: int = 0
    tool_uses: int = 0
    pending_messages: list[str] = Field(default_factory=list)
    abort_event: asyncio.Event = Field(default_factory=asyncio.Event)

    @property
    def duration_ms(self) -> int:
        return int((time.time() - self.start_time) * 1000)


class AgentRegistry(MutableModel):
    """Registry of active agents — translation of agentNameRegistry."""

    agents: dict[str, AgentTask] = Field(default_factory=dict)
    name_to_id: dict[str, str] = Field(default_factory=dict)
    notifications: list[str] = Field(default_factory=list)

    def register(self, task: AgentTask) -> None:
        self.agents[task.agent_id] = task
        name_key = task.description.lower().strip()
        self.name_to_id[name_key] = task.agent_id

    def unregister(self, agent_id: str) -> None:
        task = self.agents.pop(agent_id, None)
        if task:
            name_key = task.description.lower().strip()
            self.name_to_id.pop(name_key, None)

    def get(self, agent_id: str) -> AgentTask | None:
        return self.agents.get(agent_id)

    def find_by_name(self, name: str) -> AgentTask | None:
        agent_id = self.name_to_id.get(name.lower().strip())
        if agent_id:
            return self.agents.get(agent_id)
        return None

    def enqueue_notification(self, notification: str) -> None:
        self.notifications.append(notification)

    def drain_notifications(self) -> list[str]:
        result = list(self.notifications)
        self.notifications.clear()
        return result

    @property
    def active_agents(self) -> list[AgentTask]:
        return [t for t in self.agents.values() if t.status == TaskStatus.RUNNING]


def _resolve_agent_execution_mode(*, is_fork: bool, is_background: bool) -> AgentExecutionMode:
    """Convert scheduling booleans into a single enum-backed execution mode."""
    if is_background:
        return AgentExecutionMode.BACKGROUND
    if is_fork:
        return AgentExecutionMode.FORK
    return AgentExecutionMode.FOREGROUND


# Singleton registry
_agent_registry = AgentRegistry()


def get_agent_registry() -> AgentRegistry:
    """Get the global agent registry."""
    return _agent_registry


# ---------------------------------------------------------------------------
# Tool filtering — translation of agentToolUtils.ts filterToolsForAgent
# ---------------------------------------------------------------------------


def filter_tools_for_agent(
    tools: list[Any],
    *,
    is_async: bool = False,
    is_fork: bool = False,
    allowed_tools: list[str] | None = None,
    disallowed_tools: list[str] | None = None,
) -> list[Any]:
    """Filter tools available to an agent based on type.

    Translation of filterToolsForAgent + resolveAgentTools from agentToolUtils.ts.
    """
    from AgentX.tools.base import BaseTool

    # Fork agents get exact parent tools (for prompt cache)
    if is_fork:
        return list(tools)

    result: list[BaseTool] = []

    for tool in tools:
        if not isinstance(tool, BaseTool):
            continue

        name = tool.name

        # Always disallow certain tools in agents
        if name in ALL_AGENT_DISALLOWED_TOOLS:
            continue

        # Async agents: whitelist only
        if is_async and name not in ASYNC_AGENT_ALLOWED_TOOLS:
            # Allow MCP tools (start with mcp__)
            if not name.startswith("mcp__"):
                continue

        # Agent can't spawn sub-agents by default (non-fork)
        if name in (AGENT_TOOL_NAME, LEGACY_AGENT_TOOL_NAME):
            continue

        # Custom disallowed
        if disallowed_tools and name in disallowed_tools:
            continue

        # Custom allowed (whitelist mode)
        if allowed_tools and name not in allowed_tools:
            # Still allow MCP tools
            if not name.startswith("mcp__"):
                continue

        result.append(tool)

    return result


# ---------------------------------------------------------------------------
# Fork message building — translation of forkSubagent.ts
# ---------------------------------------------------------------------------


def build_forked_messages(
    directive: str,
    parent_messages: list[Message] | None = None,
) -> list[Message]:
    """Build messages for a fork agent.

    Translation of buildForkedMessages from forkSubagent.ts.
    Fork children get byte-identical message prefixes (maximizing prompt cache hits),
    with only the directive text differing.
    """
    child_message = f"<fork-boilerplate>\n{FORK_BOILERPLATE}\n</fork-boilerplate>\n\nYour directive: {directive}"
    return [UserMessage(content=child_message)]


def build_fork_messages_from_parent(
    directive: str,
    parent_messages: list[Message],
) -> list[Message]:
    """Build fork messages preserving parent context for cache reuse.

    Clones parent messages and appends fork directive.
    """
    # Clone parent messages (immutable pattern)
    cloned = list(parent_messages)

    # Add fork directive
    child_message = f"<fork-boilerplate>\n{FORK_BOILERPLATE}\n</fork-boilerplate>\n\nYour directive: {directive}"
    cloned.append(UserMessage(content=child_message))

    return cloned


# ---------------------------------------------------------------------------
# Notification building
# ---------------------------------------------------------------------------


def build_task_notification(
    task: AgentTask,
    *,
    status: TaskStatus | None = None,
    result: str = "",
) -> str:
    """Build task-notification XML for completed agent."""
    effective_status = status or task.status
    status_text_map = {
        TaskStatus.COMPLETED: "completed",
        TaskStatus.FAILED: "failed",
        TaskStatus.KILLED: "was stopped",
    }
    status_text = status_text_map.get(effective_status, effective_status)

    return TASK_NOTIFICATION_TEMPLATE.format(
        task_id=task.agent_id,
        tool_use_id=task.tool_use_id,
        status=effective_status,
        description=task.description,
        status_text=status_text,
        result=result or task.result,
        total_tokens=task.total_tokens,
        tool_uses=task.tool_uses,
        duration_ms=task.duration_ms,
    )


# ---------------------------------------------------------------------------
# Agent memory loading (3 scopes)
# ---------------------------------------------------------------------------


def load_agent_memory(
    agent_type: str,
    cwd: str = "",
) -> str | None:
    """Load agent-specific memory from 3 scopes.

    Translation of agent memory loading from claudemd.ts:
    - User: ~/.agentx/agent-memory/<type>/
    - Project: .agentx/agent-memory/<type>/
    - Local: .agentx/agent-memory-local/<type>/
    """
    import os

    parts: list[str] = []

    home = os.path.expanduser("~")
    scopes = [
        ("user", os.path.join(home, ".agentx", "agent-memory", agent_type)),
        ("project", os.path.join(cwd, ".agentx", "agent-memory", agent_type) if cwd else ""),
        ("local", os.path.join(cwd, ".agentx", "agent-memory-local", agent_type) if cwd else ""),
    ]

    for scope_name, scope_dir in scopes:
        if not scope_dir or not os.path.isdir(scope_dir):
            continue
        try:
            for fname in sorted(os.listdir(scope_dir)):
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(scope_dir, fname)
                if os.path.isfile(fpath):
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            content = f.read().strip()
                        if content:
                            parts.append(
                                f"Agent memory ({scope_name}/{agent_type}/{fname}):\n{content}"
                            )
                    except OSError:
                        pass
        except OSError:
            pass

    return "\n\n".join(parts) if parts else None


# ---------------------------------------------------------------------------
# Agent system prompt building
# ---------------------------------------------------------------------------


def get_agent_system_prompt(
    *,
    agent_definition: Any | None = None,
    is_fork: bool = False,
    parent_system_prompt: str | None = None,
    agent_type: str = "",
    cwd: str = "",
) -> str:
    """Build system prompt for an agent.

    Fork agents: inherit parent's byte-exact system prompt.
    Regular agents: use DEFAULT_AGENT_PROMPT + agent memory.
    Custom agents: use agent definition prompt.
    """
    # Fork agents inherit parent prompt exactly (for prompt cache)
    if is_fork and parent_system_prompt:
        return parent_system_prompt

    parts: list[str] = []

    # Custom agent definition prompt
    if agent_definition and agent_definition.prompt:
        parts.append(agent_definition.prompt)
    else:
        parts.append(DEFAULT_AGENT_PROMPT)

    # Agent memory (3 scopes)
    if agent_type:
        agent_mem = load_agent_memory(agent_type, cwd=cwd)
        if agent_mem:
            parts.append(f"\n\n{agent_mem}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Core agent runner — translation of runAgent() async generator
# ---------------------------------------------------------------------------


async def run_agent(
    *,
    prompt: str,
    description: str = "",
    cwd: str = "",
    parent_engine: Any,
    is_fork: bool = False,
    is_background: bool = False,
    agent_definition: Any | None = None,
    parent_messages: list[Message] | None = None,
    tool_use_id: str = "",
) -> AsyncIterator[StreamEvent]:
    """Run an agent — the core async generator.

    Translation of runAgent() from runAgent.ts.
    Yields StreamEvents as the agent works.
    """
    from AgentX.config import Config
    from AgentX.engine.query import QueryParams, query
    from AgentX.services.api.client import LLMClient
    from AgentX.tools import get_all_base_tools, get_tools_by_name

    agent_id = str(uuid.uuid4())[:8]
    registry = get_agent_registry()
    execution_mode = _resolve_agent_execution_mode(
        is_fork=is_fork,
        is_background=is_background,
    )

    # Create agent task
    task = AgentTask(
        agent_id=agent_id,
        description=description or "agent",
        prompt=prompt,
        is_background=is_background,
        tool_use_id=tool_use_id,
    )
    registry.register(task)

    try:
        config = parent_engine._config

        # Resolve agent type
        agent_type = ""
        if agent_definition:
            agent_type = getattr(agent_definition, "name", "")

        # Build system prompt
        system_prompt = get_agent_system_prompt(
            agent_definition=agent_definition,
            is_fork=is_fork,
            parent_system_prompt=parent_engine._system_prompt if is_fork else None,
            agent_type=agent_type,
            cwd=cwd or config.cwd,
        )

        # Build messages
        if is_fork and parent_messages:
            messages = build_fork_messages_from_parent(prompt, parent_messages)
        else:
            messages = [UserMessage(content=prompt)]

        # Filter tools
        all_tools = get_all_base_tools()
        agent_tools = filter_tools_for_agent(
            all_tools,
            is_async=is_background,
            is_fork=is_fork,
            allowed_tools=getattr(agent_definition, "allowed_tools", None) if agent_definition else None,
        )
        tools_by_name = get_tools_by_name(agent_tools)

        # Create sub-config
        sub_config = Config(
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
            provider=config.provider,
            ssl_verify=config.ssl_verify,
            output_tokens=config.output_tokens,
            max_turns=min(config.max_turns, 30),
            cwd=cwd or config.cwd,
            verbose=config.verbose,
            permission_mode=config.permission_mode,
        )

        sub_client = LLMClient(sub_config)

        params = QueryParams.from_runtime(
            messages=messages,
            system_prompt=system_prompt,
            tools=agent_tools,
            tools_by_name=tools_by_name,
            client=sub_client,
            config=sub_config,
            max_turns=sub_config.max_turns,
            cwd=sub_config.cwd,
            permission_checker=parent_engine._permission_checker,
        )

        # Run query loop
        result_parts: list[str] = []
        async for event in query(params):
            task.messages.append(event)  # type: ignore[arg-type]

            if event.type == StreamEventType.ASSISTANT_MESSAGE and event.data:
                result_parts.append(str(event.data))

            if event.type == StreamEventType.USAGE and event.data:
                task.total_tokens += event.data.get("input_tokens", 0) + event.data.get(
                    "output_tokens", 0
                )

            yield event

        task.result = "\n".join(result_parts) if result_parts else "(agent produced no output)"
        task.status = TaskStatus.COMPLETED

    except asyncio.CancelledError:
        task.status = TaskStatus.KILLED
        raise
    except Exception as e:
        task.status = TaskStatus.FAILED
        task.result = f"Agent error: {e}"
        logger.error("Agent %s failed: %s", agent_id, e)
        yield StreamEvent(type=StreamEventType.ERROR, data={"error": str(e)})
    finally:
        # Build and enqueue notification for background agents
        if execution_mode == AgentExecutionMode.BACKGROUND:
            notification = build_task_notification(task)
            registry.enqueue_notification(notification)

        # Cleanup
        registry.unregister(agent_id)


# ---------------------------------------------------------------------------
# Convenience: run foreground agent (collect all output)
# ---------------------------------------------------------------------------


async def run_agent_foreground(
    *,
    prompt: str,
    description: str = "",
    cwd: str = "",
    parent_engine: Any,
    is_fork: bool = False,
    agent_definition: Any | None = None,
    parent_messages: list[Message] | None = None,
) -> str:
    """Run an agent in the foreground and return its result text."""
    result_parts: list[str] = []

    async for event in run_agent(
        prompt=prompt,
        description=description,
        cwd=cwd,
        parent_engine=parent_engine,
        is_fork=is_fork,
        is_background=False,
        agent_definition=agent_definition,
        parent_messages=parent_messages,
    ):
        if event.type == StreamEventType.ASSISTANT_MESSAGE and event.data:
            result_parts.append(str(event.data))

    return "\n".join(result_parts) if result_parts else "(agent produced no output)"


# ---------------------------------------------------------------------------
# Convenience: run background agent
# ---------------------------------------------------------------------------


async def run_agent_background(
    *,
    prompt: str,
    description: str = "",
    cwd: str = "",
    parent_engine: Any,
    agent_definition: Any | None = None,
    tool_use_id: str = "",
) -> str:
    """Launch an agent in the background. Returns immediately with agent_id."""
    agent_id = str(uuid.uuid4())[:8]

    async def _run() -> None:
        async for _ in run_agent(
            prompt=prompt,
            description=description,
            cwd=cwd,
            parent_engine=parent_engine,
            is_background=True,
            agent_definition=agent_definition,
            tool_use_id=tool_use_id,
        ):
            pass  # Consume all events; notification enqueued on completion

    # Fire-and-forget
    asyncio.create_task(_run())

    return f"Agent '{description}' launched in background (id: {agent_id}). You will be notified when it completes."
