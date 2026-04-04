"""Core types — strict translation of Tool.ts + types/permissions.ts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, NewType, TypeVar

from pydantic import Field, field_validator

from claude_code.pydantic_models import FrozenModel

EnumT = TypeVar("EnumT", bound=StrEnum)

# ── Branded ID types (from types/ids.ts) ──
SessionId = NewType("SessionId", str)
AgentId = NewType("AgentId", str)


def coerce_str_enum(
    enum_cls: type[EnumT],
    value: EnumT | str | None,
    *,
    default: EnumT,
) -> EnumT:
    """Convert a raw string to a StrEnum value with a safe default."""
    if isinstance(value, enum_cls):
        return value
    if value in (None, ""):
        return default
    try:
        return enum_cls(value)
    except ValueError:
        return default


def maybe_coerce_str_enum(
    enum_cls: type[EnumT],
    value: EnumT | str | None,
) -> EnumT | None:
    """Convert a raw string to a StrEnum value, returning None if empty/invalid."""
    if isinstance(value, enum_cls):
        return value
    if value in (None, ""):
        return None
    try:
        return enum_cls(value)
    except ValueError:
        return None

# ── Permission types (from types/permissions.ts) ──


class PermissionMode(StrEnum):
    ACCEPT_EDITS = "acceptEdits"
    AUTO = "auto"
    BUBBLE = "bubble"
    BYPASS_PERMISSIONS = "bypassPermissions"
    DEFAULT = "default"
    DONT_ASK = "dontAsk"
    PLAN = "plan"


class ProviderType(StrEnum):
    """LLM provider type — replaces raw string in Config."""

    AUTO = ""  # auto-detect from base_url
    CUSTOM = "custom"
    DEEPSEEK = "deepseek"
    OPENAI = "openai"


class PermissionBehavior(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PermissionDecision(StrEnum):
    """User's response to an interactive permission prompt."""

    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    DENY = "deny"


class PermissionRuleSource(StrEnum):
    CLI_ARG = "cliArg"
    COMMAND = "command"
    FLAG_SETTINGS = "flagSettings"
    LOCAL_SETTINGS = "localSettings"
    POLICY_SETTINGS = "policySettings"
    PROJECT_SETTINGS = "projectSettings"
    SESSION = "session"
    USER_SETTINGS = "userSettings"


class MessageRole(StrEnum):
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"
    USER = "user"


class StreamEventType(StrEnum):
    AGENT_NOTIFICATION = "agent_notification"
    ASSISTANT_MESSAGE = "assistant_message"
    AUTO_COMPACT = "auto_compact"
    CONTENT_DELTA = "content_delta"
    ERROR = "error"
    MAX_TURNS_REACHED = "max_turns_reached"
    QUERY_COMPLETE = "query_complete"
    QUERY_ERROR = "query_error"
    STREAM_END = "stream_end"
    STREAM_REQUEST_START = "stream_request_start"
    STREAM_START = "stream_start"
    THINKING_DELTA = "thinking_delta"
    THINKING_END = "thinking_end"
    TOOL_RESULT = "tool_result"
    TOOL_USE = "tool_use"
    USAGE = "usage"


class ToolParameterType(StrEnum):
    ARRAY = "array"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMBER = "number"
    OBJECT = "object"
    STRING = "string"


class ToolExecutionStatus(StrEnum):
    COMPLETED = "completed"
    ERROR = "error"
    PERMISSION_DENIED = "permission_denied"
    STARTED = "started"


class TaskType(StrEnum):
    DREAM = "dream"
    IN_PROCESS_TEAMMATE = "in_process_teammate"
    LOCAL_AGENT = "local_agent"
    LOCAL_BASH = "local_bash"
    LOCAL_WORKFLOW = "local_workflow"
    MONITOR_MCP = "monitor_mcp"
    REMOTE_AGENT = "remote_agent"


class TaskStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"
    PENDING = "pending"
    RUNNING = "running"


class QuerySource(StrEnum):
    AGENT = "agent"
    API = "api"
    REPL = "repl"
    TASK = "task"


class AgentExecutionMode(StrEnum):
    BACKGROUND = "background"
    FORK = "fork"
    FOREGROUND = "foreground"


class AgentContextMode(StrEnum):
    FORK = "fork"


class AgentModel(StrEnum):
    HAIKU = "haiku"
    INHERIT = "inherit"
    OPUS = "opus"
    SONNET = "sonnet"


