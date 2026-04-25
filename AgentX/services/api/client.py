"""LLM API 客户端 — 桥接层，委托给具体 Provider 实现。

支持三种供应商:
  - openai   — 官方 OpenAI API
  - deepseek — DeepSeek 官方 API（含 R1 推理）
  - custom   — 任何 OpenAI 兼容端点（LiteLLM/vLLM/Ollama 等）

LLMClient 保留原有的 stream_chat / single_chat 接口，
内部通过 Provider 抽象统一调度。
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from AgentX.config import Config
from AgentX.data_types import (
    AssistantMessage,
    Message,
    StreamEvent,
    StreamEventType,
    SystemMessage,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from AgentX.services.llm import (
    LLMProvider,
    StreamOutput,
    StreamStatus,
    build_provider,
)
from AgentX.pydantic_models import FrozenModel

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3


class StreamResult(FrozenModel):
    """Result from a single streaming API call."""

    message: AssistantMessage
    usage: Usage
    stop_reason: str


def _messages_to_openai(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert internal Message types to OpenAI chat format."""
    result: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            result.append({"role": "system", "content": msg.content})
        elif isinstance(msg, UserMessage):
            result.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AssistantMessage):
            entry: dict[str, Any] = {"role": "assistant"}
            if msg.content:
                entry["content"] = msg.content
            if msg.tool_calls:
                entry["tool_calls"] = msg.tool_calls
            if msg.reasoning_content:
                entry["reasoning_content"] = msg.reasoning_content
            result.append(entry)
        elif isinstance(msg, ToolResultMessage):
            result.append({
                "role": "tool",
                "tool_call_id": msg.tool_call_id,
                "content": msg.content,
            })
    return result


def _build_provider_from_config(config: Config) -> LLMProvider:
    """根据 Config 选择并构建对应的 Provider。

    自动推断逻辑:
      1. config.provider 显式指定 → 直接使用
      2. model 名包含 "deepseek" → DeepSeekProvider
      3. base_url 非默认 → CustomProvider
      4. 兜底 → OpenAIProvider
    """
    provider_name = getattr(config, "provider", "") or ""

    if not provider_name:
        model_lower = config.model.lower()
        if "deepseek" in model_lower:
            provider_name = "deepseek"
        elif config.base_url and config.base_url != "https://api.openai.com/v1":
            provider_name = "custom"
        else:
            provider_name = "openai"

    ssl_verify = getattr(config, "ssl_verify", True)

    if provider_name == "openai":
        return build_provider(
            "openai",
            api_key=config.api_key,
            model=config.model,
            base_url=config.base_url if config.base_url != "https://api.openai.com/v1" else None,
            ssl_verify=ssl_verify,
            max_retries=MAX_RETRIES,
        )
    elif provider_name == "deepseek":
        return build_provider(
            "deepseek",
            api_key=config.api_key,
            model=config.model,
            base_url=config.base_url if config.base_url != "https://api.openai.com/v1" else None,
            ssl_verify=ssl_verify,
            max_retries=MAX_RETRIES,
        )
    else:
        # custom — 含 LiteLLM / vLLM / Ollama 等
        return build_provider(
            "custom",
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            ssl_verify=ssl_verify,
            max_retries=MAX_RETRIES,
        )


class LLMClient:
    """LLM 客户端桥接层 — 委托给具体 Provider。

    对外保持原有 stream_chat / single_chat 接口不变，
    内部统一通过 Provider._stream / Provider._invoke 调度。
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._provider = _build_provider_from_config(config)
        self._total_usage = Usage()

    @property
    def provider(self) -> LLMProvider:
        """当前使用的 LLM 供应商实例。"""
        return self._provider

    @property
    def provider_name(self) -> str:
        """当前供应商名称。"""
        return self._provider.name

    @property
    def total_usage(self) -> Usage:
        return self._total_usage

    async def stream_chat(
        self,
        *,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a chat completion. Yields StreamEvents.

        接口保持不变，内部委托给 Provider._stream()。
        """
        openai_messages = [{"role": "system", "content": system_prompt}]
        openai_messages.extend(_messages_to_openai(messages))

        effective_temp = temperature if temperature is not None else self._config.temperature
        effective_max_tokens = max_tokens or self._config.output_tokens or None

        yield StreamEvent(type=StreamEventType.STREAM_START)

        # 累积完整消息
        content_parts: list[str] = []
        reasoning_parts: list[str] = []  # DeepSeek thinking mode passthrough
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        finish_reason = ""
        usage = Usage()

        stream = self._provider.invoke(
            openai_messages,
            tools=tools,
            max_tokens=effective_max_tokens,
            temperature=effective_temp,
            stream=True,
        )

        async for ev in stream:
            ev: StreamOutput

            # Usage
            if ev.usage:
                usage = Usage(
                    input_tokens=ev.usage.get("prompt_tokens", 0),
                    output_tokens=ev.usage.get("completion_tokens", 0),
                )

            # Finish reason
            if ev.finish_reason:
                finish_reason = ev.finish_reason

            # Tool call deltas
            if ev.tool_calls_delta:
                for tc in ev.tool_calls_delta:
                    idx = tc["index"]
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    acc = tool_calls_acc[idx]
                    if tc.get("id"):
                        acc["id"] = tc["id"]
                    if tc.get("function_name"):
                        acc["function"]["name"] = tc["function_name"]
                    if tc.get("function_args"):
                        acc["function"]["arguments"] += tc["function_args"]

            # Content / thinking deltas → yield to caller
            if ev.answer:
                content_parts.append(ev.answer)
                yield StreamEvent(type=StreamEventType.CONTENT_DELTA, data=ev.answer)

            if ev.thinking:
                reasoning_parts.append(ev.thinking)
                yield StreamEvent(type=StreamEventType.THINKING_DELTA, data=ev.thinking)

            if ev.thinking_snapshot:
                yield StreamEvent(type=StreamEventType.THINKING_END, data=ev.thinking_snapshot)

        # Build final tool_calls list
        tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc.keys())]

        # Build assistant message
        content = "".join(content_parts) if content_parts else None
        reasoning_content = "".join(reasoning_parts) if reasoning_parts else None
        assistant_msg = AssistantMessage(
            content=content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
        )

        # Update total usage
        self._total_usage = Usage(
            input_tokens=self._total_usage.input_tokens + usage.input_tokens,
            output_tokens=self._total_usage.output_tokens + usage.output_tokens,
        )

        yield StreamEvent(
            type=StreamEventType.STREAM_END,
            data=StreamResult(
                message=assistant_msg,
                usage=usage,
                stop_reason=finish_reason,
            ),
        )

    async def single_chat(
        self,
        *,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> AssistantMessage:
        """Non-streaming single completion — 委托给 Provider._invoke()。"""
        openai_messages = [{"role": "system", "content": system_prompt}]
        openai_messages.extend(_messages_to_openai(messages))

        msg = await self._provider.invoke(
            openai_messages,
            tools=tools,
            max_tokens=self._config.output_tokens or None,
            temperature=self._config.temperature,
        )

        # Convert ChatCompletionMessage → AssistantMessage
        tool_calls_list: list[dict[str, Any]] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = tc.function.arguments
                # json_repair may have already parsed it to dict
                if isinstance(args, dict):
                    args = json.dumps(args)
                tool_calls_list.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": args,
                    },
                })

        reasoning_content = getattr(msg, 'reasoning_content', None)

        return AssistantMessage(
            content=msg.content,
            tool_calls=tool_calls_list,
            reasoning_content=reasoning_content,
        )
