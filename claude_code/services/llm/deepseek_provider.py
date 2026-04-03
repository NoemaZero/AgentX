"""DeepSeek API 供应商。

适用于:
  - deepseek-chat      — 通用对话模型
  - deepseek-reasoner  — R1 推理模型（含 reasoning_content 思考字段）

环境变量:
  DEEPSEEK_API_KEY   API 密钥
  DEEPSEEK_MODEL     模型名称（默认 deepseek-chat）
"""

from __future__ import annotations

import os
from typing import Any, AsyncGenerator

from openai import AsyncOpenAI

from typing import Any

from .base import LLMProvider
from .types import Messages, StreamOutput, StreamStatus

_DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
_DEFAULT_MODEL = "deepseek-chat"


class DeepSeekProvider(LLMProvider):
    """DeepSeek 官方 API 供应商。

    流式模式兼容 deepseek-chat（普通增量）和
    deepseek-reasoner（delta.reasoning_content 思考字段）。
    """

    name: str = "deepseek"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        ssl_verify: bool = True,
        max_retries: int = 3,
    ) -> None:
        self._api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self._model = model or os.environ.get("DEEPSEEK_MODEL", _DEFAULT_MODEL)
        self._base_url = base_url or os.environ.get("DEEPSEEK_BASE_URL", _DEFAULT_BASE_URL)
        self._ssl_verify = ssl_verify
        self._max_retries = max_retries
        self._client: AsyncOpenAI | None = None

    @property
    def model(self) -> str:
        return self._model

    def build_client(self) -> AsyncOpenAI:
        if self._client is None:
            kwargs: dict[str, Any] = {
                "api_key": self._api_key,
                "base_url": self._base_url,
                "max_retries": self._max_retries,
            }
            if not self._ssl_verify:
                kwargs["http_client"] = self._build_http_client(ssl_verify=False)
            self._client = AsyncOpenAI(**kwargs)
        return self._client

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
        """流式实现，自动识别 reasoner 思考字段 (reasoning_content)。"""
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

        in_thinking = False
        in_answering = False
        thinking_parts: list[str] = []

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
                if in_thinking:
                    yield StreamOutput(
                        status=StreamStatus.THINKING_END,
                        thinking_snapshot="".join(thinking_parts),
                    )
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
                yield StreamOutput(status=StreamStatus.ANSWERING, tool_calls_delta=tc_list)

            # 思考内容（仅 deepseek-reasoner）
            reasoning: str | None = getattr(delta, "reasoning_content", None)
            if reasoning:
                if not in_thinking:
                    in_thinking = True
                    yield StreamOutput(status=StreamStatus.THINKING_START)
                thinking_parts.append(reasoning)
                yield StreamOutput(status=StreamStatus.THINKING, thinking=reasoning)

            # 回答内容
            if delta.content:
                if in_thinking:
                    in_thinking = False
                    yield StreamOutput(
                        status=StreamStatus.THINKING_END,
                        thinking_snapshot="".join(thinking_parts),
                    )
                if not in_answering:
                    in_answering = True
                    yield StreamOutput(status=StreamStatus.ANSWER_START)
                yield StreamOutput(status=StreamStatus.ANSWERING, answer=delta.content)

        if in_thinking:
            yield StreamOutput(
                status=StreamStatus.THINKING_END,
                thinking_snapshot="".join(thinking_parts),
            )
        if not in_answering:
            yield StreamOutput(status=StreamStatus.ANSWER_END)
