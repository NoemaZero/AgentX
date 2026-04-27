"""Hooks system — pre/post tool execution hooks."""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# Hook types
HookType = str  # "pre_tool_use" | "post_tool_use" | "stop"

# Hook callback signature
HookCallback = Callable[..., Coroutine[Any, Any, dict[str, Any] | None]]


class HookManager:
    """Manage pre/post tool execution hooks.

    Translation of hooks.ts HookEvent system.
    """

    def __init__(self) -> None:
        self._hooks: dict[str, list[HookCallback]] = {
            "pre_tool_use": [],
            "post_tool_use": [],
            "stop": [],
        }

    def register(self, hook_type: HookType, callback: HookCallback) -> None:
        """Register a hook callback."""
        if hook_type not in self._hooks:
            self._hooks[hook_type] = []
        self._hooks[hook_type].append(callback)

    def unregister(self, hook_type: HookType, callback: HookCallback) -> None:
        """Unregister a hook callback."""
        if hook_type in self._hooks:
            self._hooks[hook_type] = [h for h in self._hooks[hook_type] if h is not callback]

    async def run_pre_tool_use(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> dict[str, Any]:
        """Run pre-tool-use hooks. Returns potentially modified input."""
        result = dict(tool_input)
        for hook in self._hooks.get("pre_tool_use", []):
            try:
                hook_result = await hook(
                    tool_name=tool_name,
                    tool_input=result,
                )
                if isinstance(hook_result, dict):
                    result = hook_result
            except Exception as exc:
                logger.warning("Pre-tool-use hook error: %s", exc)
        return result

    async def run_post_tool_use(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: str,
    ) -> str:
        """Run post-tool-use hooks. Returns potentially modified output."""
        result = tool_output
        for hook in self._hooks.get("post_tool_use", []):
            try:
                hook_result = await hook(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool_output=result,
                )
                if isinstance(hook_result, dict) and "output" in hook_result:
                    result = hook_result["output"]
            except Exception as exc:
                logger.warning("Post-tool-use hook error: %s", exc)
        return result

    async def run_stop(self) -> dict[str, Any] | None:
        """Run stop hooks (session ending).

        Translation of handleStopHooks() from hooks.ts.
        Returns the last hook's return value, or None.
        """
        last_result: dict[str, Any] | None = None
        for hook in self._hooks.get("stop", []):
            try:
                result = await hook()
                if isinstance(result, dict):
                    last_result = result
            except Exception as exc:
                logger.warning("Stop hook error: %s", exc)
        return last_result
