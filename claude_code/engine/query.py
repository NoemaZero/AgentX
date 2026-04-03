"""Query loop — strict translation of query.ts queryLoop().

Core loop: send messages → get response → execute tools → repeat.
Includes auto-compact and retry integration.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from pydantic import Field

from claude_code.config import Config
from claude_code.services.api.client import LLMClient, StreamResult
from claude_code.services.tools.orchestration import run_tools
from claude_code.tools.base import BaseTool
from claude_code.data_types import (
    AssistantMessage,
    Message,
    StreamEvent,
    StreamEventType,
    ToolResultMessage,
    UserMessage,
)
from claude_code.pydantic_models import FrozenModel, MutableModel

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3


class QueryState(MutableModel):
    """Mutable query loop state — strict translation of query.ts State."""

    messages: list[Message] = Field(default_factory=list)
    turn_count: int = 0
    max_output_tokens_recovery_count: int = 0
    has_attempted_reactive_compact: bool = False


class QueryParams(FrozenModel):
    """Parameters for a query loop — translation of QueryParams."""

    messages: list[Message]
    system_prompt: str
    tools: list[BaseTool]
    tools_by_name: dict[str, BaseTool]
    client: LLMClient
    config: Config
    max_turns: int = 100
    cwd: str = ""
    engine: Any = None  # QueryEngine ref for sub-agent tool
    permission_checker: Any = None  # PermissionChecker
    auto_compact_tracker: Any = None  # AutoCompactTracker

    @classmethod
    def from_runtime(cls, **kwargs: Any) -> "QueryParams":
        """Construct params without copying mutable runtime references."""
        return cls.model_construct(**kwargs)


async def query(params: QueryParams) -> AsyncIterator[StreamEvent]:
    """Core query loop — strict translation of query.ts queryLoop().

    Loop structure (must not change):
    while True:
        1. yield stream_request_start
        2. Check auto-compact
        3. Call streaming API (with retry)
        4. Run tool orchestration runTools()
        5. Increment turn_count
        6. Check max_turns
        7. No tool calls → break
    """
    state = QueryState(messages=list(params.messages))
    openai_tools = [t.to_openai_tool() for t in params.tools if t.is_enabled()]
    # We'll use params.messages as a shared mutable list for message sync
    shared_messages = params.messages  # this IS the list from the caller

    while True:
        # 1. Signal request start
        yield StreamEvent(type=StreamEventType.STREAM_REQUEST_START, data={"turn": state.turn_count})

        # 2. Auto-compact check
        if params.auto_compact_tracker is not None:
            compacted = await params.auto_compact_tracker.maybe_compact(
                messages=state.messages,
                system_prompt=params.system_prompt,
            )
            if compacted is not None:
                yield StreamEvent(type=StreamEventType.AUTO_COMPACT, data={
                    "before": len(state.messages),
                    "after": len(compacted),
                })
                state.messages = compacted
                # Sync shared messages
                shared_messages.clear()
                shared_messages.extend(compacted)

        # 3. Call streaming API
        assistant_msg: AssistantMessage | None = None
        api_error: str | None = None

        try:
            async for event in params.client.stream_chat(
                messages=state.messages,
                system_prompt=params.system_prompt,
                tools=openai_tools if openai_tools else None,
                max_tokens=params.config.max_tokens,
            ):
                yield event

                if event.type == StreamEventType.STREAM_END and isinstance(event.data, StreamResult):
                    assistant_msg = event.data.message
                elif event.type == StreamEventType.ERROR:
                    api_error = str(event.data)
        except Exception as exc:
            api_error = str(exc)
            logger.error("API stream error: %s", api_error)

            # Retry logic for retryable errors
            status_code = getattr(exc, "status_code", None)
            if status_code in (429, 500, 502, 503, 529):
                import asyncio

                delay_s = min(2 ** state.turn_count, 30)
                logger.info("Retrying after %ds (status %s)", delay_s, status_code)
                await asyncio.sleep(delay_s)
                continue

        if api_error:
            yield StreamEvent(type=StreamEventType.QUERY_ERROR, data=api_error)
            return

        if assistant_msg is None:
            yield StreamEvent(type=StreamEventType.QUERY_ERROR, data="No response from API")
            return

        # Add assistant message to conversation
        state.messages.append(assistant_msg)
        shared_messages.append(assistant_msg)

        # Yield assistant message for display
        if assistant_msg.content:
            yield StreamEvent(type=StreamEventType.ASSISTANT_MESSAGE, data=assistant_msg.content)

        # 4. Check for tool calls
        tool_calls = assistant_msg.tool_calls
        if not tool_calls:
            # No tool calls → done
            yield StreamEvent(type=StreamEventType.QUERY_COMPLETE, data={"turns": state.turn_count + 1})
            return

        # Signal tool execution
        for tc in tool_calls:
            func = tc.get("function", {})
            yield StreamEvent(type=StreamEventType.TOOL_USE, data={
                "id": tc.get("id", ""),
                "name": func.get("name", ""),
            })

        # 5. Run tool orchestration with permission checker
        tool_results = await run_tools(
            tool_calls=tool_calls,
            tools_by_name=params.tools_by_name,
            cwd=params.cwd,
            permission_checker=params.permission_checker,
            engine=params.engine,
        )

        # Add tool results to conversation
        for result in tool_results:
            state.messages.append(result)
            shared_messages.append(result)
            yield StreamEvent(type=StreamEventType.TOOL_RESULT, data={
                "tool_call_id": result.tool_call_id,
                "content": result.content[:500] + "..." if len(result.content) > 500 else result.content,
            })

        # 6. Increment turn count
        state.turn_count += 1

        # 6.5. Drain agent notifications (background agent completions)
        if params.engine is not None and hasattr(params.engine, "drain_agent_notifications"):
            notifications = params.engine.drain_agent_notifications()
            for notification in notifications:
                notification_msg = UserMessage(content=notification)
                state.messages.append(notification_msg)
                shared_messages.append(notification_msg)
                yield StreamEvent(type=StreamEventType.AGENT_NOTIFICATION, data=notification)

        # 7. Check max turns
        if state.turn_count >= params.max_turns:
            yield StreamEvent(type=StreamEventType.MAX_TURNS_REACHED, data={"turns": state.turn_count})
            return
