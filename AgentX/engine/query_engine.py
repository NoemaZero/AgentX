"""QueryEngine — strict translation of QueryEngine.ts.

Central orchestrator: builds prompts, manages messages, drives the query loop.
Integrates permission checker, auto-compact, task manager, and usage tracking.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from AgentX.config import Config
from AgentX.constants.prompts import get_system_prompt
from AgentX.engine.context import get_system_context, get_user_context
from AgentX.engine.query import QueryParams, query
from AgentX.permissions.checker import PermissionChecker
from AgentX.services.api.client import LLMClient
from AgentX.services.api.usage import UsageTracker
from AgentX.services.compact import AutoCompactTracker
from AgentX.tasks.manager import TaskManager
from AgentX.tools import get_all_base_tools, get_tools_by_name
from AgentX.tools.base import BaseTool
from AgentX.data_types import (
    Message,
    StreamEvent,
    StreamEventType,
    UserMessage,
)

logger = logging.getLogger(__name__)


class QueryEngine:
    """Central query engine — translation of QueryEngine.ts."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._client = LLMClient(config)
        self._tools = get_all_base_tools()
        self._tools_by_name = get_tools_by_name(self._tools)
        self._messages: list[Message] = []
        self._system_prompt: str = ""
        self._initialized = False

        # Subsystems
        self._permission_checker = PermissionChecker(mode=config.permission_mode)
        self._usage_tracker = UsageTracker()
        self._auto_compact_tracker = AutoCompactTracker(
            max_context_tokens=self._get_max_context_tokens(),
            max_output_tokens=self._config.output_tokens
        )
        self._task_manager = TaskManager()

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    @property
    def total_usage(self):
        return self._client.total_usage

    @property
    def permission_checker(self) -> PermissionChecker:
        return self._permission_checker

    @property
    def task_manager(self) -> TaskManager:
        return self._task_manager

    @property
    def usage_tracker(self) -> UsageTracker:
        return self._usage_tracker

    def _get_max_context_tokens(self) -> int:
        """Get max context tokens for the current model."""
        # 优先使用配置中指定的 context_tokens
        if self._config.context_tokens is not None:
            return self._config.context_tokens

        from AgentX.config import MODEL_CONTEXT_WINDOWS

        return MODEL_CONTEXT_WINDOWS.get(self._config.model, 128000)

    async def initialize(self) -> None:
        """Build system prompt with context (git status, CLAUDE.md, etc.)."""
        cwd = self._config.cwd

        # Gather context in parallel
        user_ctx = await get_user_context(cwd)
        system_ctx = await get_system_context(cwd)

        enabled_tools = {t.name for t in self._tools if t.is_enabled()}

        self._system_prompt = get_system_prompt(
            enabled_tools=enabled_tools,
            cwd=cwd,
            git_status=system_ctx.get("gitStatus"),
            claude_md=user_ctx.get("claudeMd"),
        )

        # Apply custom system prompt overrides
        if self._config.system_prompt:
            self._system_prompt = self._config.system_prompt

        if self._config.append_system_prompt:
            self._system_prompt += f"\n\n{self._config.append_system_prompt}"

        self._initialized = True

    def register_tool(self, tool: BaseTool) -> None:
        """Register a custom tool into the engine.

        The tool is added to the active tool list and becomes available
        in subsequent queries. Can be called before or after initialize().
        """
        self._tools.append(tool)
        self._tools_by_name[tool.name] = tool
        for alias in tool.aliases:
            self._tools_by_name[alias] = tool

    async def submit_message(self, user_input: str) -> AsyncIterator[StreamEvent]:
        """Submit a user message and stream the response.

        Translation of QueryEngine.submitMessage().
        Messages are synced via the shared mutable list passed to query().
        """
        if not self._initialized:
            await self.initialize()

        # Add user message
        self._messages.append(UserMessage(content=user_input))

        # Build query params — pass self._messages directly so query() can append
        params = QueryParams.from_runtime(
            messages=self._messages,
            system_prompt=self._system_prompt,
            tools=self._tools,
            tools_by_name=self._tools_by_name,
            client=self._client,
            config=self._config,
            fallback_model=self._config.fallback_model,
            max_turns=self._config.max_turns,
            cwd=self._config.cwd,
            engine=self,
            permission_checker=self._permission_checker,
            auto_compact_tracker=self._auto_compact_tracker,
        )

        # Run query loop — it will append assistant + tool messages to self._messages
        async for event in query(params):
            # Track usage from stream events
            if event.type == StreamEventType.USAGE and event.data:
                from AgentX.data_types import Usage

                usage = Usage(
                    input_tokens=event.data.get("input_tokens", 0),
                    output_tokens=event.data.get("output_tokens", 0),
                )
                self._usage_tracker.record(usage)
            yield event

    def drain_agent_notifications(self) -> list[str]:
        """Drain pending agent notifications (task-notification XML).

        Called by query loop to inject background agent completions.
        Merges from both:
          - TaskManager (background agents registered via AgentTool)
          - AgentRegistry (legacy path)
        """
        notifications: list[str] = []

        # Primary: TaskManager notifications (from run_async_agent_lifecycle)
        tm_notifications = self._task_manager.drain_notifications()
        if tm_notifications:
            notifications.extend(tm_notifications)

        # Secondary: AgentRegistry notifications (legacy agents/runner path)
        try:
            from AgentX.agents.runner import get_agent_registry
            registry_notifications = get_agent_registry().drain_notifications()
            if registry_notifications:
                notifications.extend(registry_notifications)
        except ImportError:
            pass

        return notifications

    async def cleanup(self) -> None:
        """Clean up resources (tasks, etc.)."""
        await self._task_manager.cleanup()
