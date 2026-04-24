"""Shared fixtures for real-API tests.

Loads .env via python-dotenv, provides reusable config/client/tool helpers,
and a concurrent scenario warmup pattern for fast test execution.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
from typing import Any

import pytest
from dotenv import load_dotenv

from AgentX.config import Config, load_config
from AgentX.data_types import (
    Message,
    StreamEvent,
    StreamEventType,
    UserMessage,
)
from AgentX.engine.query import QueryParams, query
from AgentX.permissions.checker import PermissionChecker
from AgentX.services.api.client import LLMClient
from AgentX.tools.base import BaseTool, ToolParameter, ToolParameterType, ToolResult
from AgentX.utils.hooks import HookManager

# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------

_ENV_PATH = pathlib.Path(__file__).resolve().parent.parent / ".env"

_env_loaded = False


def _ensure_env() -> None:
    global _env_loaded
    if _env_loaded:
        return
    if not _ENV_PATH.exists():
        pytest.skip(".env file not found — real-API tests require it")
    load_dotenv(_ENV_PATH, override=True)
    _env_loaded = True


def get_env(key: str) -> str:
    _ensure_env()
    val = os.environ.get(key, "")
    if not val:
        pytest.skip(f"env missing key: {key}")
    return val


# ---------------------------------------------------------------------------
# Config / Client
# ---------------------------------------------------------------------------


def make_real_config(**overrides: Any) -> Config:
    defaults = {
        "model": get_env("model"),
        "api_key": get_env("api-key"),
        "base_url": get_env("base-url"),
        "provider": get_env("provider"),
        "max_tokens": 1024,
        "max_turns": 10,
        "ssl_verify": False,
    }
    defaults.update(overrides)
    return load_config(**defaults)


def make_real_client(config: Config | None = None) -> LLMClient:
    return LLMClient(config or make_real_config())


# ---------------------------------------------------------------------------
# GetWeatherTool — simple deterministic tool for tool-call tests
# ---------------------------------------------------------------------------


class GetWeatherTool(BaseTool):
    """Simple tool for testing tool-call round-trips with real LLM."""

    name: str = "get_weather"
    is_read_only: bool = True
    is_concurrency_safe: bool = True

    def get_description(self) -> str:
        return "Get current weather for a city. Returns a short weather description."

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="city",
                type=ToolParameterType.STRING,
                description="City name, e.g. 'Beijing'",
            )
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        city = tool_input.get("city", "Unknown")
        return ToolResult(data=f"Weather in {city}: 25°C, sunny, humidity 40%")


# ---------------------------------------------------------------------------
# QueryParams builder
# ---------------------------------------------------------------------------


def build_query_params(
    client: LLMClient,
    *,
    messages: list[Message] | None = None,
    system_prompt: str = "You are a helpful assistant. Be concise.",
    tools: list[BaseTool] | None = None,
    max_turns: int = 10,
    hook_manager: HookManager | None = None,
    config: Config | None = None,
    engine: Any = None,
    permission_checker: PermissionChecker | None = None,
) -> QueryParams:
    tool_list = tools if tools is not None else []
    by_name = {t.name: t for t in tool_list}
    cfg = config or make_real_config()
    msgs: list[Message] = messages if messages is not None else [UserMessage(content="Hello")]
    return QueryParams.from_runtime(
        messages=msgs,
        system_prompt=system_prompt,
        tools=tool_list,
        tools_by_name=by_name,
        client=client,
        config=cfg,
        max_turns=max_turns,
        cwd="/tmp",
        engine=engine,
        permission_checker=permission_checker or PermissionChecker(mode="bypassPermissions"),
        hook_manager=hook_manager,
    )


# ---------------------------------------------------------------------------
# Event collection helpers
# ---------------------------------------------------------------------------


async def collect_query_events(params: QueryParams) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    async for ev in query(params):
        events.append(ev)
    return events


def events_of_type(events: list[StreamEvent], t: StreamEventType) -> list[StreamEvent]:
    return [e for e in events if e.type == t]


# ---------------------------------------------------------------------------
# Concurrent scenario runner
# ---------------------------------------------------------------------------


async def run_scenario_simple(prompt: str = "What is 2+2? Answer with just the number.") -> list[StreamEvent]:
    """Run a simple text-only query (no tools)."""
    client = make_real_client()
    params = build_query_params(client, messages=[UserMessage(content=prompt)])
    return await collect_query_events(params)


async def run_scenario_tool(
    prompt: str = "What's the weather in Tokyo? Use get_weather tool.",
    system_prompt: str | None = None,
    max_turns: int = 10,
) -> list[StreamEvent]:
    """Run a tool-call query with GetWeatherTool."""
    tool = GetWeatherTool()
    client = make_real_client()
    params = build_query_params(
        client,
        messages=[UserMessage(content=prompt)],
        tools=[tool],
        system_prompt=system_prompt or WEATHER_REPLY_SYSTEM_PROMPT,
        max_turns=max_turns,
    )
    return await collect_query_events(params)


# ---------------------------------------------------------------------------
# System prompt for tool-forced scenarios
# ---------------------------------------------------------------------------

WEATHER_SYSTEM_PROMPT = (
    "You are a helpful assistant. When asked about weather, "
    "you MUST use the get_weather tool. Be concise."
)

WEATHER_REPLY_SYSTEM_PROMPT = (
    "You are a helpful assistant. When asked about weather, "
    "you MUST use the get_weather tool. After getting the result, "
    "reply to the user with the weather info. Be concise."
)


# ---------------------------------------------------------------------------
# Progress reporter plugin: prints [1/56] style progress
# ---------------------------------------------------------------------------


class _ProgressReporter:
    """Pytest plugin that prints [n/total] progress before each test result.

    Works in both normal mode and xdist (``-n N``) mode:
    * Normal: ``pytest_collection_modifyitems`` fires locally.
    * xdist master: ``pytest_xdist_node_collection_finished`` fires once
      per worker with the *full* set of collected test IDs; we grab the
      count from the first worker to report.
    """

    def __init__(self) -> None:
        self._counter = 0
        self._total = 0

    # -- non-xdist: collection happens in this process ----------------------
    def pytest_collection_modifyitems(self, items: list) -> None:  # type: ignore[override]
        self._total = len(items)

    # -- xdist master: each worker reports its full collection ---------------
    def pytest_xdist_node_collection_finished(self, node: Any, ids: list) -> None:  # type: ignore[override]
        if self._total == 0:
            self._total = len(ids)

    # -- progress line for every completed test ------------------------------
    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.when == "call":
            self._counter += 1
            if report.passed:
                status = "✅ PASS"
            elif report.failed:
                status = "❌ FAIL"
            else:
                status = "⏭️ SKIP"
            short_id = report.nodeid.split("::", 1)[-1] if "::" in report.nodeid else report.nodeid
            total_str = str(self._total) if self._total else "?"
            print(f"  [{self._counter}/{total_str}] {status}  {short_id}")


def pytest_configure(config: Any) -> None:
    # Register only on master (or when not using xdist) to avoid duplicate output.
    # Guard: only register xdist hook if pytest-xdist is installed.
    reporter = _ProgressReporter()
    try:
        import xdist  # noqa: F401
    except ImportError:
        # Remove the xdist-only hook so pluggy doesn't reject it
        if hasattr(reporter, "pytest_xdist_node_collection_finished"):
            delattr(reporter.__class__, "pytest_xdist_node_collection_finished")
    if not hasattr(config, "workerinput"):
        config.pluginmanager.register(reporter, "progress_reporter")
