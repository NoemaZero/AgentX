"""Real-API tests for engine/query.py — 12-step query loop.

Optimization: all independent API calls run concurrently via asyncio.gather.
Tests share cached scenario results to avoid redundant LLM calls.

API calls per module: 7 (all concurrent) → wall time ≈ single call (~5-8s).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from claude_code.data_types import (
    AssistantMessage,
    Message,
    StreamEvent,
    StreamEventType,
    UserMessage,
)
from claude_code.engine.query import TransitionReason
from claude_code.services.api.client import StreamResult
from claude_code.utils.hooks import HookManager

from conftest import (
    GetWeatherTool,
    WEATHER_REPLY_SYSTEM_PROMPT,
    WEATHER_SYSTEM_PROMPT,
    build_query_params,
    collect_query_events,
    events_of_type,
    make_real_client,
)

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
        client = make_real_client()
        params = build_query_params(
            client,
            messages=[UserMessage(content="What is 2+2? Answer with just the number.")],
        )
        results["simple"] = await collect_query_events(params)

    async def _tool() -> None:
        tool = GetWeatherTool()
        client = make_real_client()
        params = build_query_params(
            client,
            messages=[UserMessage(content="What's the weather in Tokyo? Use get_weather tool.")],
            tools=[tool],
            system_prompt=WEATHER_REPLY_SYSTEM_PROMPT,
        )
        results["tool"] = await collect_query_events(params)

    # ---- Unique scenarios (side effects / special config) ----

    async def _msg_sync() -> None:
        msgs: list[Message] = [UserMessage(content="Say hello")]
        client = make_real_client()
        params = build_query_params(client, messages=msgs)
        await collect_query_events(params)
        results["msg_sync"] = msgs

    async def _hook_sampling() -> None:
        hm = HookManager()
        captured: list[tuple[str, str]] = []

        async def _rec(tool_name: str, tool_input: dict, tool_output: str) -> None:
            captured.append((tool_name, tool_output))

        hm.register("post_tool_use", _rec)
        client = make_real_client()
        params = build_query_params(
            client,
            messages=[UserMessage(content="Say 'test passed'")],
            hook_manager=hm,
        )
        events = await collect_query_events(params)
        results["hook_sampling_events"] = events
        results["hook_sampling_captured"] = captured

    async def _hook_stop() -> None:
        hm = HookManager()
        stop_calls: list[bool] = []

        async def _stop() -> None:
            stop_calls.append(True)

        hm.register("stop", _stop)
        client = make_real_client()
        params = build_query_params(
            client,
            messages=[UserMessage(content="Say ok")],
            hook_manager=hm,
        )
        await collect_query_events(params)
        results["hook_stop_calls"] = stop_calls

    async def _max_turns() -> None:
        tool = GetWeatherTool()
        client = make_real_client()
        params = build_query_params(
            client,
            messages=[UserMessage(content="Weather in NYC? Use get_weather tool.")],
            tools=[tool],
            max_turns=1,
            system_prompt=WEATHER_SYSTEM_PROMPT,
        )
        results["max_turns"] = await collect_query_events(params)

    async def _msg_accum() -> None:
        msgs: list[Message] = [UserMessage(content="Say hello")]
        client = make_real_client()
        params = build_query_params(client, messages=msgs)
        await collect_query_events(params)
        results["msg_accum"] = msgs

    # All 7 API calls run concurrently
    await asyncio.gather(
        _simple(),
        _tool(),
        _msg_sync(),
        _hook_sampling(),
        _hook_stop(),
        _max_turns(),
        _msg_accum(),
    )

    _cache.update(results)


# ===========================================================================
# Step 1: STREAM_REQUEST_START
# ===========================================================================


class TestStep1_RequestStart:
    async def test_first_event_is_request_start(self) -> None:
        await _warmup()
        events = _cache["simple"]

        assert len(events) >= 3
        assert events[0].type == StreamEventType.STREAM_REQUEST_START
        assert events[0].data["turn"] == 0
        assert events[0].data["transition"] is None


# ===========================================================================
# Step 2: Auto-compact (no tracker → no compact)
# ===========================================================================


class TestStep2_AutoCompact:
    async def test_no_compact_without_tracker(self) -> None:
        await _warmup()
        events = _cache["simple"]

        ac = events_of_type(events, StreamEventType.AUTO_COMPACT)
        assert len(ac) == 0


# ===========================================================================
# Step 3–4: Streaming API call
# ===========================================================================


class TestStep3_4_StreamingAPI:
    async def test_stream_produces_content_and_end(self) -> None:
        await _warmup()
        events = _cache["simple"]

        starts = events_of_type(events, StreamEventType.STREAM_START)
        ends = events_of_type(events, StreamEventType.STREAM_END)
        assert len(starts) >= 1
        assert len(ends) >= 1
        assert isinstance(ends[0].data, StreamResult)
        assert isinstance(ends[0].data.message, AssistantMessage)

    async def test_assistant_message_has_content(self) -> None:
        await _warmup()
        events = _cache["simple"]

        msgs = events_of_type(events, StreamEventType.ASSISTANT_MESSAGE)
        assert len(msgs) >= 1
        assert len(str(msgs[0].data)) > 0

    async def test_content_delta_events_present(self) -> None:
        await _warmup()
        events = _cache["simple"]

        deltas = events_of_type(events, StreamEventType.CONTENT_DELTA)
        assert len(deltas) >= 1

    async def test_shared_messages_synced(self) -> None:
        await _warmup()
        msgs = _cache["msg_sync"]

        assert len(msgs) >= 2
        assert isinstance(msgs[0], UserMessage)
        assert isinstance(msgs[-1], AssistantMessage)


# ===========================================================================
# Step 6: Post-sampling hooks
# ===========================================================================


class TestStep6_PostSamplingHooks:
    async def test_post_sampling_hook_called_with_real_content(self) -> None:
        await _warmup()
        captured = _cache["hook_sampling_captured"]

        assert len(captured) >= 1
        assert captured[0][0] == "__sampling__"
        assert len(captured[0][1]) > 0


# ===========================================================================
# Step 7: Tool use summary — TOOL_USE events
# ===========================================================================


class TestStep7_ToolUseSummary:
    async def test_tool_use_event_with_real_tool_call(self) -> None:
        await _warmup()
        events = _cache["tool"]

        tool_uses = events_of_type(events, StreamEventType.TOOL_USE)
        assert len(tool_uses) >= 1, (
            f"Expected TOOL_USE event, got types: {[e.type for e in events]}"
        )
        assert tool_uses[0].data["name"] == "get_weather"


# ===========================================================================
# Step 8: Terminal path (normal completion)
# ===========================================================================


class TestStep8_NormalTerminal:
    async def test_simple_question_completes(self) -> None:
        await _warmup()
        events = _cache["simple"]

        completions = events_of_type(events, StreamEventType.QUERY_COMPLETE)
        assert len(completions) == 1
        assert completions[0].data["reason"] == "completed"

    async def test_no_tool_calls_means_single_turn(self) -> None:
        await _warmup()
        events = _cache["simple"]

        starts = events_of_type(events, StreamEventType.STREAM_REQUEST_START)
        assert len(starts) == 1


# ===========================================================================
# Step 9: Tool execution with real LLM
# ===========================================================================


class TestStep9_ToolExecution:
    async def test_tool_call_roundtrip(self) -> None:
        await _warmup()
        events = _cache["tool"]

        results = events_of_type(events, StreamEventType.TOOL_RESULT)
        assert len(results) >= 1
        assert "Tokyo" in results[0].data["content"] or "25" in results[0].data["content"]

        msgs = events_of_type(events, StreamEventType.ASSISTANT_MESSAGE)
        assert len(msgs) >= 1
        final_text = str(msgs[-1].data).lower()
        assert any(w in final_text for w in ("25", "sunny", "tokyo", "weather", "celsius", "°")), \
            f"Expected weather info in: {final_text}"

    async def test_stop_hook_fires_on_completion(self) -> None:
        await _warmup()
        stop_calls = _cache["hook_stop_calls"]

        assert len(stop_calls) == 1

    async def test_tool_result_event_emitted(self) -> None:
        await _warmup()
        events = _cache["tool"]

        results = events_of_type(events, StreamEventType.TOOL_RESULT)
        if len(results) >= 1:
            assert "tool_call_id" in results[0].data
            assert "content" in results[0].data
            assert len(results[0].data["content"]) > 0


# ===========================================================================
# Step 10: Agent notifications — no engine, no crash
# ===========================================================================


class TestStep10_NoEngineNoCrash:
    async def test_no_notifications_without_engine(self) -> None:
        await _warmup()
        events = _cache["tool"]

        notifs = events_of_type(events, StreamEventType.AGENT_NOTIFICATION)
        assert len(notifs) == 0


# ===========================================================================
# Step 11: Max turns
# ===========================================================================


class TestStep11_MaxTurns:
    async def test_max_turns_stops_loop(self) -> None:
        await _warmup()
        events = _cache["max_turns"]

        tool_uses = events_of_type(events, StreamEventType.TOOL_USE)
        if len(tool_uses) >= 1:
            mt = events_of_type(events, StreamEventType.MAX_TURNS_REACHED)
            assert len(mt) == 1


# ===========================================================================
# Step 12: State transition — multi-turn tool flow
# ===========================================================================


class TestStep12_StateTransition:
    async def test_tool_flow_has_next_turn_transition(self) -> None:
        await _warmup()
        events = _cache["tool"]

        starts = events_of_type(events, StreamEventType.STREAM_REQUEST_START)
        tool_uses = events_of_type(events, StreamEventType.TOOL_USE)

        if len(tool_uses) >= 1:
            assert len(starts) >= 2
            assert starts[1].data["transition"] == TransitionReason.NEXT_TURN
            assert starts[1].data["turn"] == 1

    async def test_turn_count_increments(self) -> None:
        await _warmup()
        events = _cache["tool"]

        starts = events_of_type(events, StreamEventType.STREAM_REQUEST_START)
        tool_uses = events_of_type(events, StreamEventType.TOOL_USE)

        if len(tool_uses) >= 1 and len(starts) >= 2:
            assert starts[0].data["turn"] == 0
            assert starts[1].data["turn"] == 1


# ===========================================================================
# Integration
# ===========================================================================


class TestIntegration:
    async def test_complete_event_flow(self) -> None:
        await _warmup()
        events = _cache["simple"]
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

    async def test_tool_roundtrip_event_flow(self) -> None:
        await _warmup()
        events = _cache["tool"]
        types = [e.type for e in events]

        assert StreamEventType.STREAM_REQUEST_START in types
        tool_uses = events_of_type(events, StreamEventType.TOOL_USE)
        if len(tool_uses) >= 1:
            assert StreamEventType.TOOL_RESULT in types
            starts = events_of_type(events, StreamEventType.STREAM_REQUEST_START)
            assert len(starts) >= 2
            assert StreamEventType.QUERY_COMPLETE in types

    async def test_multiple_messages_accumulate(self) -> None:
        await _warmup()
        msgs = _cache["msg_accum"]

        assert len(msgs) >= 2
        assert isinstance(msgs[0], UserMessage)
        assert isinstance(msgs[-1], AssistantMessage)
