"""Task tools — strict translation of TaskOutputTool, TaskStopTool, TaskCreateTool, TaskGetTool, TaskUpdateTool, TaskListTool."""

from __future__ import annotations

from typing import Any

from claude_code.data_types import (
    TaskStatus,
    TaskType,
    ToolParameterType,
    ToolResult,
    coerce_str_enum,
    maybe_coerce_str_enum,
)
from claude_code.tools.base import BaseTool, ToolParameter
from claude_code.tools.tool_names import (
    TASK_CREATE_TOOL_NAME,
    TASK_GET_TOOL_NAME,
    TASK_LIST_TOOL_NAME,
    TASK_OUTPUT_TOOL_NAME,
    TASK_STOP_TOOL_NAME,
    TASK_UPDATE_TOOL_NAME,
)


def _get_task_manager(kwargs: dict[str, Any]) -> Any | None:
    """Resolve task_manager from kwargs or engine.task_manager."""
    tm = kwargs.get("task_manager")
    if tm is not None:
        return tm
    engine = kwargs.get("engine")
    if engine is not None and hasattr(engine, "task_manager"):
        return engine.task_manager
    return None


class TaskOutputTool(BaseTool):
    """Get the output of a background task."""

    name = TASK_OUTPUT_TOOL_NAME

    def get_description(self) -> str:
        return "Get the output of a background task. Optionally wait for completion."

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="task_id",
                type=ToolParameterType.STRING,
                description="The task ID to get output from",
            ),
            ToolParameter(
                name="block",
                type=ToolParameterType.BOOLEAN,
                description="Whether to wait for completion",
                required=False,
                default=True,
            ),
            ToolParameter(
                name="timeout",
                type=ToolParameterType.INTEGER,
                description="Max wait time in ms (default: 30000)",
                required=False,
                default=30000,
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        task_id = tool_input.get("task_id", "")
        task_manager = _get_task_manager(kwargs)

        if task_manager is None:
            return ToolResult(data="Error: Task manager not available")

        task_info = task_manager.get_task(task_id)
        if task_info is None:
            return ToolResult(data=f"Error: Task '{task_id}' not found")

        if task_info.status == TaskStatus.COMPLETED:
            return ToolResult(data=str(task_info.result or "(no output)"))

        if task_info.status in (TaskStatus.FAILED, TaskStatus.KILLED):
            return ToolResult(data=f"Task {task_id} {task_info.status}: {task_info.result or '(no details)'}")

        block = tool_input.get("block", True)
        if not block:
            return ToolResult(data=f"Task {task_id} is still {task_info.status}")

        # Wait for completion
        import asyncio

        timeout_ms = min(tool_input.get("timeout", 30000), 600000)
        timeout_s = timeout_ms / 1000.0
        try:
            result = await asyncio.wait_for(
                task_manager.wait_for_task(task_id),
                timeout=timeout_s,
            )
            return ToolResult(data=str(result or "(no output)"))
        except asyncio.TimeoutError:
            return ToolResult(data=f"Task {task_id} timed out after {timeout_ms}ms (still running)")


class TaskStopTool(BaseTool):
    """Stop a running background task."""

    name = TASK_STOP_TOOL_NAME
    aliases = ["KillShell"]
    is_concurrency_safe = True
    should_defer = True
    search_hint = "kill a running background task"

    def get_description(self) -> str:
        return "Stop a running background task or shell."

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="task_id",
                type=ToolParameterType.STRING,
                description="The ID of the background task to stop",
                required=False,
            ),
            ToolParameter(
                name="shell_id",
                type=ToolParameterType.STRING,
                description="Deprecated: use task_id instead",
                required=False,
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        task_id = tool_input.get("task_id") or tool_input.get("shell_id", "")
        if not task_id:
            return ToolResult(data="Error: task_id is required")

        task_manager = _get_task_manager(kwargs)
        if task_manager is None:
            return ToolResult(data="Error: Task manager not available")

        success = await task_manager.stop_task(task_id)
        if success:
            return ToolResult(data=f"Task {task_id} stopped")
        return ToolResult(data=f"Error: Could not stop task {task_id}")


class TaskCreateTool(BaseTool):
    """Create a new background task."""

    name = TASK_CREATE_TOOL_NAME

    def get_description(self) -> str:
        return "Create a new task for background execution."

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="description",
                type=ToolParameterType.STRING,
                description="Short description of the task",
            ),
            ToolParameter(
                name="prompt",
                type=ToolParameterType.STRING,
                description="The task prompt or command to execute",
            ),
            ToolParameter(
                name="task_type",
                type=ToolParameterType.STRING,
                description="Type of task to create",
                required=False,
                enum=[TaskType.LOCAL_BASH.value, TaskType.LOCAL_AGENT.value],
                default=TaskType.LOCAL_AGENT.value,
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        task_manager = _get_task_manager(kwargs)
        if task_manager is None:
            return ToolResult(data="Error: Task manager not available")

        description = tool_input.get("description", "")
        prompt = tool_input.get("prompt", "")
        task_type = coerce_str_enum(
            TaskType,
            tool_input.get("task_type"),
            default=TaskType.LOCAL_AGENT,
        )

        task_id = await task_manager.create_task(
            description=description,
            prompt=prompt,
            task_type=task_type,
            cwd=cwd,
        )
        return ToolResult(data=f"Created task {task_id}: {description}")


class TaskGetTool(BaseTool):
    """Get information about a specific task."""

    name = TASK_GET_TOOL_NAME
    is_read_only = True
    is_concurrency_safe = True

    def get_description(self) -> str:
        return "Get status and details of a specific task."

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="task_id",
                type=ToolParameterType.STRING,
                description="The task ID to look up",
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        task_manager = _get_task_manager(kwargs)
        if task_manager is None:
            return ToolResult(data="Error: Task manager not available")

        task_id = tool_input.get("task_id", "")
        task_info = task_manager.get_task(task_id)
        if task_info is None:
            return ToolResult(data=f"Error: Task '{task_id}' not found")

        lines = [
            f"Task ID: {task_info.task_id}",
            f"Type: {task_info.task_type}",
            f"Status: {task_info.status}",
            f"Description: {task_info.description}",
        ]
        if task_info.result is not None:
            lines.append(f"Result: {task_info.result}")
        return ToolResult(data="\n".join(lines))


class TaskUpdateTool(BaseTool):
    """Update a running task."""

    name = TASK_UPDATE_TOOL_NAME

    def get_description(self) -> str:
        return "Send an update or additional input to a running task."

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="task_id",
                type=ToolParameterType.STRING,
                description="The task ID to update",
            ),
            ToolParameter(
                name="message",
                type=ToolParameterType.STRING,
                description="Message or input to send to the task",
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        task_manager = _get_task_manager(kwargs)
        if task_manager is None:
            return ToolResult(data="Error: Task manager not available")

        task_id = tool_input.get("task_id", "")
        message = tool_input.get("message", "")

        success = await task_manager.update_task(task_id, message)
        if success:
            return ToolResult(data=f"Updated task {task_id}")
        return ToolResult(data=f"Error: Could not update task {task_id}")


class TaskListTool(BaseTool):
    """List all tasks."""

    name = TASK_LIST_TOOL_NAME
    is_read_only = True
    is_concurrency_safe = True

    def get_description(self) -> str:
        return "List all tasks and their current status."

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="status_filter",
                type=ToolParameterType.STRING,
                description="Optional filter by status",
                required=False,
                enum=[status.value for status in TaskStatus],
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        task_manager = _get_task_manager(kwargs)
        if task_manager is None:
            return ToolResult(data="Error: Task manager not available")

        status_filter = maybe_coerce_str_enum(TaskStatus, tool_input.get("status_filter"))
        tasks = task_manager.list_tasks(status_filter=status_filter)

        if not tasks:
            return ToolResult(data="No tasks found")

        lines = [f"Tasks ({len(tasks)}):"]
        for t in tasks:
            lines.append(f"  [{t.status}] {t.task_id}: {t.description}")
        return ToolResult(data="\n".join(lines))
