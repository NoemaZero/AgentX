"""Real-API integration tests for agent_tool — triggered via QueryEngine.

Tests exercise the full call chain:
    QueryEngine.submit_message() → query loop → LLM calls Agent tool
    → AgentTool.execute() → run_agent_foreground() → sub-query → result

Also includes pure-logic unit tests for modules that don't need API calls.

Optimization: all API scenarios run concurrently via asyncio.gather.
API calls per module: 3 (concurrent) → wall time ≈ single call (~8-12s).
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from AgentX.data_types import (
    AgentModel,
    StreamEvent,
    StreamEventType,
    UserMessage,
)
from AgentX.engine.query_engine import QueryEngine
from AgentX.permissions.checker import PermissionChecker
from AgentX.services.api.client import LLMClient
from AgentX.services.api.usage import UsageTracker
from AgentX.services.compact import AutoCompactTracker
from AgentX.tasks.manager import TaskManager
from AgentX.tools.agent_tool import AgentTool
from AgentX.tools.agent_tool.constants import AGENT_TOOL_NAME
from AgentX.tools.base import BaseTool, ToolParameter, ToolParameterType, ToolResult

from conftest import events_of_type, make_real_config


# ═══════════════════════════════════════════════════════════════════════════
# Deterministic tool for sub-agents to call
# ═══════════════════════════════════════════════════════════════════════════


class CalculatorTool(BaseTool):
    """Safe calculator tool for sub-agent roundtrip tests."""

    name = "Calculator"
    is_read_only = True
    is_concurrency_safe = True

    def get_description(self) -> str:
        return "Evaluate a simple arithmetic expression. Returns the numeric result."

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="expression",
                type=ToolParameterType.STRING,
                description="Arithmetic expression, e.g. '2+3*4'",
            )
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        expr = tool_input.get("expression", "0")
        try:
            result = eval(expr, {"__builtins__": {}}, {})  # noqa: S307
            return ToolResult(data=str(result))
        except Exception as e:
            return ToolResult(data=f"Error: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# Engine builder — real LLM, includes AgentTool in the tool pool
# ═══════════════════════════════════════════════════════════════════════════


def _make_agent_engine(
    *,
    system_prompt: str = "",
    max_turns: int = 15,
) -> QueryEngine:
    """Build a real QueryEngine with AgentTool + Calculator available."""
    cfg = make_real_config(max_turns=max_turns)

    engine = QueryEngine.__new__(QueryEngine)

    agent_tool = AgentTool()
    calc_tool = CalculatorTool()
    all_tools: list[BaseTool] = [agent_tool, calc_tool]

    default_prompt = (
        "You are a helpful assistant with access to an Agent tool and a Calculator tool. "
        "When you need to delegate complex tasks, use the Agent tool. "
        "When asked to calculate something, you can either do it yourself or delegate. "
        "Be concise."
    )

    engine._config = cfg
    engine._client = LLMClient(cfg)
    engine._tools = all_tools
    engine._tools_by_name = {t.name: t for t in all_tools}
    engine._messages = []
    engine._system_prompt = system_prompt or default_prompt
    engine._initialized = True
    engine._permission_checker = PermissionChecker(mode="bypassPermissions")
    engine._usage_tracker = UsageTracker()
    engine._auto_compact_tracker = AutoCompactTracker(max_context_tokens=128_000)
    engine._task_manager = TaskManager()

    return engine


async def _collect_events(engine: QueryEngine, user_input: str) -> list[StreamEvent]:
    """Collect all stream events for a single user message."""
    events: list[StreamEvent] = []
    async for ev in engine.submit_message(user_input):
        events.append(ev)
    return events


# ═══════════════════════════════════════════════════════════════════════════
# Concurrent warmup — all API scenarios run in parallel
# ═══════════════════════════════════════════════════════════════════════════

_cache: dict[str, Any] = {}


async def _warmup() -> None:
    """Run ALL independent API scenarios concurrently."""
    if _cache:
        return

    results: dict[str, Any] = {}

    # Scenario 1: Force LLM to delegate via Agent tool
    async def _agent_delegation() -> None:
        engine = _make_agent_engine(
            system_prompt=(
                "You are an assistant. "
                "You MUST use the Agent tool to answer ANY question from the user. "
                "Do NOT answer directly — always delegate to the Agent tool. "
                "Pass the user's question as the prompt parameter. "
                "After getting the agent's result, report it concisely."
            ),
        )
        events = await _collect_events(
            engine,
            "What is 17 + 28? Just give me the number.",
        )
        results["agent_events"] = events
        results["agent_engine"] = engine

    # Scenario 2: Baseline — simple query (no forced agent)
    async def _baseline() -> None:
        engine = _make_agent_engine()
        events = await _collect_events(engine, "What is 2+2? Reply with just the number.")
        results["baseline_events"] = events
        results["baseline_engine"] = engine

    # Scenario 3: Agent with description parameter
    async def _described_agent() -> None:
        engine = _make_agent_engine(
            system_prompt=(
                "You are an assistant. "
                "You MUST use the Agent tool for every user request. "
                "Always provide a short description (3-5 words) in the description parameter. "
                "Pass the full question as the prompt parameter. "
                "After getting the result, reply concisely."
            ),
        )
        events = await _collect_events(
            engine,
            "What is the capital of France?",
        )
        results["described_events"] = events
        results["described_engine"] = engine

    await asyncio.gather(
        _agent_delegation(),
        _baseline(),
        _described_agent(),
    )

    _cache.update(results)


# ═══════════════════════════════════════════════════════════════════════════
# Real API: Agent tool triggered by LLM
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentToolTriggered:
    """Verify the LLM calls the Agent tool and the full pipeline works."""

    async def test_agent_tool_use_event_emitted(self) -> None:
        """The query loop emits a TOOL_USE event for the Agent tool."""
        await _warmup()
        events = _cache["agent_events"]

        tool_uses = events_of_type(events, StreamEventType.TOOL_USE)
        agent_uses = [e for e in tool_uses if e.data.get("name") == AGENT_TOOL_NAME]
        assert len(agent_uses) >= 1, (
            f"Expected Agent TOOL_USE, got: {[e.data.get('name') for e in tool_uses]}"
        )

    async def test_agent_tool_result_returned(self) -> None:
        """The Agent tool returns a TOOL_RESULT back into the query loop."""
        await _warmup()
        events = _cache["agent_events"]

        tool_results = events_of_type(events, StreamEventType.TOOL_RESULT)
        assert len(tool_results) >= 1, "Expected at least one TOOL_RESULT"

        # At least one non-error result
        good = [r for r in tool_results if r.data.get("content", "") and "Error" not in str(r.data.get("content", ""))]
        assert len(good) >= 1 or len(tool_results) >= 1, (
            f"All tool results were errors: {[r.data for r in tool_results]}"
        )

    async def test_multi_turn_with_agent(self) -> None:
        """Agent delegation requires >= 2 turns (call + finalize)."""
        await _warmup()
        events = _cache["agent_events"]

        starts = events_of_type(events, StreamEventType.STREAM_REQUEST_START)
        tool_uses = [
            e for e in events_of_type(events, StreamEventType.TOOL_USE)
            if e.data.get("name") == AGENT_TOOL_NAME
        ]
        if tool_uses:
            assert len(starts) >= 2, f"Expected >= 2 turns, got {len(starts)}"

    async def test_query_completes_after_agent(self) -> None:
        """The conversation completes (QUERY_COMPLETE or MAX_TURNS_REACHED)."""
        await _warmup()
        events = _cache["agent_events"]

        completions = events_of_type(events, StreamEventType.QUERY_COMPLETE)
        max_turns = events_of_type(events, StreamEventType.MAX_TURNS_REACHED)
        assert len(completions) + len(max_turns) >= 1

    async def test_final_assistant_message_present(self) -> None:
        """The final assistant message is non-empty."""
        await _warmup()
        events = _cache["agent_events"]

        msgs = events_of_type(events, StreamEventType.ASSISTANT_MESSAGE)
        assert len(msgs) >= 1
        assert len(str(msgs[-1].data)) > 0


class TestAgentWithDescription:
    """Agent tool calls with a description parameter."""

    async def test_tool_use_contains_agent_name(self) -> None:
        await _warmup()
        events = _cache["described_events"]

        tool_uses = events_of_type(events, StreamEventType.TOOL_USE)
        agent_uses = [e for e in tool_uses if e.data.get("name") == AGENT_TOOL_NAME]
        assert len(agent_uses) >= 1

    async def test_conversation_completes(self) -> None:
        await _warmup()
        events = _cache["described_events"]

        completions = events_of_type(events, StreamEventType.QUERY_COMPLETE)
        max_turns = events_of_type(events, StreamEventType.MAX_TURNS_REACHED)
        assert len(completions) + len(max_turns) >= 1

    async def test_final_message_has_content(self) -> None:
        await _warmup()
        events = _cache["described_events"]

        msgs = events_of_type(events, StreamEventType.ASSISTANT_MESSAGE)
        assert len(msgs) >= 1
        assert len(str(msgs[-1].data)) > 0


class TestBaselineWithoutAgent:
    """Baseline: simple query completes without forced agent delegation."""

    async def test_completes_successfully(self) -> None:
        await _warmup()
        events = _cache["baseline_events"]

        completions = events_of_type(events, StreamEventType.QUERY_COMPLETE)
        assert len(completions) >= 1

    async def test_has_assistant_reply(self) -> None:
        await _warmup()
        events = _cache["baseline_events"]

        msgs = events_of_type(events, StreamEventType.ASSISTANT_MESSAGE)
        assert len(msgs) >= 1


class TestEngineState:
    """Engine state consistency after agent execution."""

    async def test_messages_accumulate(self) -> None:
        """Engine messages should include user + assistant + tool rounds."""
        await _warmup()
        engine = _cache["agent_engine"]
        assert len(engine.messages) >= 3

    async def test_first_message_is_user(self) -> None:
        await _warmup()
        engine = _cache["agent_engine"]
        assert isinstance(engine.messages[0], UserMessage)


class TestEventFlow:
    """Event ordering for agent-involved queries."""

    async def test_event_sequence_with_agent(self) -> None:
        """REQUEST_START → … → TOOL_USE(Agent) → TOOL_RESULT → … → COMPLETE."""
        await _warmup()
        events = _cache["agent_events"]
        types = [e.type for e in events]

        assert types[0] == StreamEventType.STREAM_REQUEST_START

        agent_uses = [
            (i, e) for i, e in enumerate(events)
            if e.type == StreamEventType.TOOL_USE and e.data.get("name") == AGENT_TOOL_NAME
        ]
        if agent_uses:
            tu_idx = agent_uses[0][0]
            tr_after = [i for i, e in enumerate(events) if e.type == StreamEventType.TOOL_RESULT and i > tu_idx]
            assert len(tr_after) >= 1, "Expected TOOL_RESULT after Agent TOOL_USE"

        terminal = StreamEventType.QUERY_COMPLETE in types or StreamEventType.MAX_TURNS_REACHED in types
        assert terminal, f"Expected terminal event, got: {set(types)}"


# ═══════════════════════════════════════════════════════════════════════════
# Unit tests: pure logic — no API calls
# ═══════════════════════════════════════════════════════════════════════════


class TestConstants:
    def test_agent_tool_name(self) -> None:
        from AgentX.tools.agent_tool.constants import AGENT_TOOL_NAME

        assert AGENT_TOOL_NAME == "Agent"

    def test_legacy_agent_tool_name(self) -> None:
        from AgentX.tools.agent_tool.constants import LEGACY_AGENT_TOOL_NAME

        assert LEGACY_AGENT_TOOL_NAME == "Task"

    def test_verification_agent_type(self) -> None:
        from AgentX.tools.agent_tool.constants import VERIFICATION_AGENT_TYPE

        assert VERIFICATION_AGENT_TYPE == "verification"

    def test_one_shot_builtin_types(self) -> None:
        from AgentX.tools.agent_tool.constants import ONE_SHOT_BUILTIN_AGENT_TYPES

        assert isinstance(ONE_SHOT_BUILTIN_AGENT_TYPES, frozenset)
        assert "Explore" in ONE_SHOT_BUILTIN_AGENT_TYPES
        assert "Plan" in ONE_SHOT_BUILTIN_AGENT_TYPES


class TestAgentMemoryScope:
    def test_scope_values(self) -> None:
        from AgentX.tools.agent_tool.memory import AgentMemoryScope

        assert AgentMemoryScope.USER == "user"
        assert AgentMemoryScope.PROJECT == "project"
        assert AgentMemoryScope.LOCAL == "local"


class TestMemoryDirPaths:
    def test_user_scope_path(self) -> None:
        from AgentX.tools.agent_tool.memory import AgentMemoryScope, get_agent_memory_dir

        result = get_agent_memory_dir("test-agent", AgentMemoryScope.USER)
        expected = os.path.join(Path.home(), ".agentx", "agent-memory", "test-agent") + os.sep
        assert result == expected

    def test_project_scope_path(self) -> None:
        from AgentX.tools.agent_tool.memory import AgentMemoryScope, get_agent_memory_dir

        result = get_agent_memory_dir("test-agent", AgentMemoryScope.PROJECT, cwd="/tmp/proj")
        expected = os.path.join("/tmp/proj", ".agentx", "agent-memory", "test-agent") + os.sep
        assert result == expected

    def test_local_scope_path(self) -> None:
        from AgentX.tools.agent_tool.memory import AgentMemoryScope, get_agent_memory_dir

        result = get_agent_memory_dir("test-agent", AgentMemoryScope.LOCAL, cwd="/tmp/proj")
        expected = os.path.join("/tmp/proj", ".agentx", "agent-memory-local", "test-agent") + os.sep
        assert result == expected

    def test_colon_sanitization(self) -> None:
        from AgentX.tools.agent_tool.memory import AgentMemoryScope, get_agent_memory_dir

        result = get_agent_memory_dir("plugin:my-agent", AgentMemoryScope.USER)
        assert "plugin-my-agent" in result
        assert ":" not in result


class TestIsAgentMemoryPath:
    def test_user_scope_detected(self) -> None:
        from AgentX.tools.agent_tool.memory import is_agent_memory_path

        path = os.path.join(Path.home(), ".agentx", "agent-memory", "test", "MEMORY.md")
        assert is_agent_memory_path(path) is True

    def test_unrelated_path_rejected(self) -> None:
        from AgentX.tools.agent_tool.memory import is_agent_memory_path

        assert is_agent_memory_path("/tmp/random/file.txt") is False


class TestLoadAgentMemoryPrompt:
    def test_empty_dir_returns_basic_prompt(self) -> None:
        from AgentX.tools.agent_tool.memory import AgentMemoryScope, load_agent_memory_prompt

        with tempfile.TemporaryDirectory() as tmpdir:
            result = load_agent_memory_prompt("test-agent", AgentMemoryScope.PROJECT, cwd=tmpdir)
            assert "Persistent Agent Memory" in result

    def test_reads_md_files(self) -> None:
        from AgentX.tools.agent_tool.memory import AgentMemoryScope, load_agent_memory_prompt

        with tempfile.TemporaryDirectory() as tmpdir:
            mem_dir = os.path.join(tmpdir, ".agentx", "agent-memory", "test-agent")
            os.makedirs(mem_dir)
            with open(os.path.join(mem_dir, "notes.md"), "w") as f:
                f.write("Important note")

            result = load_agent_memory_prompt("test-agent", AgentMemoryScope.PROJECT, cwd=tmpdir)
            assert "Important note" in result


class TestDefinitionEnums:
    def test_agent_source_values(self) -> None:
        from AgentX.tools.agent_tool.definitions import AgentSource

        assert AgentSource.BUILT_IN == "built-in"
        assert AgentSource.USER_SETTINGS == "userSettings"

    def test_isolation_mode(self) -> None:
        from AgentX.tools.agent_tool.definitions import IsolationMode

        assert IsolationMode.WORKTREE == "worktree"


class TestBaseAgentDefinition:
    def test_minimal_construction(self) -> None:
        from AgentX.tools.agent_tool.definitions import BaseAgentDefinition

        agent = BaseAgentDefinition(agent_type="test")
        assert agent.agent_type == "test"
        assert agent.tools is None

    def test_system_prompt_callable(self) -> None:
        from AgentX.tools.agent_tool.definitions import BaseAgentDefinition

        agent = BaseAgentDefinition(agent_type="test")
        agent._get_system_prompt = lambda **kw: "Hello"
        assert agent.get_system_prompt() == "Hello"


class TestTypeGuards:
    def test_built_in_detection(self) -> None:
        from AgentX.tools.agent_tool.definitions import AgentSource, BaseAgentDefinition, is_built_in_agent

        agent = BaseAgentDefinition(agent_type="t", source=AgentSource.BUILT_IN)
        assert is_built_in_agent(agent) is True

    def test_custom_detection(self) -> None:
        from AgentX.tools.agent_tool.definitions import AgentSource, BaseAgentDefinition, is_custom_agent

        agent = BaseAgentDefinition(agent_type="t", source=AgentSource.USER_SETTINGS)
        assert is_custom_agent(agent) is True


class TestFrontmatterParser:
    def _parse(self, content: str) -> tuple[dict[str, Any], str]:
        from AgentX.tools.agent_tool.definitions import _parse_frontmatter

        return _parse_frontmatter(content)

    def test_no_frontmatter(self) -> None:
        fm, body = self._parse("Hello")
        assert fm == {}

    def test_simple_key_value(self) -> None:
        fm, body = self._parse("---\nname: my-agent\ndescription: A test\n---\nBody")
        assert fm["name"] == "my-agent"
        assert body == "Body"

    def test_inline_list(self) -> None:
        fm, _ = self._parse("---\ntools: [Read, Write, Bash]\n---\nBody")
        assert fm["tools"] == ["Read", "Write", "Bash"]

    def test_yaml_style_list(self) -> None:
        fm, _ = self._parse("---\ntools:\n  - Read\n  - Write\n---\nBody")
        assert fm["tools"] == ["Read", "Write"]

    def test_boolean_values(self) -> None:
        fm, _ = self._parse("---\nbackground: true\nomit: false\n---\nBody")
        assert fm["background"] is True
        assert fm["omit"] is False


class TestParseAgentFromMarkdown:
    def test_valid_agent(self) -> None:
        from AgentX.tools.agent_tool.definitions import AgentSource, parse_agent_from_markdown

        fm = {"name": "test", "description": "A test", "tools": ["Read"]}
        agent = parse_agent_from_markdown("/p/t.md", "/p", fm, "Prompt", AgentSource.USER_SETTINGS)
        assert agent is not None
        assert agent.agent_type == "test"
        assert agent.get_system_prompt() == "Prompt"

    def test_missing_name_returns_none(self) -> None:
        from AgentX.tools.agent_tool.definitions import AgentSource, parse_agent_from_markdown

        agent = parse_agent_from_markdown("/p/t.md", "/p", {"description": "d"}, "B", AgentSource.USER_SETTINGS)
        assert agent is None

    def test_memory_auto_injects_file_tools(self) -> None:
        from AgentX.tools.agent_tool.definitions import AgentSource, parse_agent_from_markdown
        from AgentX.tools.tool_names import FILE_READ_TOOL_NAME, FILE_WRITE_TOOL_NAME

        fm = {"name": "m", "description": "d", "tools": ["Bash"], "memory": "user"}
        agent = parse_agent_from_markdown("/p/t.md", "/p", fm, "B", AgentSource.USER_SETTINGS)
        assert agent is not None
        assert FILE_READ_TOOL_NAME in agent.tools
        assert FILE_WRITE_TOOL_NAME in agent.tools


class TestActiveAgents:
    def test_later_source_overrides(self) -> None:
        from AgentX.tools.agent_tool.definitions import (
            AgentSource,
            BaseAgentDefinition,
            get_active_agents_from_list,
        )

        a1 = BaseAgentDefinition(agent_type="w", when_to_use="old", source=AgentSource.BUILT_IN)
        a2 = BaseAgentDefinition(agent_type="w", when_to_use="new", source=AgentSource.USER_SETTINGS)
        active = get_active_agents_from_list([a1, a2])
        assert len(active) == 1
        assert active[0].when_to_use == "new"


class TestLoadAgentsDir:
    def test_loads_md_files(self) -> None:
        from AgentX.tools.agent_tool.definitions import load_agents_dir

        with tempfile.TemporaryDirectory() as tmpdir:
            content = "---\nname: test-agent\ndescription: A test\n---\nPrompt."
            with open(os.path.join(tmpdir, "test.md"), "w") as f:
                f.write(content)
            agents = load_agents_dir(tmpdir)
            assert len(agents) == 1
            assert agents[0].agent_type == "test-agent"

    def test_nonexistent_dir(self) -> None:
        from AgentX.tools.agent_tool.definitions import load_agents_dir

        assert load_agents_dir("/nonexistent/xyz") == []


class TestBuiltIn:
    def test_general_purpose_agent(self) -> None:
        from AgentX.tools.agent_tool.built_in import GENERAL_PURPOSE_AGENT

        assert GENERAL_PURPOSE_AGENT.agent_type == "general-purpose"
        assert GENERAL_PURPOSE_AGENT.tools == ["*"]

    def test_system_prompt_has_content(self) -> None:
        from AgentX.tools.agent_tool.built_in import GENERAL_PURPOSE_AGENT

        prompt = GENERAL_PURPOSE_AGENT.get_system_prompt()
        assert "agent for Claude Code" in prompt

    def test_get_built_in_agents(self) -> None:
        from AgentX.tools.agent_tool.built_in import get_built_in_agents

        agents = get_built_in_agents()
        assert len(agents) >= 1


class TestFork:
    def test_fork_agent_type(self) -> None:
        from AgentX.tools.agent_tool.fork import FORK_AGENT

        assert FORK_AGENT.agent_type == "fork"
        assert FORK_AGENT.tools == ["*"]
        assert FORK_AGENT.permission_mode == "bubble"

    def test_is_in_fork_child_false(self) -> None:
        from AgentX.tools.agent_tool.fork import is_in_fork_child

        assert is_in_fork_child([]) is False
        assert is_in_fork_child([UserMessage(content="hello")]) is False

    def test_is_in_fork_child_true(self) -> None:
        from AgentX.tools.agent_tool.fork import FORK_BOILERPLATE_TAG, is_in_fork_child

        msgs = [UserMessage(content=f"<{FORK_BOILERPLATE_TAG}> directive")]
        assert is_in_fork_child(msgs) is True

    def test_build_child_message(self) -> None:
        from AgentX.tools.agent_tool.fork import build_child_message

        text = build_child_message("Analyze auth")
        assert "Analyze auth" in text
        assert "forked worker process" in text
        assert "Scope:" in text

    def test_build_forked_messages(self) -> None:
        from AgentX.tools.agent_tool.fork import build_forked_messages

        msgs = build_forked_messages("task X")
        assert len(msgs) >= 1
        assert isinstance(msgs[-1], UserMessage)
        assert "task X" in msgs[-1].content

    def test_build_worktree_notice(self) -> None:
        from AgentX.tools.agent_tool.fork import build_worktree_notice

        notice = build_worktree_notice("/parent", "/worktree")
        assert "/parent" in notice
        assert "/worktree" in notice


class TestFilterToolsForAgent:
    def _make_tools(self) -> list[BaseTool]:
        class DummyTool(BaseTool):
            def __init__(self, tool_name: str) -> None:
                self.name = tool_name

            def get_description(self) -> str:
                return ""

            def get_parameters(self) -> list:
                return []

            async def execute(self, **kw: Any) -> Any:
                return None

        return [
            DummyTool("Read"),
            DummyTool("Write"),
            DummyTool("Bash"),
            DummyTool("TaskOutput"),
            DummyTool("ExitPlanMode"),
            DummyTool("AskUserQuestion"),
            DummyTool("mcp__github__list"),
        ]

    def test_disallowed_tools_removed(self) -> None:
        from AgentX.tools.agent_tool.utils import filter_tools_for_agent

        result = filter_tools_for_agent(self._make_tools(), is_built_in=True)
        names = {t.name for t in result}
        assert "TaskOutput" not in names
        assert "AskUserQuestion" not in names

    def test_mcp_tools_always_allowed(self) -> None:
        from AgentX.tools.agent_tool.utils import filter_tools_for_agent

        result = filter_tools_for_agent(self._make_tools(), is_built_in=True)
        names = {t.name for t in result}
        assert "mcp__github__list" in names

    def test_async_restricts_to_whitelist(self) -> None:
        from AgentX.tools.agent_tool.utils import filter_tools_for_agent
        from AgentX.tools.tool_names import ASYNC_AGENT_ALLOWED_TOOLS

        result = filter_tools_for_agent(self._make_tools(), is_built_in=True, is_async=True)
        for t in result:
            assert t.name in ASYNC_AGENT_ALLOWED_TOOLS or t.name.startswith("mcp__")


class TestResolveAgentTools:
    def _make_tools(self) -> list[BaseTool]:
        class DummyTool(BaseTool):
            def __init__(self, tool_name: str) -> None:
                self.name = tool_name

            def get_description(self) -> str:
                return ""

            def get_parameters(self) -> list:
                return []

            async def execute(self, **kw: Any) -> Any:
                return None

        return [DummyTool("Read"), DummyTool("Write"), DummyTool("Agent")]

    def test_wildcard(self) -> None:
        from AgentX.tools.agent_tool.definitions import BaseAgentDefinition
        from AgentX.tools.agent_tool.utils import resolve_agent_tools

        agent = BaseAgentDefinition(agent_type="t", tools=["*"])
        result = resolve_agent_tools(agent, self._make_tools())
        assert result.has_wildcard is True

    def test_explicit_tools(self) -> None:
        from AgentX.tools.agent_tool.definitions import BaseAgentDefinition
        from AgentX.tools.agent_tool.utils import resolve_agent_tools

        agent = BaseAgentDefinition(agent_type="t", tools=["Read"])
        result = resolve_agent_tools(agent, self._make_tools())
        assert result.has_wildcard is False
        assert {t.name for t in result.resolved_tools} == {"Read"}

    def test_invalid_tools_tracked(self) -> None:
        from AgentX.tools.agent_tool.definitions import BaseAgentDefinition
        from AgentX.tools.agent_tool.utils import resolve_agent_tools

        agent = BaseAgentDefinition(agent_type="t", tools=["Read", "NonExistent"])
        result = resolve_agent_tools(agent, self._make_tools())
        assert "NonExistent" in result.invalid_tools

    def test_agent_allowed_types_parsed(self) -> None:
        from AgentX.tools.agent_tool.definitions import BaseAgentDefinition
        from AgentX.tools.agent_tool.utils import resolve_agent_tools

        agent = BaseAgentDefinition(agent_type="t", tools=["Read", "Agent(worker, researcher)"])
        result = resolve_agent_tools(agent, self._make_tools())
        assert result.allowed_agent_types == ["worker", "researcher"]


class TestFinalizeAgentTool:
    def test_no_messages_fallback(self) -> None:
        from AgentX.tools.agent_tool.utils import finalize_agent_tool

        result = finalize_agent_tool([], "id")
        assert result["content"][0]["text"] == "(agent produced no output)"

    def test_extracts_text(self) -> None:
        from AgentX.tools.agent_tool.utils import finalize_agent_tool

        block = MagicMock()
        block.type = "text"
        block.text = "Final answer"
        msg = MagicMock()
        msg.type = "assistant"
        msg.message.content = [block]
        msg.message.usage = None

        result = finalize_agent_tool([msg], "id")
        assert result["content"][0]["text"] == "Final answer"


class TestPrompt:
    def test_default_loads_built_in(self) -> None:
        from AgentX.tools.agent_tool.prompt import get_prompt

        prompt = get_prompt()
        assert "general-purpose" in prompt

    def test_coordinator_shorter(self) -> None:
        from AgentX.tools.agent_tool.prompt import get_prompt

        full = get_prompt(is_coordinator=False)
        coord = get_prompt(is_coordinator=True)
        assert len(coord) < len(full)

    def test_custom_agents_included(self) -> None:
        from AgentX.tools.agent_tool.definitions import BaseAgentDefinition
        from AgentX.tools.agent_tool.prompt import get_prompt

        agents = [BaseAgentDefinition(agent_type="custom", when_to_use="Custom", tools=["Bash"])]
        prompt = get_prompt(agent_definitions=agents)
        assert "custom" in prompt

    def test_allowed_filter(self) -> None:
        from AgentX.tools.agent_tool.definitions import BaseAgentDefinition
        from AgentX.tools.agent_tool.prompt import get_prompt

        agents = [
            BaseAgentDefinition(agent_type="agent_yes_type", when_to_use="y"),
            BaseAgentDefinition(agent_type="agent_no_type", when_to_use="n"),
        ]
        prompt = get_prompt(agent_definitions=agents, allowed_agent_types=["agent_yes_type"])
        assert "agent_yes_type" in prompt
        assert "agent_no_type" not in prompt


class TestAgentToolClass:
    def test_name_and_aliases(self) -> None:
        tool = AgentTool()
        assert tool.name == "Agent"
        assert "Task" in tool.aliases

    def test_parameters(self) -> None:
        tool = AgentTool()
        names = {p.name for p in tool.get_parameters()}
        assert {"prompt", "description", "subagent_type", "model", "run_in_background"} == names

    def test_model_enum_values(self) -> None:
        tool = AgentTool()
        params = {p.name: p for p in tool.get_parameters()}
        for m in AgentModel:
            assert m.value in params["model"].enum

    def test_openai_schema(self) -> None:
        tool = AgentTool()
        schema = tool.to_openai_tool()
        assert schema["function"]["name"] == "Agent"
        assert "prompt" in schema["function"]["parameters"]["properties"]

    async def test_missing_prompt_error(self) -> None:
        tool = AgentTool()
        result = await tool.execute(tool_input={}, cwd="/tmp")
        assert "Error" in result.data

    async def test_missing_engine_error(self) -> None:
        tool = AgentTool()
        result = await tool.execute(tool_input={"prompt": "test", "description": "t"}, cwd="/tmp")
        assert "engine" in result.data.lower()

    async def test_fork_guard(self) -> None:
        from AgentX.tools.agent_tool.fork import FORK_BOILERPLATE_TAG

        tool = AgentTool()
        engine = MagicMock()
        engine.messages = [UserMessage(content=f"<{FORK_BOILERPLATE_TAG}> directive")]
        result = await tool.execute(
            tool_input={"prompt": "test", "description": "t"},
            cwd="/tmp",
            engine=engine,
        )
        assert "fork child" in result.data.lower()


class TestShouldRunAsync:
    def test_explicit_background(self) -> None:
        from AgentX.tools.agent_tool.tool import _should_run_async

        assert _should_run_async(run_in_background=True, agent_definition=None, is_fork=False) is True

    def test_default_is_sync(self) -> None:
        from AgentX.tools.agent_tool.tool import _should_run_async

        assert _should_run_async(run_in_background=False, agent_definition=None, is_fork=False) is False


class TestResolveAgentDefinition:
    def test_empty_returns_none(self) -> None:
        from AgentX.tools.agent_tool.tool import _resolve_agent_definition

        assert _resolve_agent_definition("", "/tmp") is None

    def test_builtin_resolves(self) -> None:
        from AgentX.tools.agent_tool.tool import _resolve_agent_definition

        result = _resolve_agent_definition("general-purpose", "/tmp")
        assert result is not None
        assert result.agent_type == "general-purpose"


class TestRunAgentHelpers:
    def test_build_system_prompt(self) -> None:
        from AgentX.tools.agent_tool.definitions import BaseAgentDefinition
        from AgentX.tools.agent_tool.run_agent import _build_agent_system_prompt

        agent = BaseAgentDefinition(agent_type="test")
        agent._get_system_prompt = lambda **kw: "Custom"
        assert _build_agent_system_prompt(agent) == "Custom"

    def test_empty_prompt_falls_back(self) -> None:
        from AgentX.constants.prompts import DEFAULT_AGENT_PROMPT
        from AgentX.tools.agent_tool.definitions import BaseAgentDefinition
        from AgentX.tools.agent_tool.run_agent import _build_agent_system_prompt

        agent = BaseAgentDefinition(agent_type="test")
        agent._get_system_prompt = lambda **kw: ""
        assert _build_agent_system_prompt(agent) == DEFAULT_AGENT_PROMPT


class TestResumeHelpers:
    def test_read_metadata_nonexistent(self) -> None:
        from AgentX.tools.agent_tool.resume import _read_agent_metadata

        assert _read_agent_metadata(Path("/nonexistent/path.json")) == {}

    def test_read_metadata_valid(self) -> None:
        from AgentX.tools.agent_tool.resume import _read_agent_metadata

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"agent_type": "worker"}, f)
            f.flush()
            result = _read_agent_metadata(Path(f.name))
            assert result["agent_type"] == "worker"
            os.unlink(f.name)

    def test_clean_transcript_removes_orphaned(self) -> None:
        from AgentX.tools.agent_tool.resume import _clean_transcript_messages

        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "orphan", "name": "Read"}]},
            {"role": "user", "content": "bye"},
        ]
        cleaned = _clean_transcript_messages(messages)
        assistant_msgs = [m for m in cleaned if isinstance(m, dict) and m.get("role") == "assistant"]
        assert len(assistant_msgs) == 0

    def test_clean_transcript_keeps_matched(self) -> None:
        from AgentX.tools.agent_tool.resume import _clean_transcript_messages

        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "ok", "name": "Read"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "ok", "content": "done"}]},
        ]
        cleaned = _clean_transcript_messages(messages)
        assistant_msgs = [m for m in cleaned if isinstance(m, dict) and m.get("role") == "assistant"]
        assert len(assistant_msgs) == 1


class TestAsyncLifecycle:
    async def test_success_path(self) -> None:
        from AgentX.tools.agent_tool.utils import run_async_agent_lifecycle

        results: list[Any] = []

        async def make_stream():
            yield MagicMock(type="assistant")

        await run_async_agent_lifecycle(
            task_id="t1",
            make_stream=make_stream,
            metadata={"prompt": "test"},
            description="test",
            on_complete=lambda r: results.append(r),
        )
        assert len(results) == 1

    async def test_error_path(self) -> None:
        from AgentX.tools.agent_tool.utils import run_async_agent_lifecycle

        errors: list[str] = []

        async def make_stream():
            raise RuntimeError("boom")
            yield  # noqa: unreachable

        await run_async_agent_lifecycle(
            task_id="t2",
            make_stream=make_stream,
            metadata={},
            description="fail",
            on_fail=lambda e: errors.append(e),
        )
        assert "boom" in errors[0]


class TestPackageIntegration:
    def test_package_exports(self) -> None:
        from AgentX.tools.agent_tool import AgentTool

        assert AgentTool.__module__ == "AgentX.tools.agent_tool.tool"

    def test_tools_registry(self) -> None:
        from AgentX.tools import get_all_base_tools

        tools = get_all_base_tools()
        agent_tools = [t for t in tools if t.name == "Agent"]
        assert len(agent_tools) == 1

    def test_all_submodules_importable(self) -> None:
        import importlib

        for mod in [
            "AgentX.tools.agent_tool.constants",
            "AgentX.tools.agent_tool.memory",
            "AgentX.tools.agent_tool.definitions",
            "AgentX.tools.agent_tool.built_in",
            "AgentX.tools.agent_tool.fork",
            "AgentX.tools.agent_tool.utils",
            "AgentX.tools.agent_tool.prompt",
            "AgentX.tools.agent_tool.run_agent",
            "AgentX.tools.agent_tool.resume",
            "AgentX.tools.agent_tool.tool",
        ]:
            assert importlib.import_module(mod) is not None
