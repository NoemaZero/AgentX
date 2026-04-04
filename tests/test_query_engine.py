"""Real-API tests for engine/query_engine.py — QueryEngine integration.

Optimization: all independent API calls run concurrently via asyncio.gather.
Tests share cached scenario results to avoid redundant LLM calls.

API calls per module: 6 (all concurrent) → wall time ≈ single call (~5-8s).
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import pytest

from claude_code.data_types import (
    AssistantMessage,
    Message,
    StreamEvent,
    StreamEventType,
    UserMessage,
)
from claude_code.engine.query import QueryParams, TransitionReason, query
from claude_code.engine.query_engine import QueryEngine
from claude_code.permissions.checker import PermissionChecker
from claude_code.services.api.client import LLMClient
from claude_code.services.api.usage import UsageTracker
from claude_code.services.compact import AutoCompactTracker
from claude_code.tasks.manager import TaskManager
from claude_code.utils.hooks import HookManager

from conftest import (
    GetWeatherTool,
    WEATHER_REPLY_SYSTEM_PROMPT,
    WEATHER_SYSTEM_PROMPT,
    events_of_type,
    make_real_config,
)


# ---------------------------------------------------------------------------
# Engine builder (real LLM, bypasses initialize() context gathering)
# ---------------------------------------------------------------------------


def _make_real_engine(
    tools: list | None = None,
    config_overrides: dict[str, Any] | None = None,
    system_prompt: str = "You are a helpful assistant. Be concise.",
) -> QueryEngine:
    cfg = make_real_config(**(config_overrides or {}))
    engine = QueryEngine.__new__(QueryEngine)

    tool_list = tools if tools is not None else []
    engine._config = cfg
    engine._client = LLMClient(cfg)
    engine._tools = tool_list
    engine._tools_by_name = {t.name: t for t in tool_list}
    engine._messages = []
    engine._system_prompt = system_prompt
    engine._initialized = True
    engine._permission_checker = PermissionChecker(mode="bypassPermissions")
    engine._usage_tracker = UsageTracker()
    engine._auto_compact_tracker = AutoCompactTracker(max_context_tokens=128_000)
    engine._task_manager = TaskManager()
    engine._hook_manager = None

    return engine


async def _collect_events(engine: QueryEngine, user_input: str) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    async for ev in engine.submit_message(user_input):
        events.append(ev)
    return events


# ---------------------------------------------------------------------------
# Module-level concurrent warmup
# ---------------------------------------------------------------------------

_cache: dict[str, Any] = {}


async def _warmup() -> None:
    """Run ALL independent scenarios concurrently. Called once per module."""
    if _cache:
        return

    results: dict[str, Any] = {}

    # ---- Shared read-only scenarios ----

    async def _simple() -> None:
        engine = _make_real_engine()
        events = await _collect_events(engine, "What is 5+5? Answer the number only.")
        results["simple_events"] = events
        results["simple_engine"] = engine

    async def _tool() -> None:
        tool = GetWeatherTool()
        engine = _make_real_engine(tools=[tool], system_prompt=WEATHER_REPLY_SYSTEM_PROMPT)
        events = await _collect_events(engine, "What's the weather in Tokyo? Use get_weather tool.")
        results["tool_events"] = events
        results["tool_engine"] = engine

    # ---- Unique scenarios ----

    async def _consecutive() -> None:
        engine = _make_real_engine()
        await _collect_events(engine, "first message")
        await _collect_events(engine, "second message")
        results["consecutive_engine"] = engine

    async def _hook_sampling() -> None:
        hm = HookManager()
        captured: list[str] = []

        async def _hook(tool_name: str, tool_input: dict, tool_output: str) -> None:
            captured.append(tool_output)

        hm.register("post_tool_use", _hook)

        engine = _make_real_engine()
        # Patch submit to pass hook_manager to query params
        engine._messages.append(UserMessage(content="Say 'hook test'"))
        params = QueryParams.from_runtime(
            messages=engine._messages,
            system_prompt=engine._system_prompt,
            tools=engine._tools,
            tools_by_name=engine._tools_by_name,
            client=engine._client,
            config=engine._config,
            max_turns=engine._config.max_turns,
            cwd=engine._config.cwd,
            engine=engine,
            permission_checker=engine._permission_checker,
            auto_compact_tracker=engine._auto_compact_tracker,
            hook_manager=hm,
        )
        from conftest import collect_query_events

        await collect_query_events(params)
        results["hook_captured"] = captured

    async def _max_turns() -> None:
        tool = GetWeatherTool()
        engine = _make_real_engine(
            tools=[tool],
            config_overrides={"max_turns": 1},
            system_prompt=WEATHER_SYSTEM_PROMPT,
        )
        events = await _collect_events(engine, "Weather in NYC? Use get_weather tool.")
        results["max_turns_events"] = events

    async def _msg_copy() -> None:
        engine = _make_real_engine()
        await _collect_events(engine, "hello")
        results["msg_copy_engine"] = engine

    # All 6 API calls (7 total including 2nd submit in consecutive) run concurrently
    await asyncio.gather(
        _simple(),
        _tool(),
        _consecutive(),
        _hook_sampling(),
        _max_turns(),
        _msg_copy(),
    )

    _cache.update(results)


# ===========================================================================
# Initialization
# ===========================================================================


class TestInitialization:
    async def test_engine_initializes_with_real_config(self) -> None:
        await _warmup()
        engine = _cache["simple_engine"]
        assert engine._initialized is True
        assert len(engine._system_prompt) > 0

    def test_config_has_real_values(self) -> None:
        engine = _make_real_engine()
        assert engine._config.api_key != ""
        assert engine._config.base_url != ""


# ===========================================================================
# Step 1: STREAM_REQUEST_START
# ===========================================================================


class TestStep1_RequestStart:
    async def test_first_event_is_request_start(self) -> None:
        await _warmup()
        events = _cache["simple_events"]

        assert events[0].type == StreamEventType.STREAM_REQUEST_START
        assert events[0].data["turn"] == 0

    async def test_user_message_added_before_query(self) -> None:
        await _warmup()
        engine = _cache["simple_engine"]

        assert len(engine._messages) >= 2
        assert isinstance(engine._messages[0], UserMessage)


# ===========================================================================
# Step 2: Auto-compact
# ===========================================================================


class TestStep2_AutoCompact:
    async def test_no_compact_for_short_conversation(self) -> None:
        await _warmup()
        events = _cache["simple_events"]

        ac = events_of_type(events, StreamEventType.AUTO_COMPACT)
        assert len(ac) == 0


# ===========================================================================
# Step 3–4: Streaming API
# ===========================================================================


class TestStep3_4_StreamingAPI:
    async def test_content_delta_forwarded(self) -> None:
        await _warmup()
        events = _cache["simple_events"]

        deltas = events_of_type(events, StreamEventType.CONTENT_DELTA)
        assert len(deltas) >= 1

    async def test_assistant_message_event_emitted(self) -> None:
        await _warmup()
        events = _cache["simple_events"]

        msgs = events_of_type(events, StreamEventType.ASSISTANT_MESSAGE)
        assert len(msgs) >= 1
        assert len(str(msgs[0].data)) > 0

    async def test_messages_synced_to_engine(self) -> None:
        await _warmup()
        engine = _cache["simple_engine"]

        assert len(engine._messages) == 2
        assert isinstance(engine._messages[0], UserMessage)
        assert isinstance(engine._messages[1], AssistantMessage)


# ===========================================================================
# Step 6: Post-sampling hooks
# ===========================================================================


class TestStep6_PostSamplingHooks:
    async def test_post_sampling_hook_fires(self) -> None:
        await _warmup()
        captured = _cache["hook_captured"]

        assert len(captured) >= 1
        assert len(captured[0]) > 0  # real content from LLM


# ===========================================================================
# Step 7: TOOL_USE events
# ===========================================================================


class TestStep7_ToolUse:
    async def test_tool_use_event_forwarded(self) -> None:
        await _warmup()
        events = _cache["tool_events"]

        tool_uses = events_of_type(events, StreamEventType.TOOL_USE)
        assert len(tool_uses) >= 1
        assert tool_uses[0].data["name"] == "get_weather"


# ===========================================================================
# Step 8: Terminal path
# ===========================================================================


class TestStep8_NormalTerminal:
    async def test_simple_question_completes(self) -> None:
        await _warmup()
        events = _cache["simple_events"]

        completions = events_of_type(events, StreamEventType.QUERY_COMPLETE)
        assert len(completions) == 1
        assert completions[0].data["reason"] == "completed"

    async def test_no_tool_calls_means_single_turn(self) -> None:
        await _warmup()
        events = _cache["simple_events"]

        starts = events_of_type(events, StreamEventType.STREAM_REQUEST_START)
        assert len(starts) == 1


# ===========================================================================
# Step 9: Tool execution
# ===========================================================================


class TestStep9_ToolExecution:
    async def test_tool_result_forwarded(self) -> None:
        await _warmup()
        events = _cache["tool_events"]

        results = events_of_type(events, StreamEventType.TOOL_RESULT)
        assert len(results) >= 1
        assert "Tokyo" in results[0].data["content"] or "25" in results[0].data["content"]

    async def test_tool_call_roundtrip_produces_final_answer(self) -> None:
        await _warmup()
        events = _cache["tool_events"]

        msgs = events_of_type(events, StreamEventType.ASSISTANT_MESSAGE)
        assert len(msgs) >= 1
        final_text = str(msgs[-1].data).lower()
        assert any(w in final_text for w in ("25", "sunny", "tokyo", "weather", "celsius", "°")), \
            f"Expected weather info in: {final_text}"


# ===========================================================================
# Step 10: Agent notifications
# ===========================================================================


class TestStep10_AgentNotifications:
    async def test_engine_ref_wired_no_crash(self) -> None:
        await _warmup()
        events = _cache["tool_events"]

        notifs = events_of_type(events, StreamEventType.AGENT_NOTIFICATION)
        assert len(notifs) == 0


# ===========================================================================
# Step 11: Max turns
# ===========================================================================


class TestStep11_MaxTurns:
    async def test_max_turns_stops_loop(self) -> None:
        await _warmup()
        events = _cache["max_turns_events"]

        tool_uses = events_of_type(events, StreamEventType.TOOL_USE)
        if len(tool_uses) >= 1:
            mt = events_of_type(events, StreamEventType.MAX_TURNS_REACHED)
            assert len(mt) == 1


# ===========================================================================
# Step 12: State transition
# ===========================================================================


class TestStep12_StateTransition:
    async def test_tool_flow_has_next_turn_transition(self) -> None:
        await _warmup()
        events = _cache["tool_events"]

        starts = events_of_type(events, StreamEventType.STREAM_REQUEST_START)
        tool_uses = events_of_type(events, StreamEventType.TOOL_USE)

        if len(tool_uses) >= 1:
            assert len(starts) >= 2
            assert starts[1].data["transition"] == TransitionReason.NEXT_TURN
            assert starts[1].data["turn"] == 1

    async def test_turn_count_increments(self) -> None:
        await _warmup()
        events = _cache["tool_events"]

        starts = events_of_type(events, StreamEventType.STREAM_REQUEST_START)
        tool_uses = events_of_type(events, StreamEventType.TOOL_USE)

        if len(tool_uses) >= 1 and len(starts) >= 2:
            assert starts[0].data["turn"] == 0
            assert starts[1].data["turn"] == 1


# ===========================================================================
# Message accumulation
# ===========================================================================


class TestMessageAccumulation:
    async def test_consecutive_submits_accumulate(self) -> None:
        await _warmup()
        engine = _cache["consecutive_engine"]

        assert len(engine._messages) == 4
        assert engine._messages[2].content == "second message"  # type: ignore[union-attr]

    async def test_messages_property_returns_copy(self) -> None:
        await _warmup()
        engine = _cache["msg_copy_engine"]

        msgs = engine.messages
        assert msgs == engine._messages
        assert msgs is not engine._messages


# ===========================================================================
# Properties & Cleanup
# ===========================================================================


class TestProperties:
    def test_permission_checker_property(self) -> None:
        engine = _make_real_engine()
        assert engine.permission_checker is engine._permission_checker

    def test_task_manager_property(self) -> None:
        engine = _make_real_engine()
        assert engine.task_manager is engine._task_manager

    def test_usage_tracker_property(self) -> None:
        engine = _make_real_engine()
        assert engine.usage_tracker is engine._usage_tracker


class TestCleanup:
    async def test_cleanup_runs_without_error(self) -> None:
        engine = _make_real_engine()
        await engine.cleanup()


# ===========================================================================
# Integration
# ===========================================================================


class TestIntegration:
    async def test_full_tool_flow_end_to_end(self) -> None:
        await _warmup()
        events = _cache["tool_events"]
        types = [e.type for e in events]

        assert types[0] == StreamEventType.STREAM_REQUEST_START
        assert StreamEventType.STREAM_END in types

        tool_uses = events_of_type(events, StreamEventType.TOOL_USE)
        if len(tool_uses) >= 1:
            assert tool_uses[0].data["name"] == "get_weather"
            assert StreamEventType.TOOL_RESULT in types
            starts = events_of_type(events, StreamEventType.STREAM_REQUEST_START)
            assert len(starts) >= 2
            assert starts[1].data["transition"] == TransitionReason.NEXT_TURN

        assert StreamEventType.ASSISTANT_MESSAGE in types
        assert StreamEventType.QUERY_COMPLETE in types

    async def test_complete_event_order_no_tools(self) -> None:
        await _warmup()
        events = _cache["simple_events"]
        types = [e.type for e in events]

        assert types[0] == StreamEventType.STREAM_REQUEST_START
        assert StreamEventType.STREAM_START in types
        assert StreamEventType.STREAM_END in types
        assert StreamEventType.ASSISTANT_MESSAGE in types
        assert StreamEventType.QUERY_COMPLETE in types

        end_idx = types.index(StreamEventType.STREAM_END)
        msg_idx = types.index(StreamEventType.ASSISTANT_MESSAGE)
        done_idx = types.index(StreamEventType.QUERY_COMPLETE)
        assert end_idx < msg_idx < done_idx
