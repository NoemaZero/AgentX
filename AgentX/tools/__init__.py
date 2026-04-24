"""Tool registry — strict translation of tools.ts getAllBaseTools() order."""

from __future__ import annotations

from AgentX.tools.agent_tool import AgentTool
from AgentX.tools.ask_user_question_tool import AskUserQuestionTool
from AgentX.tools.base import BaseTool
from AgentX.tools.bash_tool import BashTool
from AgentX.tools.brief_tool import BriefTool
from AgentX.tools.config_tool import ConfigTool
from AgentX.tools.file_edit_tool import FileEditTool
from AgentX.tools.file_read_tool import FileReadTool
from AgentX.tools.file_write_tool import FileWriteTool
from AgentX.tools.glob_tool import GlobTool
from AgentX.tools.grep_tool import GrepTool
from AgentX.tools.mcp_tools import ListMcpResourcesTool, ReadMcpResourceTool
from AgentX.tools.notebook_edit_tool import NotebookEditTool
from AgentX.tools.plan_mode_tool import EnterPlanModeTool, ExitPlanModeTool
from AgentX.tools.send_message_tool import SendMessageTool
from AgentX.tools.skill_tool import SkillTool
from AgentX.tools.sleep_tool import SleepTool
from AgentX.tools.task_tools import (
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskOutputTool,
    TaskStopTool,
    TaskUpdateTool,
)
from AgentX.tools.todo_write_tool import TodoWriteTool
from AgentX.tools.tool_search_tool import ToolSearchTool
from AgentX.tools.web_fetch_tool import WebFetchTool
from AgentX.tools.web_search_tool import WebSearchTool


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
