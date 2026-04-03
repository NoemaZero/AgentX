"""Task management system — strict translation of Task.ts + task manager."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Callable

from claude_code.data_types import (
    TaskInfo,
    TaskStatus,
    TaskType,
    coerce_str_enum,
    maybe_coerce_str_enum,
)

logger = logging.getLogger(__name__)


class TaskManager:
    """Manage background tasks (agents, bash processes, etc.).

    Translation of task management from Task.ts and tasks.ts.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, _ManagedTask] = {}

    def get_task(self, task_id: str) -> TaskInfo | None:
        """Get task info by ID."""
        managed = self._tasks.get(task_id)
        if managed is None:
            return None
        return managed.info

    def list_tasks(self, status_filter: TaskStatus | str | None = None) -> list[TaskInfo]:
        """List all tasks, optionally filtered by status."""
        tasks = [m.info for m in self._tasks.values()]
        if status_filter is not None:
            resolved_status = maybe_coerce_str_enum(TaskStatus, status_filter)
            if resolved_status is not None:
                tasks = [t for t in tasks if t.status == resolved_status]
        return tasks

    async def create_task(
        self,
        description: str,
        prompt: str,
        task_type: TaskType | str = TaskType.LOCAL_AGENT,
        cwd: str = "",
        run_fn: Callable[..., Any] | None = None,
    ) -> str:
        """Create and start a new task.

        Args:
            description: Short task description.
            prompt: The task prompt/command.
            task_type: Type of task (local_bash, local_agent, etc.).
            cwd: Working directory.
            run_fn: Async function to run the task. If None, uses internal dispatch.

        Returns:
            The task ID.
        """
        resolved_task_type = coerce_str_enum(
            TaskType,
            task_type,
            default=TaskType.LOCAL_AGENT,
        )
        task_id = str(uuid.uuid4())[:8]
        info = TaskInfo(
            task_id=task_id,
            task_type=resolved_task_type,
            status=TaskStatus.PENDING,
            description=description,
        )

        managed = _ManagedTask(info=info, prompt=prompt, cwd=cwd)
        self._tasks[task_id] = managed

        # Start the task in the background
        managed.task = asyncio.create_task(
            self._run_task(task_id, run_fn),
            name=f"task-{task_id}",
        )

        return task_id

    async def _run_task(
        self,
        task_id: str,
        run_fn: Callable[..., Any] | None = None,
    ) -> None:
        """Execute a task and update its status."""
        managed = self._tasks.get(task_id)
        if managed is None:
            return

        # Update to running
        managed.info = TaskInfo(
            task_id=managed.info.task_id,
            task_type=managed.info.task_type,
            status=TaskStatus.RUNNING,
            description=managed.info.description,
        )

        try:
            if run_fn is not None:
                result = await run_fn(managed.prompt, managed.cwd)
            elif managed.info.task_type == TaskType.LOCAL_BASH:
                result = await self._run_bash_task(managed.prompt, managed.cwd)
            else:
                # For agent tasks without a run_fn, just return
                result = "(Agent task execution requires QueryEngine integration)"

            managed.info = TaskInfo(
                task_id=managed.info.task_id,
                task_type=managed.info.task_type,
                status=TaskStatus.COMPLETED,
                description=managed.info.description,
                result=result,
            )
        except asyncio.CancelledError:
            managed.info = TaskInfo(
                task_id=managed.info.task_id,
                task_type=managed.info.task_type,
                status=TaskStatus.KILLED,
                description=managed.info.description,
            )
        except Exception as exc:
            managed.info = TaskInfo(
                task_id=managed.info.task_id,
                task_type=managed.info.task_type,
                status=TaskStatus.FAILED,
                description=managed.info.description,
                result=str(exc),
            )
        finally:
            if managed.completion_event:
                managed.completion_event.set()

    async def _run_bash_task(self, command: str, cwd: str) -> str:
        """Run a bash command as a task."""
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd or None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode(errors="replace")
        if stderr:
            output += "\n" + stderr.decode(errors="replace")
        return output.strip()

    async def stop_task(self, task_id: str) -> bool:
        """Stop a running task."""
        managed = self._tasks.get(task_id)
        if managed is None:
            return False

        if managed.task and not managed.task.done():
            managed.task.cancel()
            try:
                await managed.task
            except asyncio.CancelledError:
                pass
            return True

        return False

    async def update_task(self, task_id: str, message: str) -> bool:
        """Send an update to a running task."""
        managed = self._tasks.get(task_id)
        if managed is None:
            return False

        # For now, just log the update. In a full implementation,
        # this would send input to the task's stdin or agent loop.
        logger.info("Task %s update: %s", task_id, message[:200])
        return True

    async def wait_for_task(self, task_id: str) -> Any:
        """Wait for a task to complete and return its result."""
        managed = self._tasks.get(task_id)
        if managed is None:
            return None

        if managed.info.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.KILLED):
            return managed.info.result

        if managed.completion_event is None:
            managed.completion_event = asyncio.Event()

        await managed.completion_event.wait()
        return managed.info.result

    async def cleanup(self) -> None:
        """Cancel all running tasks."""
        for managed in self._tasks.values():
            if managed.task and not managed.task.done():
                managed.task.cancel()

        # Wait for all tasks to finish
        tasks = [m.task for m in self._tasks.values() if m.task and not m.task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class _ManagedTask:
    """Internal task wrapper."""

    __slots__ = ("info", "prompt", "cwd", "task", "completion_event")

    def __init__(self, info: TaskInfo, prompt: str, cwd: str) -> None:
        self.info = info
        self.prompt = prompt
        self.cwd = cwd
        self.task: asyncio.Task[None] | None = None
        self.completion_event: asyncio.Event | None = None
