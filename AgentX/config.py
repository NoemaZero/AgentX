"""Configuration — translation of entrypoints/init.ts + QueryEngineConfig."""

from __future__ import annotations

import os

from AgentX.data_types import PermissionMode, ProviderType, coerce_str_enum
from AgentX.pydantic_models import FrozenModel


# Model context windows (aligned with original)
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o3-mini": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "deepseek-chat": 64_000,
    "deepseek-reasoner": 64_000,
    "tencent/hy3-preview:free": 262100
}

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_PROVIDER = ProviderType.DEEPSEEK
DEFAULT_MAX_TOKENS = 16_384
DEFAULT_MAX_TURNS = 100


class Config(FrozenModel):
    """Application configuration — immutable."""

    model: str = DEFAULT_MODEL
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    provider: ProviderType = DEFAULT_PROVIDER
    ssl_verify: bool = True  # SSL 证书验证，所有供应商通用
    output_tokens: int = DEFAULT_MAX_TOKENS
    max_turns: int = DEFAULT_MAX_TURNS
    context_tokens: int | None = None  # 上下文窗口大小，None 表示使用模型默认值
    temperature: float = 0.0
    cwd: str = ""
    verbose: bool = False
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    system_prompt: str | None = None
    append_system_prompt: str | None = None
    max_budget_usd: float | None = None
    non_interactive: bool = False
    fallback_model: str | None = None  # Fallback model if primary fails (translation of fallbackModel)

    # Feature flags (translation of feature() system in TypeScript)
    # These control optional features and can be toggled via environment variables
    enable_streaming_tool_executor: bool = False  # Enable StreamingToolExecutor
    enable_context_collapse: bool = False  # Enable context collapse
    enable_microcompact: bool = False  # Enable microcompact
    enable_snip_compaction: bool = False  # Enable snip compaction
    enable_token_budget: bool = False  # Enable token budget tracking
    enable_task_budget: bool = False  # Enable task budget tracking
    enable_tool_use_summary: bool = False  # Enable Haiku tool use summary
    enable_image_validation: bool = True  # Enable image validation
    enable_tombstone_messages: bool = False  # Enable tombstone messages for orphan cleanup
    enable_content_replacement: bool = False  # Enable content replacement storage


def load_config(
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    provider: str | None = None,
    ssl_verify: bool | None = None,
    output_tokens: int | None = None,
    max_turns: int | None = None,
    context_tokens: int | None = None,
    cwd: str | None = None,
    verbose: bool = False,
    permission_mode: PermissionMode | str = PermissionMode.DEFAULT,
    system_prompt: str | None = None,
    append_system_prompt: str | None = None,
    max_budget_usd: float | None = None,
    non_interactive: bool = False,
    fallback_model: str | None = None,
) -> Config:
    """Build config from env vars + explicit overrides."""
    resolved_api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    resolved_base_url = base_url or os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)
    resolved_model = model or os.environ.get("DEEPSEEK_CODE_MODEL", DEFAULT_MODEL)
    resolved_provider = coerce_str_enum(
        ProviderType,
        provider or os.environ.get("PROVIDER", ""),
        default=DEFAULT_PROVIDER,
    )
    if ssl_verify is None:
        env_val = os.environ.get("DEEPSEEK_SSL_VERIFY", "true")
        resolved_ssl_verify = env_val.lower() not in ("false", "0", "no")
    else:
        resolved_ssl_verify = ssl_verify
    resolved_cwd = cwd or os.getcwd()
    resolved_permission_mode = coerce_str_enum(
        PermissionMode,
        permission_mode,
        default=PermissionMode.DEFAULT,
    )

    return Config(
        model=resolved_model,
        api_key=resolved_api_key,
        base_url=resolved_base_url,
        provider=resolved_provider,
        ssl_verify=resolved_ssl_verify,
        output_tokens=output_tokens or DEFAULT_MAX_TOKENS,
        max_turns=max_turns or DEFAULT_MAX_TURNS,
        context_tokens=context_tokens,
        cwd=resolved_cwd,
        verbose=verbose,
        permission_mode=resolved_permission_mode,
        system_prompt=system_prompt,
        append_system_prompt=append_system_prompt,
        max_budget_usd=max_budget_usd,
        non_interactive=non_interactive,
        fallback_model=fallback_model,
    )
