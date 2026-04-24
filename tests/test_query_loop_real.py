"""Supplementary real-API tests for query loop — different prompts/cities.

Provides additional coverage diversity. Same concurrent warmup pattern.

API calls per module: 3 (all concurrent) → wall time ≈ single call (~5-8s).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from AgentX.data_types import (
    AssistantMessage,
    Message,
    StreamEvent,
    StreamEventType,
    UserMessage,
)
from AgentX.engine.query import TransitionReason
from AgentX.services.api.client import StreamResult
from AgentX.utils.hooks import HookManager

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
    if _cache:
        return

    results: dict[str, Any] = {}

    async def _simple() -> None:
        client = make_real_client()
        params = build_query_params(
            client,
            messages=[UserMessage(content="Count from 1 to 5.")],
        )
        results["simple"] = await collect_query_events(params)

    async def _tool() -> None:
        tool = GetWeatherTool()
        client = make_real_client()
        params = build_query_params(
            client,
            messages=[UserMessage(content="What's the weather in London? Use get_weather tool.")],
            tools=[tool],
            system_prompt=WEATHER_REPLY_SYSTEM_PROMPT,
        )
        results["tool"] = await collect_query_events(params)

    async def _max_turns() -> None:
        tool = GetWeatherTool()
        client = make_real_client()
        params = build_query_params(
            client,
            messages=[UserMessage(content="Weather in Shanghai? Use get_weather tool.")],
            tools=[tool],
            max_turns=1,
            system_prompt=WEATHER_SYSTEM_PROMPT,
        )
        results["max_turns"] = await collect_query_events(params)

    await asyncio.gather(_simple(), _tool(), _max_turns())
    _cache.update(results)


# ===========================================================================
# Step 1: Request start
# ===========================================================================


class TestStep1_RequestStart:
    async def test_first_event_is_request_start(self) -> None:
        await _warmup()
        events = _cache["simple"]

        assert events[0].type == StreamEventType.STREAM_REQUEST_START
        assert events[0].data["turn"] == 0
        assert events[0].data["transition"] is None


# ===========================================================================
# Step 3–4: Streaming API
# ===========================================================================


class TestStep3_4_StreamingAPI:
    async def test_stream_produces_content_and_end(self) -> None:
        await _warmup()
        events = _cache["simple"]

        ends = events_of_type(events, StreamEventType.STREAM_END)
        assert len(ends) >= 1
        assert isinstance(ends[0].data, StreamResult)
        assert isinstance(ends[0].data.message, AssistantMessage)

    async def test_content_delta_events_present(self) -> None:
        await _warmup()
        events = _cache["simple"]

        deltas = events_of_type(events, StreamEventType.CONTENT_DELTA)
        assert len(deltas) >= 1


# ===========================================================================
# Step 7: Tool use
# ===========================================================================


class TestStep7_ToolUseSummary:
    async def test_tool_use_event(self) -> None:
        await _warmup()
        events = _cache["tool"]

        tool_uses = events_of_type(events, StreamEventType.TOOL_USE)
        assert len(tool_uses) >= 1
        assert tool_uses[0].data["name"] == "get_weather"


# ===========================================================================
# Step 8: Normal completion
# ===========================================================================


class TestStep8_NormalTerminal:
    async def test_simple_question_completes(self) -> None:
        await _warmup()
        events = _cache["simple"]

        completions = events_of_type(events, StreamEventType.QUERY_COMPLETE)
        assert len(completions) == 1
        assert completions[0].data["reason"] == "completed"


# ===========================================================================
# Step 9: Tool execution
# ===========================================================================


class TestStep9_ToolExecution:
    async def test_tool_call_roundtrip(self) -> None:
        await _warmup()
        events = _cache["tool"]

        results = events_of_type(events, StreamEventType.TOOL_RESULT)
        assert len(results) >= 1

        msgs = events_of_type(events, StreamEventType.ASSISTANT_MESSAGE)
        assert len(msgs) >= 1


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
# Step 12: State transition
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


# ===========================================================================
# Integration
# ===========================================================================


class TestIntegration:
    async def test_complete_event_flow(self) -> None:
        await _warmup()
        events = _cache["simple"]
        types = [e.type for e in events]

        assert types[0] == StreamEventType.STREAM_REQUEST_START
        assert StreamEventType.STREAM_END in types
        assert StreamEventType.ASSISTANT_MESSAGE in types
        assert StreamEventType.QUERY_COMPLETE in types

    async def test_tool_roundtrip_event_flow(self) -> None:
        await _warmup()
        events = _cache["tool"]
        types = [e.type for e in events]

        assert StreamEventType.STREAM_REQUEST_START in types
        tool_uses = events_of_type(events, StreamEventType.TOOL_USE)
        if len(tool_uses) >= 1:
            assert StreamEventType.TOOL_RESULT in types
            assert StreamEventType.QUERY_COMPLETE in types