class ConfigAction(StrEnum):
    GET = "get"
    SET = "set"


class GrepOutputMode(StrEnum):
    CONTENT = "content"
    COUNT = "count"
    FILES_WITH_MATCHES = "files_with_matches"


class NotebookCellType(StrEnum):
    CODE = "code"
    MARKDOWN = "markdown"


class NotebookEditMode(StrEnum):
    DELETE = "delete"
    INSERT = "insert"
    REPLACE = "replace"


class SkillSource(StrEnum):
    BUNDLED = "bundled"
    MANAGED = "managed"
    MCP = "mcp"
    PROJECT = "project"
    USER = "user"


class PermissionRuleValue(FrozenModel):
    tool_name: str
    rule_content: str | None = None


class PermissionRule(FrozenModel):
    source: PermissionRuleSource
    rule_behavior: PermissionBehavior
    rule_value: PermissionRuleValue

    @field_validator("source", mode="before")
    @classmethod
    def _coerce_source(cls, v: Any) -> PermissionRuleSource:
        return coerce_str_enum(PermissionRuleSource, v, default=PermissionRuleSource.USER_SETTINGS)

    @field_validator("rule_behavior", mode="before")
    @classmethod
    def _coerce_behavior(cls, v: Any) -> PermissionBehavior:
        return coerce_str_enum(PermissionBehavior, v, default=PermissionBehavior.ASK)


class PermissionResult(FrozenModel):
    behavior: PermissionBehavior
    updated_input: dict[str, Any] | None = None
    message: str | None = None

    @field_validator("behavior", mode="before")
    @classmethod
    def _coerce_behavior(cls, v: Any) -> PermissionBehavior:
        return coerce_str_enum(PermissionBehavior, v, default=PermissionBehavior.ASK)


# ── Message types (OpenAI format) ──
class SystemMessage(FrozenModel):
    role: MessageRole = MessageRole.SYSTEM
    content: str = ""

    @field_validator("role", mode="before")
    @classmethod
    def _coerce_role(cls, v: Any) -> MessageRole:
        return coerce_str_enum(MessageRole, v, default=MessageRole.SYSTEM)


class UserMessage(FrozenModel):
    role: MessageRole = MessageRole.USER
    content: str | list[dict[str, Any]] = ""

    @field_validator("role", mode="before")
    @classmethod
    def _coerce_role(cls, v: Any) -> MessageRole:
        return coerce_str_enum(MessageRole, v, default=MessageRole.USER)


class AssistantMessage(FrozenModel):
    role: MessageRole = MessageRole.ASSISTANT
    content: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("role", mode="before")
    @classmethod
    def _coerce_role(cls, v: Any) -> MessageRole:
        return coerce_str_enum(MessageRole, v, default=MessageRole.ASSISTANT)


class ToolResultMessage(FrozenModel):
    role: MessageRole = MessageRole.TOOL
    tool_call_id: str = ""
    content: str = ""

    @field_validator("role", mode="before")
    @classmethod
    def _coerce_role(cls, v: Any) -> MessageRole:
        return coerce_str_enum(MessageRole, v, default=MessageRole.TOOL)


Message = SystemMessage | UserMessage | AssistantMessage | ToolResultMessage


# ── Stream event types ──
class StreamEvent(FrozenModel):
    type: StreamEventType
    data: Any = None

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type(cls, v: Any) -> StreamEventType:
        return coerce_str_enum(StreamEventType, v, default=StreamEventType.ERROR)


# ── Validation result (from Tool.ts) ──
class ValidationResult(FrozenModel):
    result: bool
    message: str = ""
    error_code: int = 0


# ── Tool result (from Tool.ts) ──
class ToolResult(FrozenModel):
    data: Any = None
    new_messages: list[Message] = Field(default_factory=list)


# ── Usage tracking ──
class Usage(FrozenModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class TaskInfo(FrozenModel):
    task_id: str
    task_type: TaskType
    status: TaskStatus
    description: str = ""
    result: Any = None

    @field_validator("task_type", mode="before")
    @classmethod
    def _coerce_task_type(cls, v: Any) -> TaskType:
        return coerce_str_enum(TaskType, v, default=TaskType.LOCAL_AGENT)

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, v: Any) -> TaskStatus:
        return coerce_str_enum(TaskStatus, v, default=TaskStatus.PENDING)


