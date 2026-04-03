"""Integration tests for Memory + Agent systems."""
import asyncio
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

passed = 0
failed = 0


def test(name, condition):
    global passed, failed
    if condition:
        print(f"  PASS: {name}")
        passed += 1
    else:
        print(f"  FAIL: {name}")
        failed += 1


# ===================================================================
# Test 1: CLAUDE.md multi-layer loading
# ===================================================================
print("\n=== Test 1: CLAUDE.md multi-layer loading ===")

from claude_code.utils.claudemd import (
    get_memory_files, format_memory_files, get_claude_mds,
    MemoryFileInfo, MEMORY_INSTRUCTION_PROMPT, MAX_INCLUDE_DEPTH,
    reset_memory_file_cache, _extract_include_paths,
    MEMORY_TYPE_DESCRIPTIONS, TEXT_FILE_EXTENSIONS,
)

test("MEMORY_INSTRUCTION_PROMPT contains OVERRIDE", "OVERRIDE any default behavior" in MEMORY_INSTRUCTION_PROMPT)
test("MAX_INCLUDE_DEPTH is 5", MAX_INCLUDE_DEPTH == 5)
test("6 memory type descriptions", len(MEMORY_TYPE_DESCRIPTIONS) == 6)
test("TEXT_FILE_EXTENSIONS has .py", ".py" in TEXT_FILE_EXTENSIONS)

# Test include path extraction
paths = _extract_include_paths("@./readme.md @~/notes.txt @/etc/conf.md", "/tmp")
normalized = [os.path.normpath(p) for p in paths]
test("@include extracts ./readme.md -> /tmp/readme.md", "/tmp/readme.md" in normalized)
test("@include extracts ~/notes.txt", any("notes.txt" in p for p in normalized))
test("@include extracts /etc/conf.md", "/etc/conf.md" in normalized)

# Test code block stripping in @include
paths_code = _extract_include_paths("```\n@should_not_match.md\n```\n@should_match.md", "/tmp")
normalized_code = [os.path.normpath(p) for p in paths_code]
test("@include skips code blocks", "/tmp/should_not_match.md" not in normalized_code)
test("@include matches outside code blocks", "/tmp/should_match.md" in normalized_code)

# Test format_memory_files
files = [
    MemoryFileInfo(path="/test/CLAUDE.md", content="Hello project", type="Project"),
    MemoryFileInfo(path="/home/.claude/CLAUDE.md", content="Hello user", type="User"),
]
formatted = format_memory_files(files)
test("format includes MEMORY_INSTRUCTION_PROMPT", MEMORY_INSTRUCTION_PROMPT in formatted)
test("format includes project content", "Hello project" in formatted)
test("format includes user content", "Hello user" in formatted)

# Test format with filter
filtered = format_memory_files(files, filter_type="Project")
test("filter: includes Project", "Hello project" in filtered)
test("filter: excludes User", "Hello user" not in filtered)

# Test empty returns None
test("format empty returns None", format_memory_files([]) is None)

# Test cache reset
reset_memory_file_cache(reason="test")
test("Cache reset runs without error", True)


# ===================================================================
# Test 2: Directory walking
# ===================================================================
print("\n=== Test 2: Directory walking + @include ===")

