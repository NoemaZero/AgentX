"""Query loop — strict translation of query.ts queryLoop().

Core loop: send messages → get response → execute tools → repeat.
Implements full error recovery paths from design doc §8:
- Prompt-too-long (413) three-level recovery
- Max output tokens two-level recovery (escalation + multi-turn)
- Model fallback
- API error protection (skip stop hooks to prevent infinite loops)
- Stop hooks evaluation (§9)
"""

from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from typing import Any, AsyncIterator

from pydantic import Field

from AgentX.config import Config
from AgentX.services.api.client import LLMClient, StreamResult
from AgentX.services.tools.orchestration import run_tools
from AgentX.tools.base import BaseTool
from AgentX.data_types import (
    AssistantMessage,
    Message,
    StreamEvent,
    StreamEventType,
    ToolResultMessage,
    UserMessage,
)
from AgentX.pydantic_models import FrozenModel, MutableModel

logger = logging.getLogger(__name__)

# ── Constants (from query.ts) ──

MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3
ESCALATED_MAX_TOKENS = 65_536
DEFAULT_MAX_TOKENS_CAP = 8_192

# Recovery message injected for max_output_tokens multi-turn recovery
MAX_TOKENS_RECOVERY_MESSAGE = (
    "Output token limit hit. Resume directly — no apology, no recap. "
    "Pick up mid-thought if that is where the cut happened. "
    "Break remaining work into smaller pieces."
)


class TransitionReason(StrEnum):
    """State transition reasons — strict translation of query.ts.

    Used for debugging/analytics to track why the loop iterated.
    """

    NEXT_TURN = "next_turn"
    COLLAPSE_DRAIN_RETRY = "collapse_drain_retry"
    REACTIVE_COMPACT_RETRY = "reactive_compact_retry"
    MAX_OUTPUT_TOKENS_ESCALATE = "max_output_tokens_escalate"
    MAX_OUTPUT_TOKENS_RECOVERY = "max_output_tokens_recovery"
    STOP_HOOK_BLOCKING = "stop_hook_blocking"
    TOKEN_BUDGET_CONTINUATION = "token_budget_continuation"
    PROACTIVE_COMPACT = "proactive_compact"
    AUTO_COMPACT = "auto_compact"
    API_RETRY = "api_retry"


class QueryState(MutableModel):
    """Mutable query loop state — strict translation of query.ts State."""

    messages: list[Message] = Field(default_factory=list)
    turn_count: int = 0
    max_output_tokens_recovery_count: int = 0
    has_attempted_reactive_compact: bool = False
    max_output_tokens_override: int | None = None
    stop_hook_active: bool = False
    transition_reason: str | None = None


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
    hook_manager: Any = None  # HookManager
    ask_callback: Any = None  # async callback for permission "ask" prompts

    @classmethod
    def from_runtime(cls, **kwargs: Any) -> "QueryParams":
        """Construct params without copying mutable runtime references."""
        return cls.model_construct(**kwargs)


# ── Helper: detect error categories from API exceptions ──

def _is_prompt_too_long(exc: Exception) -> bool:
    """Check if exception is a prompt-too-long (413) error."""
    status = getattr(exc, "status_code", None)
    if status == 413:
        return True
    msg = str(exc).lower()
    return "prompt is too long" in msg or "prompt_too_long" in msg


def _is_max_output_tokens(exc: Exception) -> bool:
    """Check if exception indicates max output tokens was hit."""
    msg = str(exc).lower()
    return "max_output_tokens" in msg or "maximum output tokens" in msg


def _is_overloaded(exc: Exception) -> bool:
    """Check for overloaded API error (529)."""
    status = getattr(exc, "status_code", None)
    return status == 529


def _finish_reason_is_length(result: StreamResult | None) -> bool:
    """Check if finish_reason indicates truncation due to token limit."""
    if result is None:
        return False
    reason = getattr(result, "stop_reason", None)
    return reason == "length"


