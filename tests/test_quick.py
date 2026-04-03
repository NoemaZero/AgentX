"""Quick test script for claude-code-py core functionality."""

import asyncio


async def test():
    from claude_code.tools.bash_tool import BashTool
    from claude_code.tools.file_read_tool import FileReadTool
    from claude_code.tools.file_write_tool import FileWriteTool
    from claude_code.tools.file_edit_tool import FileEditTool
    from claude_code.tools.glob_tool import GlobTool
    from claude_code.tools.grep_tool import GrepTool

    cwd = "."

    # Test Bash
    bash = BashTool()
    r = await bash.execute(tool_input={"command": "echo hello world"}, cwd=cwd)
    print(f"Bash: {r.data}")
    assert "hello world" in r.data

    # Test Write
    write = FileWriteTool()
    r = await write.execute(
        tool_input={"file_path": "/tmp/test_claude_code.txt", "content": "line1\nline2\nline3\n"},
        cwd=cwd,
    )
    print(f"Write: {r.data}")
    assert "Created" in r.data or "Wrote" in r.data

    # Test Read
    read = FileReadTool()
    r = await read.execute(tool_input={"file_path": "/tmp/test_claude_code.txt"}, cwd=cwd)
    print(f"Read: {r.data}")
    assert "line1" in r.data

    # Test Edit
    edit = FileEditTool()
    r = await edit.execute(
        tool_input={
            "file_path": "/tmp/test_claude_code.txt",
            "old_string": "line2",
            "new_string": "MODIFIED",
        },
        cwd=cwd,
    )
    print(f"Edit: {r.data}")
    assert "replaced" in r.data

    # Verify edit
    r = await read.execute(tool_input={"file_path": "/tmp/test_claude_code.txt"}, cwd=cwd)
    print(f"After edit: {r.data}")
    assert "MODIFIED" in r.data

    # Test Glob
    glob_ = GlobTool()
    r = await glob_.execute(tool_input={"pattern": "*.toml"}, cwd=cwd)
    print(f"Glob: {r.data}")
    assert "pyproject.toml" in r.data

    # Test Grep
    grep = GrepTool()
    r = await grep.execute(
        tool_input={"pattern": "openai", "path": ".", "output_mode": "files_with_matches"},
        cwd=cwd,
    )
    print(f"Grep: {r.data[:200]}")

    # Test OpenAI tool schema generation
    from claude_code.tools import get_all_base_tools

    tools = get_all_base_tools()
    for t in tools:
        schema = t.to_openai_tool()
        assert schema["type"] == "function"
        assert "name" in schema["function"]
        assert "description" in schema["function"]
        assert "parameters" in schema["function"]

    print(f"\nAll {len(tools)} tool schemas valid!")

    # Test system prompt
    from claude_code.constants.prompts import get_system_prompt

    prompt = get_system_prompt(cwd="/tmp")
    assert "authorized security testing" in prompt
    assert "Using your tools" in prompt
    assert "Doing tasks" in prompt
    print(f"System prompt: {len(prompt)} chars, all sections present")

    # Test context building
    from claude_code.engine.context import get_git_status

    git_status = await get_git_status(cwd)
    if git_status:
        print(f"Git status: {git_status[:100]}...")
    else:
        print("Git status: (not a git repo)")

    # Test QueryEngine initialization
    from claude_code.config import load_config
    from claude_code.engine.query_engine import QueryEngine

    config = load_config(api_key="test-key", cwd=cwd)
    engine = QueryEngine(config)
    await engine.initialize()
    print("QueryEngine initialized successfully")

    print("\n=== ALL TESTS PASSED ===")


if __name__ == "__main__":
    asyncio.run(test())