tmpdir = tempfile.mkdtemp()
try:
    # Create project structure
    subdir = os.path.join(tmpdir, "proj", "sub")
    os.makedirs(subdir, exist_ok=True)
    os.makedirs(os.path.join(tmpdir, "proj", ".claude", "rules"), exist_ok=True)

    # Root-level CLAUDE.md
    with open(os.path.join(tmpdir, "CLAUDE.md"), "w") as f:
        f.write("Root level instructions")

    # Project-level CLAUDE.md with @include
    with open(os.path.join(tmpdir, "proj", "CLAUDE.md"), "w") as f:
        f.write("Project instructions\n@./extra.md")

    # Included file
    with open(os.path.join(tmpdir, "proj", "extra.md"), "w") as f:
        f.write("Extra included content")

    # .claude/CLAUDE.md
    with open(os.path.join(tmpdir, "proj", ".claude", "CLAUDE.md"), "w") as f:
        f.write("Dot-claude instructions")

    # Rules file
    with open(os.path.join(tmpdir, "proj", ".claude", "rules", "style.md"), "w") as f:
        f.write("Style guide")

    # Local
    with open(os.path.join(tmpdir, "proj", "CLAUDE.local.md"), "w") as f:
        f.write("Local instructions")

    async def test_dir_walk():
        reset_memory_file_cache(reason="test")
        files = await get_memory_files(cwd=os.path.join(tmpdir, "proj", "sub"), force_reload=True)
        paths = [f.path for f in files]
        types = [f.type for f in files]
        contents = [f.content for f in files]

        test("Found root CLAUDE.md", any("Root level" in c for c in contents))
        test("Found project CLAUDE.md", any("Project instructions" in c for c in contents))
        test("Found @include extra.md", any("Extra included" in c for c in contents))
        test("Found .claude/CLAUDE.md", any("Dot-claude" in c for c in contents))
        test("Found rules/style.md", any("Style guide" in c for c in contents))
        test("Found CLAUDE.local.md", any("Local instructions" in c for c in contents))

        # Check types
        test("Project type assigned", "Project" in types)
        test("Local type assigned", "Local" in types)

        # Check ordering (root before project)
        root_idx = next(i for i, c in enumerate(contents) if "Root level" in c)
        proj_idx = next(i for i, c in enumerate(contents) if "Project instructions" in c)
        test("Root before Project (priority order)", root_idx < proj_idx)

        # Test full format
        formatted = await get_claude_mds(os.path.join(tmpdir, "proj", "sub"))
        test("get_claude_mds returns string", isinstance(formatted, str))
        test("get_claude_mds has MEMORY_INSTRUCTION_PROMPT", MEMORY_INSTRUCTION_PROMPT in formatted)

    asyncio.run(test_dir_walk())
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)


# ===================================================================
# Test 3: Session memory
# ===================================================================
print("\n=== Test 3: Session memory ===")

from claude_code.memory.session_memory import (
    DEFAULT_TEMPLATE, SessionMemoryState,
    init_session_memory, read_session_notes, write_session_notes,
    build_update_prompt, get_session_memory_for_prompt,
    ensure_template_dir, get_template,
)

test("DEFAULT_TEMPLATE has Session Title", "# Session Title" in DEFAULT_TEMPLATE)
test("DEFAULT_TEMPLATE has Worklog", "# Worklog" in DEFAULT_TEMPLATE)

tmpdir2 = tempfile.mkdtemp()
try:
    # Override dir for testing
    import claude_code.memory.session_memory as sm
    orig_dir = sm.SESSION_MEMORY_DIR
    sm.SESSION_MEMORY_DIR = os.path.join(tmpdir2, "session-memory")

    state = init_session_memory("/tmp/test-project")
    test("Session state created", state is not None)
    test("Session has notes_path", state.notes_path.endswith(".md"))

    notes = read_session_notes(state)
    test("Initial notes match template", "# Session Title" in notes)

    new_state = write_session_notes(state, "# Updated\nSome content")
    test("Write returns new state (immutable)", new_state is not state)
    test("Update count incremented", new_state.update_count == 1)

    updated_notes = read_session_notes(new_state)
    test("Notes updated on disk", "Updated" in updated_notes)

    prompt = build_update_prompt(new_state)
    test("Update prompt contains current notes", "Updated" in prompt)

    sm.SESSION_MEMORY_DIR = orig_dir
finally:
    shutil.rmtree(tmpdir2, ignore_errors=True)


# ===================================================================
# Test 4: Agent runner
# ===================================================================
print("\n=== Test 4: Agent runner ===")

from claude_code.agents.runner import (
    FORK_BOILERPLATE, FORK_PLACEHOLDER,
    AgentRegistry, AgentTask,
    build_forked_messages, build_task_notification,
    filter_tools_for_agent, get_agent_registry,
    get_agent_system_prompt, load_agent_memory,
)

test("FORK_BOILERPLATE has rules", "RULES (non-negotiable)" in FORK_BOILERPLATE)
test("FORK_PLACEHOLDER text", FORK_PLACEHOLDER == "Fork started — processing in background")

# Test registry
registry = AgentRegistry()
task = AgentTask(agent_id="test-1", description="test task", prompt="do stuff")
registry.register(task)
test("Registry register", registry.get("test-1") is task)
test("Registry find_by_name", registry.find_by_name("test task") is task)
test("Registry active_agents", len(registry.active_agents) == 1)

registry.enqueue_notification("test notification")
notifications = registry.drain_notifications()
test("Notification enqueue/drain", notifications == ["test notification"])
test("Notifications cleared after drain", len(registry.drain_notifications()) == 0)

registry.unregister("test-1")
test("Registry unregister", registry.get("test-1") is None)

# Test notification format
task2 = AgentTask(agent_id="t2", description="research", prompt="find it", status="completed")
notif = build_task_notification(task2, result="Found the answer")
test("Notification has task-notification tag", "<task-notification>" in notif)
test("Notification has status", "<status>completed</status>" in notif)
test("Notification has result", "Found the answer" in notif)

