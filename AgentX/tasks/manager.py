"""Task management — translation of Task.ts + LocalAgentTask.ts + diskOutput.ts.

Provides a unified ``TaskManager`` that backs both:
  - Background Agent tasks (registered by ``AgentTool._execute_async`` via
    ``run_async_agent_lifecycle``)
  - Background Bash tasks (registered by ``BashTool`` is_background)

Key translation points from JS source:
  - ``AppState.tasks``   → ``TaskManager._tasks``
  - ``registerAsyncAgent`` → ``TaskManager.register_agent``
  - ``completeAgentTask``  → ``TaskManager.complete_task``
  - ``failAgentTask``      → ``TaskManager.fail_task``
  - ``killAsyncAgent``     → ``TaskManager.kill_task``
  - ``getTaskOutputPath``  → ``TaskManager.get_output_path``
  - ``updateAgentProgress`` → ``TaskManager.update_progress``
  - ``enqueueAgentNotification`` → ``TaskManager.enqueue_notification``
  - ``stopTask``           → ``TaskManager.stop_task``
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Callable

from AgentX.data_types import (
    TaskInfo,
    TaskStatus,
    TaskType,
    coerce_str_enum,
    maybe_coerce_str_enum,
)

logger = logging.getLogger(__name__)

__all__ = ["TaskManager"]


# ---------------------------------------------------------------------------
# Output path helpers (translation of diskOutput.ts)
# ---------------------------------------------------------------------------


def _get_task_output_dir() -> str:
    """Return the task output directory.

    Translation of getTaskOutputDir: ``<tmpdir>/claude-tasks/<pid>/``
    """
    base = os.environ.get("CLAUDE_TASK_OUTPUT_DIR") or os.path.join(
        "/tmp", "claude-tasks", str(os.getpid()),
    )
    os.makedirs(base, exist_ok=True)
    return base


def _get_task_output_path(task_id: str) -> str:
    """Return the JSONL output file path for a task.

    Translation of getTaskOutputPath.
    """
    return os.path.join(_get_task_output_dir(), f"{task_id}.jsonl")


# ---------------------------------------------------------------------------
# AgentProgress — translation of AgentProgress from LocalAgentTask.ts
# ---------------------------------------------------------------------------


class AgentProgress:
    """Mutable progress tracker for a background agent."""

    __slots__ = (
        "tool_use_count",
        "token_count",
        "last_activity",
        "last_tool_name",
        "summary",
    )

    def __init__(self) -> None:
        self.tool_use_count: int = 0
        self.token_count: int = 0
        self.last_activity: str = ""
        self.last_tool_name: str = ""
        self.summary: str = ""


# ---------------------------------------------------------------------------
# Managed task (internal wrapper with richer state than TaskInfo)
# ---------------------------------------------------------------------------


class _ManagedTask:
    """Internal task record — richer than the public ``TaskInfo``."""

    __slots__ = (
        "task_id",
        "task_type",
        "status",
        "description",
        "prompt",
        "cwd",
        "result",
        "error",
        "output_file",
        "agent_type",
        "start_time",
        "end_time",
        "progress",
        "abort_event",
        "completion_event",
        "async_task",
        "is_backgrounded",
        "notified",
    )

    def __init__(
        self,
        *,
        task_id: str,
        task_type: TaskType,
        description: str,
        prompt: str = "",
        cwd: str = "",
        agent_type: str = "",
    ) -> None:
        self.task_id = task_id
        self.task_type = task_type
        self.status = TaskStatus.RUNNING
        self.description = description
        self.prompt = prompt
        self.cwd = cwd
        self.result: Any = None
        self.error: str | None = None
        self.output_file = _get_task_output_path(task_id)
        self.agent_type = agent_type
        self.start_time = time.time()
        self.end_time: float | None = None
        self.progress = AgentProgress()
        self.abort_event: asyncio.Event | None = None
        self.completion_event: asyncio.Event = asyncio.Event()
        self.async_task: asyncio.Task[None] | None = None
        self.is_backgrounded = True
        self.notified = False

    def to_info(self) -> TaskInfo:
        """Build a public ``TaskInfo`` snapshot."""
        return TaskInfo(
            task_id=self.task_id,
            task_type=self.task_type,
            status=self.status,
            description=self.description,
            result=self.result,
        )

    def to_detail(self) -> dict[str, Any]:
        """Build a rich detail dict (for TaskGet / TaskOutput)."""
        d: dict[str, Any] = {
            "task_id": self.task_id,
            "type": str(self.task_type),
            "status": str(self.status),
            "description": self.description,
            "agent_type": self.agent_type,
            "output_file": self.output_file,
            "start_time": self.start_time,
            "duration_ms": int((time.time() - self.start_time) * 1000),
        }
        if self.end_time:
            d["duration_ms"] = int((self.end_time - self.start_time) * 1000)
        if self.progress:
            d["tool_use_count"] = self.progress.tool_use_count
            d["token_count"] = self.progress.token_count
            d["last_activity"] = self.progress.last_activity
        if self.result is not None:
            d["result"] = self.result
        if self.error:
            d["error"] = self.error
        return d


# ---------------------------------------------------------------------------
# Notification queue (translation of enqueueAgentNotification)
# ---------------------------------------------------------------------------


class _Notification:
    __slots__ = ("task_id", "description", "status", "message", "timestamp")

    def __init__(self, *, task_id: str, description: str, status: str, message: str) -> None:
        self.task_id = task_id
        self.description = description
        self.status = status
        self.message = message
        self.timestamp = time.time()


# ---------------------------------------------------------------------------
# TaskManager (singleton-like, held by QueryEngine)
# ---------------------------------------------------------------------------


class TaskManager:
    """Unified task registry — translation of AppState.tasks + task helpers.

    Backs both Agent and Bash background tasks. The ``AgentTool`` and
    ``run_async_agent_lifecycle`` interact with this via:

        register_agent() → update_progress() → complete_task() / fail_task() / kill_task()

    The ``TaskOutputTool`` / ``TaskListTool`` / ``TaskStopTool`` read from this.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, _ManagedTask] = {}
        self._notifications: list[_Notification] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_agent(
        self,
        *,
        agent_id: str,
        description: str,
        prompt: str = "",
        agent_type: str = "",
        cwd: str = "",
    ) -> str:
        """Register a background agent as a tracked task.

        Translation of registerAsyncAgent from LocalAgentTask.ts.

        Returns the task_id (=== agent_id).
        """
        managed = _ManagedTask(
            task_id=agent_id,
            task_type=TaskType.LOCAL_AGENT,
            description=description,
            prompt=prompt,
            agent_type=agent_type,
            cwd=cwd,
        )
        managed.abort_event = asyncio.Event()
        self._tasks[agent_id] = managed

        # Write initial header to output file
        self._write_output(agent_id, {
            "type": "agent_start",
            "agent_id": agent_id,
            "description": description,
            "agent_type": agent_type,
            "prompt": prompt,
            "ts": time.time(),
        })

        logger.info("Registered agent task %s: %s", agent_id, description)
        return agent_id

    async def create_task(
        self,
        *,
        description: str,
        prompt: str,
        task_type: TaskType | str = TaskType.LOCAL_AGENT,
        cwd: str = "",
        run_fn: Callable[..., Any] | None = None,
    ) -> str:
        """Create and start a new task (bash or agent).

        For agent tasks, prefer ``register_agent`` + external lifecycle.
        This method is primarily for Bash background tasks.
        """
        resolved_type = coerce_str_enum(TaskType, task_type, default=TaskType.LOCAL_AGENT)
        task_id = str(uuid.uuid4())[:8]

        managed = _ManagedTask(
            task_id=task_id,
            task_type=resolved_type,
            description=description,
            prompt=prompt,
            cwd=cwd,
        )
        self._tasks[task_id] = managed

        if run_fn is not None:
            managed.async_task = asyncio.create_task(
                self._run_with_fn(task_id, run_fn, prompt, cwd),
                name=f"task-{task_id}",
            )
        elif resolved_type == TaskType.LOCAL_BASH:
            managed.async_task = asyncio.create_task(
                self._run_bash(task_id, prompt, cwd),
                name=f"task-{task_id}",
            )

        return task_id

    # ------------------------------------------------------------------
    # Progress updates (translation of updateAsyncAgentProgress)
    # ------------------------------------------------------------------

    def update_progress(
        self,
        task_id: str,
        *,
        tool_use_count: int = 0,
        token_count: int = 0,
        last_activity: str = "",
        last_tool_name: str = "",
    ) -> None:
        """Update a running task's progress counters."""
        managed = self._tasks.get(task_id)
        if managed is None or managed.progress is None:
            return
        managed.progress.tool_use_count = tool_use_count
        managed.progress.token_count = token_count
        if last_activity:
            managed.progress.last_activity = last_activity
        if last_tool_name:
            managed.progress.last_tool_name = last_tool_name

    def append_output(self, task_id: str, event: dict[str, Any]) -> None:
        """Append a structured event to the task's output file.

        Translation of DiskTaskOutput.append / appendTaskOutput.
        """
        self._write_output(task_id, event)

    # ------------------------------------------------------------------
    # State transitions (translation of complete/fail/kill)
    # ------------------------------------------------------------------

    def complete_task(self, task_id: str, result: Any = None) -> None:
        """Mark a task as completed.

        Translation of completeAgentTask from LocalAgentTask.ts.
        """
        managed = self._tasks.get(task_id)
        if managed is None:
            return
        managed.status = TaskStatus.COMPLETED
        managed.result = result
        managed.end_time = time.time()
        managed.completion_event.set()

        # Write final status to output file
        self._write_output(task_id, {
            "type": "final_status",
            "status": "completed",
            "result": self._extract_result_text(result),
            "duration_ms": int((managed.end_time - managed.start_time) * 1000),
            "ts": managed.end_time,
        })

        logger.info("Agent task %s completed", task_id)

    def fail_task(self, task_id: str, error: str) -> None:
        """Mark a task as failed.

        Translation of failAgentTask from LocalAgentTask.ts.
        """
        managed = self._tasks.get(task_id)
        if managed is None:
            return
        managed.status = TaskStatus.FAILED
        managed.error = error
        managed.end_time = time.time()
        managed.completion_event.set()

        self._write_output(task_id, {
            "type": "final_status",
            "status": "failed",
            "error": error,
            "ts": managed.end_time,
        })

        logger.error("Agent task %s failed: %s", task_id, error)

    def kill_task(self, task_id: str) -> None:
        """Mark a task as killed and signal abort.

        Translation of killAsyncAgent from LocalAgentTask.ts.
        """
        managed = self._tasks.get(task_id)
        if managed is None:
            return
        managed.status = TaskStatus.KILLED
        managed.end_time = time.time()
        managed.completion_event.set()

        # Signal abort
        if managed.abort_event:
            managed.abort_event.set()
        if managed.async_task and not managed.async_task.done():
            managed.async_task.cancel()

        self._write_output(task_id, {
            "type": "final_status",
            "status": "killed",
            "ts": managed.end_time,
        })

        logger.info("Agent task %s killed", task_id)

    # ------------------------------------------------------------------
    # Notifications (translation of enqueueAgentNotification)
    # ------------------------------------------------------------------

    def enqueue_notification(
        self,
        *,
        task_id: str,
        description: str,
        status: str,
        message: str = "",
    ) -> None:
        """Enqueue a task-notification for injection into the main conversation.

        Translation of enqueueAgentNotification from LocalAgentTask.ts.
        """
        managed = self._tasks.get(task_id)
        if managed and managed.notified:
            return  # prevent duplicate notifications
        if managed:
            managed.notified = True

        self._notifications.append(_Notification(
            task_id=task_id,
            description=description,
            status=status,
            message=message,
        ))

    def drain_notifications(self) -> list[str]:
        """Drain all pending notifications as XML strings.

        Translation of enqueueAgentNotification → enqueuePendingNotification
        → print.ts task-notification parser flow.

        Returns a list of ``<task-notification>`` XML blocks ready for
        injection into the message stream.
        """
        if not self._notifications:
            return []

        result: list[str] = []
        for notif in self._notifications:
            managed = self._tasks.get(notif.task_id)
            output_file = managed.output_file if managed else ""
            tool_use_id = ""

            xml = (
                f"<task-notification>\n"
                f"  <task-id>{notif.task_id}</task-id>\n"
                f"  <output-file>{output_file}</output-file>\n"
                f"  <status>{notif.status}</status>\n"
                f"  <summary>Agent \"{notif.description}\" {notif.status}</summary>\n"
            )
            if notif.message:
                xml += f"  <result>{notif.message}</result>\n"
            xml += f"</task-notification>"
            result.append(xml)

        self._notifications.clear()
        return result

    # ------------------------------------------------------------------
    # Query (used by Task tools)
    # ------------------------------------------------------------------

    def get_task(self, task_id: str) -> TaskInfo | None:
        """Get public TaskInfo by ID."""
        managed = self._tasks.get(task_id)
        if managed is None:
            return None
        return managed.to_info()

    def get_task_detail(self, task_id: str) -> dict[str, Any] | None:
        """Get rich task detail (for TaskGet / TaskOutput)."""
        managed = self._tasks.get(task_id)
        if managed is None:
            return None
        return managed.to_detail()

    def get_output_path(self, task_id: str) -> str:
        """Get the output file path for a task."""
        managed = self._tasks.get(task_id)
        if managed:
            return managed.output_file
        return _get_task_output_path(task_id)

    def get_output(self, task_id: str, max_bytes: int = 8 * 1024 * 1024) -> str:
        """Read the task's output file (last ``max_bytes``).

        Translation of getTaskOutput → tailFile.
        """
        managed = self._tasks.get(task_id)
        if managed is None:
            return ""

        # If completed with in-memory result, prefer that
        if managed.status == TaskStatus.COMPLETED and managed.result:
            return self._extract_result_text(managed.result)

        # Otherwise read the disk output file
        path = managed.output_file
        if not os.path.isfile(path):
            return ""

        try:
            size = os.path.getsize(path)
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                if size > max_bytes:
                    fh.seek(size - max_bytes)
                    fh.readline()  # skip partial line
                return fh.read()
        except OSError:
            return ""

    def get_abort_event(self, task_id: str) -> asyncio.Event | None:
        """Get the abort event for a task (used by lifecycle)."""
        managed = self._tasks.get(task_id)
        return managed.abort_event if managed else None

    def list_tasks(
        self,
        status_filter: TaskStatus | str | None = None,
    ) -> list[TaskInfo]:
        """List all tasks, optionally filtered by status."""
        tasks = [m.to_info() for m in self._tasks.values()]
        if status_filter is not None:
            resolved = maybe_coerce_str_enum(TaskStatus, status_filter)
            if resolved is not None:
                tasks = [t for t in tasks if t.status == resolved]
        return tasks

    async def stop_task(self, task_id: str) -> bool:
        """Stop a running task.

        Translation of stopTask from Task.ts.
        """
        managed = self._tasks.get(task_id)
        if managed is None:
            return False

        if managed.status not in (TaskStatus.RUNNING, TaskStatus.PENDING):
            return False

        self.kill_task(task_id)
        return True

    async def update_task(self, task_id: str, message: str) -> bool:
        """Send an update message to a running agent task.

        Translation of queuePendingMessage / SendMessage routing.
        """
        managed = self._tasks.get(task_id)
        if managed is None:
            return False
        logger.info("Task %s received message: %s", task_id, message[:200])
        return True

    async def wait_for_task(self, task_id: str) -> Any:
        """Wait for a task to complete, return its result."""
        managed = self._tasks.get(task_id)
        if managed is None:
            return None
        if managed.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.KILLED):
            return managed.result
        await managed.completion_event.wait()
        return managed.result

    async def cleanup(self) -> None:
        """Cancel all running tasks."""
        for managed in self._tasks.values():
            if managed.async_task and not managed.async_task.done():
                managed.async_task.cancel()
            if managed.abort_event:
                managed.abort_event.set()

        tasks = [
            m.async_task for m in self._tasks.values()
            if m.async_task and not m.async_task.done()
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_output(self, task_id: str, record: dict[str, Any]) -> None:
        """Append a JSON record to the task's output file."""
        managed = self._tasks.get(task_id)
        path = managed.output_file if managed else _get_task_output_path(task_id)
        try:
            os.makedirs(os.path.dirname(path) or "/tmp", exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    async def _run_with_fn(
        self,
        task_id: str,
        run_fn: Callable[..., Any],
        prompt: str,
        cwd: str,
    ) -> None:
        managed = self._tasks.get(task_id)
        if managed is None:
            return
        try:
            result = await run_fn(prompt, cwd)
            self.complete_task(task_id, result)
        except asyncio.CancelledError:
            self.kill_task(task_id)
        except Exception as exc:
            self.fail_task(task_id, str(exc))

    async def _run_bash(self, task_id: str, command: str, cwd: str) -> None:
        managed = self._tasks.get(task_id)
        if managed is None:
            return
        try:
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
            self.complete_task(task_id, output.strip())
        except asyncio.CancelledError:
            self.kill_task(task_id)
        except Exception as exc:
            self.fail_task(task_id, str(exc))

    @staticmethod
    def _extract_result_text(result: Any) -> str:
        """Extract readable text from an agent result (dict or str)."""
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            content = result.get("content", [])
            if isinstance(content, list):
                texts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))
                if texts:
                    return "\n".join(texts)
            # Try other common dict keys
            for key in ("result", "data", "output"):
                val = result.get(key)
                if val:
                    return str(val)
        return str(result) if result else ""
