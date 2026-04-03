#!/usr/bin/env python3
"""Comprehensive test for all new modules."""

import asyncio
import sys


def test_all():
    """Test all imports and basic functionality."""
    errors = []

    # 1. Tools
    try:
        from claude_code.tools import get_all_base_tools, get_tools_by_name
        tools = get_all_base_tools()
        assert len(tools) == 28, f"Expected 28 tools, got {len(tools)}"
        names = [t.name for t in tools]
        print(f"✅ Tools registered: {len(tools)}")
        for t in tools:
            print(f"   {t.name} (ro={t.is_read_only}, cs={t.is_concurrency_safe}, defer={t.should_defer})")

        by_name = get_tools_by_name(tools)
        assert "Agent" in by_name
        assert "Task" in by_name  # alias
        assert "KillShell" in by_name  # alias
        print(f"   Aliases work: Task -> {by_name['Task'].name}, KillShell -> {by_name['KillShell'].name}")
    except Exception as e:
        errors.append(f"Tools: {e}")
        import traceback; traceback.print_exc()

    # 2. Permission system
    try:
        from claude_code.permissions.checker import PermissionChecker
        pc = PermissionChecker(mode="default")
        r = pc.check("Read", {}, is_read_only=True)
        assert r.behavior == "allow", f"Read should be allowed, got {r.behavior}"

        r = pc.check("Bash", {"command": "ls"}, is_read_only=False)
        assert r.behavior == "ask", f"Bash in default mode should ask, got {r.behavior}"

        pc2 = PermissionChecker(mode="bypassPermissions")
        r = pc2.check("Bash", {"command": "rm -rf /"}, is_read_only=False)
        assert r.behavior == "allow", f"Bypass mode should allow, got {r.behavior}"

        pc3 = PermissionChecker(mode="plan")
        r = pc3.check("Bash", {}, is_read_only=False)
        assert r.behavior == "deny", f"Plan mode should deny non-readonly, got {r.behavior}"
        print("✅ PermissionChecker: all modes correct")
    except Exception as e:
        errors.append(f"PermissionChecker: {e}")
        import traceback; traceback.print_exc()

    # 3. Classifier
    try:
        from claude_code.permissions.classifier import classify_bash_command, is_read_only_bash
        assert classify_bash_command("ls") == "read_only"
        assert classify_bash_command("rm -rf /") == "destructive"
        assert classify_bash_command("git status") == "read_only"
        assert classify_bash_command("npm install") == "write"
        assert is_read_only_bash("cat file.txt")
        assert not is_read_only_bash("rm file.txt")
        print("✅ Bash classifier: all classifications correct")
    except Exception as e:
        errors.append(f"Classifier: {e}")
        import traceback; traceback.print_exc()

    # 4. PathValidator
    try:
        from claude_code.permissions.path_validator import PathValidator
        pv = PathValidator("/tmp")
        assert pv.is_allowed("/tmp/foo")
        assert not pv.is_allowed("/etc/passwd")
        ok, msg = pv.validate("/tmp/test.txt")
        assert ok
        ok, msg = pv.validate("relative/path")
        assert not ok
        print("✅ PathValidator: validation correct")
    except Exception as e:
        errors.append(f"PathValidator: {e}")
        import traceback; traceback.print_exc()

    # 5. Compact service
    try:
        from claude_code.services.compact import AutoCompactTracker
        from claude_code.services.compact.compact import compact_messages, should_auto_compact
        from claude_code.data_types import UserMessage, AssistantMessage
        act = AutoCompactTracker()
        # Should not compact few messages
        msgs = [UserMessage(content="hi"), AssistantMessage(content="hello")]
        assert not act.check_should_compact(msgs)
        print("✅ Compact service: basic checks pass")
    except Exception as e:
        errors.append(f"Compact: {e}")
        import traceback; traceback.print_exc()

    # 6. Retry
    try:
        from claude_code.services.api.retry import RetryConfig, MAX_OUTPUT_TOKENS_RECOVERY_LIMIT
        assert MAX_OUTPUT_TOKENS_RECOVERY_LIMIT == 3
        rc = RetryConfig(max_retries=5)
        assert rc.max_retries == 5
        print("✅ Retry config: correct")
    except Exception as e:
        errors.append(f"Retry: {e}")
        import traceback; traceback.print_exc()

    # 7. Usage tracker
    try:
        from claude_code.services.api.usage import UsageTracker, AggregateUsage
        from claude_code.data_types import Usage
        ut = UsageTracker()
        ut.record(Usage(input_tokens=100, output_tokens=50))
        ut.record(Usage(input_tokens=200, output_tokens=100))
        assert ut.usage.total_input_tokens == 300
        assert ut.usage.total_output_tokens == 150
        assert ut.usage.request_count == 2
        print("✅ UsageTracker: aggregation correct")
    except Exception as e:
        errors.append(f"UsageTracker: {e}")
        import traceback; traceback.print_exc()

    # 8. Task manager
    try:
        from claude_code.tasks.manager import TaskManager
        tm = TaskManager()
        assert tm.list_tasks() == []
        print("✅ TaskManager: created, empty list")
    except Exception as e:
        errors.append(f"TaskManager: {e}")
        import traceback; traceback.print_exc()

    # 9. Memory
    try:
        from claude_code.memory.memdir import load_memory, format_memory_for_prompt
        print("✅ Memory module: imported")
    except Exception as e:
        errors.append(f"Memory: {e}")
        import traceback; traceback.print_exc()

    # 10. Hooks
    try:
        from claude_code.utils.hooks import HookManager
        hm = HookManager()
        assert len(hm._hooks["pre_tool_use"]) == 0
        print("✅ HookManager: created")
    except Exception as e:
        errors.append(f"Hooks: {e}")
        import traceback; traceback.print_exc()

    # 11. AppState
    try:
        from claude_code.state.app_state import AppState, AppStateStore
        s = AppState()
        s2 = s.set_plan_mode(True)
        assert s2.plan_mode is True
        assert s.plan_mode is False  # immutable
        store = AppStateStore()
        events = []
        store.subscribe(lambda st: events.append(st))
        store.set_todos([{"id": "1", "title": "test", "status": "pending"}])
        assert len(events) == 1
        print("✅ AppState: immutable, store with subscriber works")
    except Exception as e:
        errors.append(f"AppState: {e}")
        import traceback; traceback.print_exc()

    # 12. Commands
    try:
        from claude_code.commands.registry import CommandRegistry
        cr = CommandRegistry()
        cmds = [c.name for c in cr.commands if not c.is_hidden]
        assert len(cmds) >= 16, f"Expected 16+ commands, got {len(cmds)}"
        assert cr.get("exit") is not None
        assert cr.get("quit") is not None  # alias
        assert cr.get("q") is not None  # alias
        assert cr.get("ctx") is not None  # alias
        print(f"✅ Commands: {len(cmds)} registered")
        print(f"   {cmds}")
    except Exception as e:
        errors.append(f"Commands: {e}")
        import traceback; traceback.print_exc()

    # 13. Tool schemas
    try:
        from claude_code.tools import get_all_base_tools
        for tool in get_all_base_tools():
            schema = tool.to_openai_tool()
            assert schema["type"] == "function"
            assert "function" in schema
            assert "name" in schema["function"]
            assert schema["function"]["name"] == tool.name
        print("✅ All tool OpenAI schemas: valid")
    except Exception as e:
        errors.append(f"Schemas: {e}")
        import traceback; traceback.print_exc()

    # 14. QueryEngine init (without API key)
    try:
        from claude_code.config import Config
        from claude_code.engine.query_engine import QueryEngine
        c = Config(model="test", api_key="test-key", base_url="http://localhost:1234/v1")
        qe = QueryEngine(c)
        assert qe.permission_checker is not None
        assert qe.task_manager is not None
        assert qe.usage_tracker is not None
        print("✅ QueryEngine: initialized with all subsystems")
    except Exception as e:
        errors.append(f"QueryEngine: {e}")
        import traceback; traceback.print_exc()

    # Summary
    print("\n" + "=" * 50)
    if errors:
        print(f"❌ FAILED: {len(errors)} error(s)")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    else:
        print("✅ ALL TESTS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    test_all()
