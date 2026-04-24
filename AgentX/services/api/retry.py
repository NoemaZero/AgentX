"""API retry logic — strict translation of withRetry pattern."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# From query loop: MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3
MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3

# Retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY_MS = 1000
DEFAULT_MAX_DELAY_MS = 30000

# Retryable HTTP status codes
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 529})


class RetryConfig:
    """Configuration for retry behavior."""

    __slots__ = ("max_retries", "base_delay_ms", "max_delay_ms", "retryable_errors")

    def __init__(
        self,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_delay_ms: int = DEFAULT_BASE_DELAY_MS,
        max_delay_ms: int = DEFAULT_MAX_DELAY_MS,
        retryable_errors: frozenset[int] | None = None,
    ) -> None:
        self.max_retries = max_retries
        self.base_delay_ms = base_delay_ms
        self.max_delay_ms = max_delay_ms
        self.retryable_errors = retryable_errors or RETRYABLE_STATUS_CODES


async def with_retry(
    fn: Callable[..., Any],
    config: RetryConfig | None = None,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Execute an async function with exponential backoff retry.

    Translation of withRetry() from the TS codebase.
    """
    cfg = config or RetryConfig()
    last_error: Exception | None = None

    for attempt in range(cfg.max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            last_error = exc

            if not _is_retryable(exc, cfg):
                raise

            if attempt >= cfg.max_retries:
                raise

            delay_ms = _calculate_delay(attempt, cfg)
            logger.warning(
                "Retry attempt %d/%d after %dms: %s",
                attempt + 1,
                cfg.max_retries,
                delay_ms,
                str(exc)[:200],
            )
            await asyncio.sleep(delay_ms / 1000.0)

    # Should not reach here, but just in case
    if last_error:
        raise last_error


def _is_retryable(exc: Exception, config: RetryConfig) -> bool:
    """Check if an exception is retryable."""
    # OpenAI SDK errors with status_code attribute
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        return status_code in config.retryable_errors

    # Network errors
    error_type = type(exc).__name__
    if error_type in ("ConnectionError", "TimeoutError", "ConnectError", "ReadTimeout"):
        return True

    # Check for rate limit in error message
    error_msg = str(exc).lower()
    if "rate limit" in error_msg or "too many requests" in error_msg:
        return True
    if "overloaded" in error_msg or "capacity" in error_msg:
        return True

    return False


def _calculate_delay(attempt: int, config: RetryConfig) -> int:
    """Calculate delay with exponential backoff + jitter."""
    import random

    base = config.base_delay_ms * (2 ** attempt)
    jitter = random.randint(0, config.base_delay_ms)  # noqa: S311
    return min(base + jitter, config.max_delay_ms)