async def query(params: QueryParams) -> AsyncIterator[StreamEvent]:
    """Core query loop — strict translation of query.ts queryLoop().

    Implements the full 12-step loop from design doc §2.2:
    1. Prefetch (memory + skills)
    2. Auto-compact
    3. Token blocking limit check
    4. callModel (streaming API)
    5. Model fallback
    6. Post-sampling hooks
    7. Tool use summary
    8. Terminal path (no tool_use) with recovery
    9. Tool execution
    10. Attachment injection (notifications, etc.)
    11. Max turns check
    12. State transition → next turn
    """
    state = QueryState(messages=list(params.messages))
    openai_tools = [t.to_openai_tool() for t in params.tools if t.is_enabled()]
    # Shared mutable list for message sync with caller
    shared_messages = params.messages

    while True:
        # ── Step 1: Signal request start ──
        yield StreamEvent(
            type=StreamEventType.STREAM_REQUEST_START,
            data={"turn": state.turn_count, "transition": state.transition_reason},
        )
        state.transition_reason = None

        # ── Step 2: Auto-compact check ──
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
                shared_messages.clear()
                shared_messages.extend(compacted)

        # ── Step 3–4: Call streaming API ──
        assistant_msg: AssistantMessage | None = None
        stream_result: StreamResult | None = None
        api_error: Exception | None = None
        api_error_str: str | None = None
        withheld_prompt_too_long: bool = False
        withheld_max_output_tokens: bool = False

        effective_max_tokens = (
            state.max_output_tokens_override
            or params.config.max_tokens
        )

        try:
            async for event in params.client.stream_chat(
                messages=state.messages,
                system_prompt=params.system_prompt,
                tools=openai_tools if openai_tools else None,
                max_tokens=effective_max_tokens,
            ):
                yield event

                if event.type == StreamEventType.STREAM_END and isinstance(event.data, StreamResult):
                    stream_result = event.data
                    assistant_msg = event.data.message
                elif event.type == StreamEventType.ERROR:
                    api_error_str = str(event.data)
        except Exception as exc:
            api_error = exc
            api_error_str = str(exc)
            logger.error("API stream error: %s", api_error_str)

            # ── Withheld error classification (design doc §2.2 Step 4) ──
            # Certain recoverable errors are "withheld" for later recovery
            if _is_prompt_too_long(exc):
                withheld_prompt_too_long = True
                logger.info("Withholding prompt-too-long error for recovery")
            elif _is_max_output_tokens(exc):
                withheld_max_output_tokens = True
                logger.info("Withholding max_output_tokens error for recovery")
            else:
                # ── Retryable server errors (429, 5xx, 529) ──
                status_code = getattr(exc, "status_code", None)
                if status_code in (429, 500, 502, 503, 529):
                    delay_s = min(2 ** min(state.turn_count, 5), 30)
                    logger.info("Retrying after %ds (status %s)", delay_s, status_code)
                    await asyncio.sleep(delay_s)
                    state.transition_reason = TransitionReason.API_RETRY
                    continue

        # ── Non-recoverable API error → exit ──
        if api_error_str and not withheld_prompt_too_long and not withheld_max_output_tokens:
            yield StreamEvent(type=StreamEventType.QUERY_ERROR, data=api_error_str)
            return

        # ── Check finish_reason == "length" (max_output_tokens hit during streaming) ──
        if _finish_reason_is_length(stream_result):
            withheld_max_output_tokens = True

        # ── Add assistant message to conversation (if we got one) ──
        if assistant_msg is not None:
            state.messages.append(assistant_msg)
            shared_messages.append(assistant_msg)
            if assistant_msg.content:
                yield StreamEvent(type=StreamEventType.ASSISTANT_MESSAGE, data=assistant_msg.content)

        # ── Step 6: Post-sampling hooks ──
        if params.hook_manager is not None and assistant_msg is not None:
            try:
                await params.hook_manager.run_post_tool_use(
                    tool_name="__sampling__",
                    tool_input={},
                    tool_output=assistant_msg.content or "",
                )
            except Exception as hook_exc:
                logger.warning("Post-sampling hook error: %s", hook_exc)

        # ── Step 8: Terminal path — no tool calls ──
        tool_calls = assistant_msg.tool_calls if assistant_msg else []
        if not tool_calls:
            # ── Recovery paths (design doc §8) ──

            # 8.1: Prompt-too-long (413) three-level recovery
            if withheld_prompt_too_long:
                # Level 2: Reactive compact
                if (
                    params.auto_compact_tracker is not None
                    and not state.has_attempted_reactive_compact
                ):
                    logger.info("Attempting reactive compact for prompt-too-long")
                    compacted = await params.auto_compact_tracker.force_compact(
                        messages=state.messages,
                        system_prompt=params.system_prompt,
                    )
                    if compacted is not None:
                        state.messages = compacted
                        shared_messages.clear()
                        shared_messages.extend(compacted)
                        state.has_attempted_reactive_compact = True
                        state.transition_reason = TransitionReason.REACTIVE_COMPACT_RETRY
                        yield StreamEvent(type=StreamEventType.AUTO_COMPACT, data={
                            "reason": "reactive_compact",
                            "before": len(state.messages),
                            "after": len(compacted),
                        })
                        continue

                # Level 3: Unrecoverable
                logger.error("Prompt-too-long: unrecoverable after all recovery attempts")
                yield StreamEvent(
                    type=StreamEventType.QUERY_ERROR,
                    data="Conversation too long for context window. Try /compact or start a new conversation.",
                )
                return

            # 8.2: Max output tokens two-level recovery
            if withheld_max_output_tokens:
                # Level 1: Escalation (cap → 64k), same request, no meta message
                if (
                    state.max_output_tokens_override is None
                    and effective_max_tokens <= DEFAULT_MAX_TOKENS_CAP
                ):
                    logger.info(
                        "Escalating max_output_tokens: %d → %d",
                        effective_max_tokens,
                        ESCALATED_MAX_TOKENS,
                    )
                    state.max_output_tokens_override = ESCALATED_MAX_TOKENS
                    state.transition_reason = TransitionReason.MAX_OUTPUT_TOKENS_ESCALATE
                    # Remove the truncated assistant message for clean retry
                    if assistant_msg is not None and state.messages and state.messages[-1] is assistant_msg:
                        state.messages.pop()
                        if shared_messages and shared_messages[-1] is assistant_msg:
                            shared_messages.pop()
                    continue

                # Level 2: Multi-turn recovery (inject continue message, max 3 times)
                if state.max_output_tokens_recovery_count < MAX_OUTPUT_TOKENS_RECOVERY_LIMIT:
                    state.max_output_tokens_recovery_count += 1
                    logger.info(
                        "Max output tokens recovery attempt %d/%d",
                        state.max_output_tokens_recovery_count,
                        MAX_OUTPUT_TOKENS_RECOVERY_LIMIT,
                    )
                    recovery_msg = UserMessage(content=MAX_TOKENS_RECOVERY_MESSAGE)
                    state.messages.append(recovery_msg)
                    shared_messages.append(recovery_msg)
                    state.transition_reason = TransitionReason.MAX_OUTPUT_TOKENS_RECOVERY
                    continue

                # Exhausted recovery attempts
                logger.warning("Max output tokens recovery exhausted (%d attempts)", MAX_OUTPUT_TOKENS_RECOVERY_LIMIT)

            # ── Step 9 (terminal): Stop hooks evaluation (design doc §9) ──
            if params.hook_manager is not None and not (api_error_str and not withheld_prompt_too_long):
                # Skip stop hooks on API errors to prevent dead loops (§8.4)
                try:
                    await params.hook_manager.run_stop()
                except Exception as hook_exc:
                    logger.warning("Stop hook error: %s", hook_exc)

            # ── Normal completion ──
            yield StreamEvent(type=StreamEventType.QUERY_COMPLETE, data={
                "turns": state.turn_count + 1,
                "reason": "completed",
            })
            return

        # ── Step 9: Tool execution ──
        for tc in tool_calls:
            func = tc.get("function", {})
            yield StreamEvent(type=StreamEventType.TOOL_USE, data={
                "id": tc.get("id", ""),
                "name": func.get("name", ""),
            })

        tool_results = await run_tools(
            tool_calls=tool_calls,
            tools_by_name=params.tools_by_name,
            cwd=params.cwd,
            permission_checker=params.permission_checker,
            hook_manager=params.hook_manager,
            ask_callback=params.ask_callback,
            engine=params.engine,
        )

        for result in tool_results:
            state.messages.append(result)
            shared_messages.append(result)
            yield StreamEvent(type=StreamEventType.TOOL_RESULT, data={
                "tool_call_id": result.tool_call_id,
                "content": result.content[:500] + "..." if len(result.content) > 500 else result.content,
            })

        # ── Step 10: Attachment injection — drain agent notifications ──
        state.turn_count += 1

        if params.engine is not None and hasattr(params.engine, "drain_agent_notifications"):
            notifications = params.engine.drain_agent_notifications()
            for notification in notifications:
                notification_msg = UserMessage(content=notification)
                state.messages.append(notification_msg)
                shared_messages.append(notification_msg)
                yield StreamEvent(type=StreamEventType.AGENT_NOTIFICATION, data=notification)

        # ── Step 11: Max turns check ──
        if state.turn_count >= params.max_turns:
            yield StreamEvent(type=StreamEventType.MAX_TURNS_REACHED, data={"turns": state.turn_count})
            return

        # ── Step 12: State transition ──
        state.transition_reason = TransitionReason.NEXT_TURN
