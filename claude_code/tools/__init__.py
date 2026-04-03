"""Tool registry — strict translation of tools.ts getAllBaseTools() order."""

from __future__ import annotations

from claude_code.tools.agent_tool import AgentTool
from claude_code.tools.ask_user_question_tool import AskUserQuestionTool
from claude_code.tools.base import BaseTool
from claude_code.tools.bash_tool import BashTool
from claude_code.tools.brief_tool import BriefTool
from claude_code.tools.config_tool import ConfigTool
from claude_code.tools.file_edit_tool import FileEditTool
from claude_code.tools.file_read_tool import FileReadTool
from claude_code.tools.file_write_tool import FileWriteTool
from claude_code.tools.glob_tool import GlobTool
from claude_code.tools.grep_tool import GrepTool
from claude_code.tools.mcp_tools import ListMcpResourcesTool, ReadMcpResourceTool
from claude_code.tools.notebook_edit_tool import NotebookEditTool
from claude_code.tools.plan_mode_tool import EnterPlanModeTool, ExitPlanModeTool
from claude_code.tools.send_message_tool import SendMessageTool
from claude_code.tools.skill_tool import SkillTool
from claude_code.tools.sleep_tool import SleepTool
from claude_code.tools.task_tools import (
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskOutputTool,
    TaskStopTool,
    TaskUpdateTool,
)
from claude_code.tools.todo_write_tool import TodoWriteTool
from claude_code.tools.tool_search_tool import ToolSearchTool
from claude_code.tools.web_fetch_tool import WebFetchTool
from claude_code.tools.web_search_tool import WebSearchTool


def get_all_base_tools() -> list[BaseTool]:
    """Return all tools in the original registration order (from tools.ts getAllBaseTools).

    Order matches SOURCE_EXTRACTION.md §3:
     1. AgentTool
     2. TaskOutputTool
     3. BashTool
     4. GlobTool
     5. GrepTool
     6. ExitPlanModeTool
     7. FileReadTool
     8. FileEditTool
     9. FileWriteTool
    10. NotebookEditTool
    11. WebFetchTool
    12. TodoWriteTool
    13. WebSearchTool
    14. TaskStopTool
    15. AskUserQuestionTool
    16. SkillTool
    17. EnterPlanModeTool
    18. ConfigTool
    19. TaskCreateTool
    20. TaskGetTool
    21. TaskUpdateTool
    22. TaskListTool
    23. SendMessageTool
    24. BriefTool
    25. SleepTool
    26. ListMcpResourcesTool
    27. ReadMcpResourceTool
    28. ToolSearchTool
    """
    return [
        AgentTool(),
        TaskOutputTool(),
        BashTool(),
        GlobTool(),
        GrepTool(),
        ExitPlanModeTool(),
        FileReadTool(),
        FileEditTool(),
        FileWriteTool(),
        NotebookEditTool(),
        WebFetchTool(),
        TodoWriteTool(),
        WebSearchTool(),
        TaskStopTool(),
        AskUserQuestionTool(),
        SkillTool(),
        EnterPlanModeTool(),
        ConfigTool(),
        TaskCreateTool(),
        TaskGetTool(),
        TaskUpdateTool(),
        TaskListTool(),
        SendMessageTool(),
        BriefTool(),
        SleepTool(),
        ListMcpResourcesTool(),
        ReadMcpResourceTool(),
        ToolSearchTool(),
    ]


def get_tools_by_name(tools: list[BaseTool] | None = None) -> dict[str, BaseTool]:
    """Return a dict mapping tool name -> tool instance. Includes aliases."""
    if tools is None:
        tools = get_all_base_tools()

    result: dict[str, BaseTool] = {}
    for tool in tools:
        result[tool.name] = tool
        for alias in tool.aliases:
            result[alias] = tool
    return result
