"""OpenAI 官方 API 供应商。

适用于:
  - gpt-4o / gpt-4o-mini  — 通用对话模型
  - o1 / o3-mini           — 推理模型

环境变量:
  OPENAI_API_KEY   API 密钥
  OPENAI_MODEL     模型名称（默认 gpt-4o）
"""

from __future__ import annotations

import os
from typing import Any, AsyncGenerator

from openai import AsyncOpenAI

from .base import LLMProvider
from .types import Messages, StreamOutput, StreamStatus

_DEFAULT_MODEL = "gpt-4o"


class OpenAIProvider(LLMProvider):
    """使用官方 OpenAI API 的供应商。"""

    name: str = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        ssl_verify: bool = True,
        max_retries: int = 3,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._model = model or os.environ.get("OPENAI_MODEL", _DEFAULT_MODEL)
        self._base_url = base_url  # None → 使用 openai SDK 默认值
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
                "max_retries": self._max_retries,
            }
            if self._base_url:
                kwargs["base_url"] = self._base_url
            if not self._ssl_verify:
                kwargs["http_client"] = self._build_http_client(ssl_verify=False)
            self._client = AsyncOpenAI(**kwargs)
        return self._client
