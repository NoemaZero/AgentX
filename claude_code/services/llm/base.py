"""LLM 供应商抽象基类 — 参考 harness-clawd/llm/base.py。

核心对话逻辑（invoke / _invoke / _stream）封装在此，
子类只需实现 ``model`` 属性和 ``build_client()`` 方法。
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Iterable

import httpx
from openai import AsyncOpenAI
from openai.types.chat.chat_completion_message import ChatCompletionMessage

from .types import Messages, ResponseFormat, ResponseResult, StreamOutput, StreamStatus

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """所有 LLM 供应商的抽象基类。

    子类只需实现:
      - ``model``        — 返回模型名称
      - ``build_client`` — 返回已配置的 AsyncOpenAI 实例
    """

    name: str = "base"

    @staticmethod
    def _build_http_client(
        ssl_verify: bool = True,
        *,
        max_connections: int = 100,
        keepalive_expiry: float = 30,
    ) -> httpx.AsyncClient:
        """构建带 SSL 和连接池配置的 httpx 客户端。"""
        return httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(
                verify=ssl_verify,
                limits=httpx.Limits(
                    max_connections=max_connections,
                    max_keepalive_connections=max_connections,
                    keepalive_expiry=keepalive_expiry,
                ),
            ),
        )

    # ── 思考块正则 ──
    _thinking_pattern = re.compile(r"</?think>", flags=re.DOTALL)
    _content_pattern = re.compile(r"(<think>)?.*?</think>", flags=re.DOTALL)

    # ── 抽象接口 ──

    @property
    @abstractmethod
    def model(self) -> str:
        """供应商默认模型名称。"""

    @abstractmethod
    def build_client(self) -> AsyncOpenAI:
        """构建并返回 AsyncOpenAI 客户端。"""

    # ── 日志 ──

    @staticmethod
    def _record_log(messages: Messages, tools: Any = None) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        items = list(messages)
        body = "\n".join(f"[{m.get('role', '?')}]: {str(m.get('content', ''))[:200]}" for m in items)
        logger.debug("LLM Messages:\n%s", body)

    # ── 公共入口 ──

    def invoke(
        self,
        messages: Messages,
        *,
        model: str | None = None,
        response_format: ResponseFormat | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
        extra_body: dict[str, Any] | None = None,
        stream: bool = False,
        **extra: Any,
    ) -> Any:
        """对话入口，根据 stream 自动分发到 _invoke 或 _stream。"""
        resolved = model or self.model
        self._record_log(messages, tools=tools)
        if stream:
            return self._stream(
                messages=messages,
                model=resolved,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
                extra_body=extra_body,
                **extra,
            )
        return self._invoke(
            messages=messages,
            model=resolved,
            response_format=response_format,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_body=extra_body,
            **extra,
        )

    # ── 非流式实现 ──

    async def _invoke(
        self,
        messages: Messages,
        model: str,
        response_format: ResponseFormat | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
        extra_body: dict[str, Any] | None = None,
        **extra: Any,
    ) -> ChatCompletionMessage:
        """非流式实现。tool_call.function.arguments 自动容错修复。"""
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            **extra,
        }
        if response_format is not None:
            params["response_format"] = response_format
        if tools:
            params["tools"] = tools
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if extra_body is not None:
            params["extra_body"] = extra_body

        response = await self.build_client().chat.completions.create(**params)
        message = response.choices[0].message

        # 自动修复 tool call 参数
        if message.tool_calls:
            try:
                import json_repair
                for tc in message.tool_calls:
                    tc.function.arguments = json_repair.loads(tc.function.arguments)
            except ImportError:
                pass  # json_repair 不可用时跳过

        return message

    # ── 流式实现 ──

    async def _stream(
        self,
        messages: Messages,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
        extra_body: dict[str, Any] | None = None,
        **extra: Any,
    ) -> AsyncGenerator[StreamOutput, None]:
        """通用流式实现，子类可覆盖以处理思考块等特殊逻辑。"""
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
            **extra,
        }
        if tools:
            params["tools"] = tools
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if extra_body is not None:
            params["extra_body"] = extra_body

        in_answering = False

        async for chunk in await self.build_client().chat.completions.create(**params):
            # Usage
            if chunk.usage:
                yield StreamOutput(
                    status=StreamStatus.ANSWERING if in_answering else StreamStatus.ANSWER_START,
                    usage={
                        "prompt_tokens": chunk.usage.prompt_tokens or 0,
                        "completion_tokens": chunk.usage.completion_tokens or 0,
                    },
                )

            if not chunk.choices:
                continue

            choice = chunk.choices[0]

            if choice.finish_reason:
                yield StreamOutput(
                    status=StreamStatus.ANSWER_END,
                    finish_reason=choice.finish_reason,
                )
                continue

            delta = choice.delta
            if delta is None:
                continue

            # Tool call deltas
            if delta.tool_calls:
                tc_list = []
                for tc_delta in delta.tool_calls:
                    tc_list.append({
                        "index": tc_delta.index,
                        "id": tc_delta.id,
                        "function_name": tc_delta.function.name if tc_delta.function else None,
                        "function_args": tc_delta.function.arguments if tc_delta.function else None,
                    })
                yield StreamOutput(
                    status=StreamStatus.ANSWERING,
                    tool_calls_delta=tc_list,
                )

            # Content delta
            if delta.content:
                if not in_answering:
                    in_answering = True
                    yield StreamOutput(status=StreamStatus.ANSWER_START)
                yield StreamOutput(status=StreamStatus.ANSWERING, answer=delta.content)

        if in_answering:
            yield StreamOutput(status=StreamStatus.ANSWER_END)

    # ── 工具方法 ──

    def split_think(self, content: str) -> ResponseResult:
        """将模型输出拆分为思考部分和回答部分。"""
        answer = self._content_pattern.sub("", content)
        thinking = self._thinking_pattern.sub("", content.replace(answer, ""))
        return ResponseResult(thinking=thinking, answer=answer)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.model!r})"
