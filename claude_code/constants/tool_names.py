"""Tool name constants — shared, dependency-free.

Copy of the constants from claude_code/tools/tool_names.py so that
constants/prompts.py can import tool names without triggering a circular
import through tools/__init__.py.
"""

AGENT_TOOL_NAME = "Agent"
ASK_USER_QUESTION_TOOL_NAME = "AskUserQuestion"
BASH_TOOL_NAME = "Bash"
FILE_EDIT_TOOL_NAME = "Edit"
FILE_READ_TOOL_NAME = "Read"
FILE_WRITE_TOOL_NAME = "Write"
GLOB_TOOL_NAME = "Glob"
GREP_TOOL_NAME = "Grep"
SKILL_TOOL_NAME = "Skill"
TASK_CREATE_TOOL_NAME = "TaskCreate"
TODO_WRITE_TOOL_NAME = "TodoWrite"
