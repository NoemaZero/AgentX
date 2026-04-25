"""自定义 / OpenAI 兼容 API 供应商。

适用于任何 OpenAI Chat Completions 兼容服务:
  - Anthropic (via OpenAI-compat)
  - Azure OpenAI
  - Ollama / vLLM / llama.cpp
  - LiteLLM 代理
  - 任何 base_url + api_key 形式的服务

环境变量:
  CUSTOM_API_KEY     API 密钥
  CUSTOM_BASE_URL    服务端 base_url（必填）
  CUSTOM_MODEL       模型名称（必填）
"""

from __future__ import annotations

import os
from typing import Any, AsyncGenerator

from openai import AsyncOpenAI

from .base import LLMProvider
from .types import Messages, ResponseFormat, StreamOutput, StreamStatus

_THINKING_DISABLED_BODY = {"chat_template_kwargs": {"enable_thinking": False}}


class CustomProvider(LLMProvider):
    """OpenAI 兼容接口的自定义供应商。

    特性:
      - 自动禁用 thinking（Qwen3 等模型），调用方可通过 extra_body 覆盖
      - SSL 验证可配置
      - 连接池配置（高并发场景）
      - 支持 <think>…</think> 思考块解析
    """

    name: str = "custom"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        ssl_verify: bool = True,
        max_connections: int = 100,
        keepalive_expiry: float = 1800,
        max_retries: int = 3,
        timeout: float = 1800,
    ) -> None:
        self._base_url = base_url or os.environ.get("CUSTOM_BASE_URL", "")
        self._api_key = api_key or os.environ.get("CUSTOM_API_KEY", "sk-placeholder")
        self._model = model or os.environ.get("CUSTOM_MODEL", "")
        self._ssl_verify = ssl_verify
        self._max_connections = max_connections
        self._keepalive_expiry = keepalive_expiry
        self._max_retries = max_retries
        self._timeout = timeout
        self._client: AsyncOpenAI | None = None

        if not self._base_url:
            raise ValueError("CustomProvider requires base_url or CUSTOM_BASE_URL env var")
        if not self._model:
            raise ValueError("CustomProvider requires model or CUSTOM_MODEL env var")

    @property
    def model(self) -> str:
        return self._model

    def build_client(self) -> AsyncOpenAI:
        if self._client is None:
            http_client = self._build_http_client(
                ssl_verify=self._ssl_verify,
                max_connections=self._max_connections,
                keepalive_expiry=self._keepalive_expiry,
            )
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                max_retries=self._max_retries,
                timeout=self._timeout,
                http_client=http_client,
            )
        return self._client

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
    ) -> Any:
        """非流式实现，默认关闭思考模式。

        若调用方未显式传入 extra_body，则注入
        {"chat_template_kwargs": {"enable_thinking": False}}
        以禁用 Qwen3 等模型的推理输出。
        """
        if extra_body is None:
            extra_body = dict(_THINKING_DISABLED_BODY)
        return await super()._invoke(
            messages=messages,
            model=model,
            response_format=response_format,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_body=extra_body,
            **extra,
        )

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
        """流式实现，解析 <think>…</think> 思考块。

        启用思考模式时（extra_body 中 enable_thinking=True），
        自动检测 <think>/</ think> 标记并分发 THINKING_* 事件。
        """
        if extra_body is None:
            # extra_body = dict(_THINKING_DISABLED_BODY)
            extra_body = {"chat_template_kwargs": {"enable_thinking": True}}
        enable_thinking = (
            (extra_body or {}).get("chat_template_kwargs", {}).get("enable_thinking", False)
        )

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

        in_thinking = enable_thinking
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
                    in_thinking = False
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

            content: str | None = delta.content
            if not content:
                continue

            # 思考块解析
            if in_thinking:
                if content == "<think>":
                    yield StreamOutput(status=StreamStatus.THINKING_START)
                elif content == "</think>":
                    in_thinking = False
                    yield StreamOutput(
                        status=StreamStatus.THINKING_END,
                        thinking_snapshot="".join(thinking_parts),
                    )
                else:
                    thinking_parts.append(content)
                    yield StreamOutput(status=StreamStatus.THINKING, thinking=content)
            else:
                if not in_answering:
                    in_answering = True
                    yield StreamOutput(status=StreamStatus.ANSWER_START)
                yield StreamOutput(status=StreamStatus.ANSWERING, answer=content)

        if in_thinking:
            yield StreamOutput(
                status=StreamStatus.THINKING_END,
                thinking_snapshot="".join(thinking_parts),
            )
        if in_answering:
            yield StreamOutput(status=StreamStatus.ANSWER_END)