# Test fork messages
fork_msgs = build_forked_messages("Find all tests")
test("Fork produces 1 message", len(fork_msgs) == 1)
test("Fork message has boilerplate", "STOP. READ THIS FIRST" in str(fork_msgs[0].content))
test("Fork message has directive", "Find all tests" in str(fork_msgs[0].content))


# ===================================================================
# Test 5: Tool filtering
# ===================================================================
print("\n=== Test 5: Tool filtering ===")

from claude_code.tools.base import BaseTool, ToolParameter
from claude_code.data_types import ToolResult

# Create mock tools
class MockTool(BaseTool):
    def __init__(self, name):
        self.name = name
    def get_description(self): return ""
    def get_parameters(self): return []
    async def execute(self, **kwargs): return ToolResult(data="")

all_tools = [
    MockTool("Read"), MockTool("Write"), MockTool("Edit"),
    MockTool("Bash"), MockTool("Grep"), MockTool("Glob"),
    MockTool("Agent"), MockTool("Task"), MockTool("TodoWrite"),
    MockTool("TaskOutput"), MockTool("EnterPlanMode"), MockTool("ExitPlanMode"),
    MockTool("AskUserQuestion"), MockTool("TaskStop"),
    MockTool("WebFetch"), MockTool("WebSearch"),
]

# Regular agent filtering
regular = filter_tools_for_agent(all_tools)
regular_names = {t.name for t in regular}
test("Regular: excludes Agent", "Agent" not in regular_names)
test("Regular: excludes TaskOutput", "TaskOutput" not in regular_names)
test("Regular: excludes AskUserQuestion", "AskUserQuestion" not in regular_names)
test("Regular: includes Read", "Read" in regular_names)
test("Regular: includes Bash", "Bash" in regular_names)

# Async agent filtering
async_tools = filter_tools_for_agent(all_tools, is_async=True)
async_names = {t.name for t in async_tools}
test("Async: includes Read", "Read" in async_names)
test("Async: includes Bash", "Bash" in async_names)
test("Async: excludes Agent", "Agent" not in async_names)

# Fork agent filtering (keeps everything)
fork_tools = filter_tools_for_agent(all_tools, is_fork=True)
test("Fork: keeps all tools", len(fork_tools) == len(all_tools))


# ===================================================================
# Test 6: Agent system prompt
# ===================================================================
print("\n=== Test 6: Agent system prompt ===")

from claude_code.constants.prompts import DEFAULT_AGENT_PROMPT

# Regular agent
regular_prompt = get_agent_system_prompt()
test("Regular prompt is DEFAULT_AGENT_PROMPT", regular_prompt == DEFAULT_AGENT_PROMPT)

# Fork agent inherits parent
fork_prompt = get_agent_system_prompt(
    is_fork=True,
    parent_system_prompt="Parent system prompt here",
)
test("Fork inherits parent prompt", fork_prompt == "Parent system prompt here")

# Custom agent definition
class MockDef:
    name = "researcher"
    prompt = "You are a researcher."
    allowed_tools = ["Read", "Grep"]

custom_prompt = get_agent_system_prompt(agent_definition=MockDef())
test("Custom agent uses definition prompt", "researcher" in custom_prompt)


# ===================================================================
# Test 7: Send message tool
# ===================================================================
print("\n=== Test 7: SendMessage tool ===")

from claude_code.tools.send_message_tool import SendMessageTool

sm_tool = SendMessageTool()
test("SendMessage has target_agent param", any(
    p.name == "target_agent" for p in sm_tool.get_parameters()
))


# ===================================================================
# Test 8: Memory __init__ exports
# ===================================================================
print("\n=== Test 8: Memory package exports ===")

from claude_code.memory import (
    SessionMemoryState, init_session_memory,
    read_session_notes, write_session_notes,
    build_update_prompt, get_session_memory_for_prompt,
)
test("Memory package exports work", True)


# ===================================================================
# Test 9: Agent package exports
# ===================================================================
print("\n=== Test 9: Agent package exports ===")

from claude_code.agents import (
    FORK_BOILERPLATE as FB,
    AgentRegistry as AR,
    AgentTask as AT,
    filter_tools_for_agent as FTA,
    run_agent, run_agent_foreground, run_agent_background,
)
test("Agent package exports work", True)


# ===================================================================
# Summary
# ===================================================================
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed, {passed+failed} total")
if failed == 0:
    print("ALL TESTS PASSED")
else:
    print(f"FAILURES: {failed}")
    sys.exit(1)
