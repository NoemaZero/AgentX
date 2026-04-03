"""LLM 供应商共享类型定义 — 参考 harness-clawd/llm/types.py."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, NamedTuple, TypeAlias

# --------------------------------------------------------------------------- 消息
ContentPart: TypeAlias = str | dict[str, Any]
"""单条内容片段，与 OpenAI API 原生格式一致。"""

Messages: TypeAlias = list[dict[str, Any]]
"""对话消息列表，OpenAI Chat Completions 格式。"""

# --------------------------------------------------------------------------- 工具
ToolParam: TypeAlias = dict[str, Any]
"""OpenAI function-calling 工具描述。"""

# --------------------------------------------------------------------------- 响应格式
ResponseFormat: TypeAlias = dict[str, Any]
"""响应格式约束: {"type": "text"} | {"type": "json_object"} | ...。"""


# --------------------------------------------------------------------------- 流式输出

class StreamStatus(StrEnum):
    """流式对话中每个事件帧的状态。

    EnableThinking=True:
        THINKING_START → THINKING ×N → THINKING_END → ANSWER_START → ANSWERING ×N → ANSWER_END

    EnableThinking=False:
        ANSWER_START → ANSWERING ×N → ANSWER_END
    """

    THINKING_START = "thinking_start"
    THINKING = "thinking"
    THINKING_END = "thinking_end"
    ANSWER_START = "answer_start"
    ANSWERING = "answering"
    ANSWER_END = "answer_end"


class StreamOutput:
    """流式对话的单个事件帧 — 不可变。"""

    __slots__ = ("status", "thinking", "thinking_snapshot", "answer", "answer_snapshot",
                 "tool_calls_delta", "usage", "finish_reason")

    def __init__(
        self,
        status: StreamStatus,
        *,
        thinking: str | None = None,
        thinking_snapshot: str | None = None,
        answer: str | None = None,
        answer_snapshot: str | None = None,
        tool_calls_delta: list[dict[str, Any]] | None = None,
        usage: dict[str, int] | None = None,
        finish_reason: str | None = None,
    ) -> None:
        self.status = status
        self.thinking = thinking
        self.thinking_snapshot = thinking_snapshot
        self.answer = answer
        self.answer_snapshot = answer_snapshot
        self.tool_calls_delta = tool_calls_delta
        self.usage = usage
        self.finish_reason = finish_reason


class ResponseResult(NamedTuple):
    """非流式调用最终结果，拆分思考和回答。"""

    thinking: str
    answer: str
