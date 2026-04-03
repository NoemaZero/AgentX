"""Config tool — strict translation of ConfigTool."""

from __future__ import annotations

from typing import Any

from claude_code.data_types import ConfigAction, ToolParameterType, ToolResult, coerce_str_enum
from claude_code.pydantic_models import model_to_dict
from claude_code.tools.base import BaseTool, ToolParameter
from claude_code.tools.tool_names import CONFIG_TOOL_NAME


class ConfigTool(BaseTool):
    """Read or update configuration settings."""

    name = CONFIG_TOOL_NAME
    is_read_only = True
    is_concurrency_safe = True

    def get_description(self) -> str:
        return "Read or update Claude Code configuration settings."

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="action",
                type=ToolParameterType.STRING,
                description="Action to perform: 'get' to read config, 'set' to update",
                enum=[action.value for action in ConfigAction],
            ),
            ToolParameter(
                name="key",
                type=ToolParameterType.STRING,
                description="The configuration key to get or set",
                required=False,
            ),
            ToolParameter(
                name="value",
                type=ToolParameterType.STRING,
                description="The value to set (required for 'set' action)",
                required=False,
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        action = coerce_str_enum(
            ConfigAction,
            tool_input.get("action"),
            default=ConfigAction.GET,
        )
        key = tool_input.get("key")

        config = kwargs.get("config")
        if config is None:
            return ToolResult(data="Error: Configuration not available")

        if action == ConfigAction.GET:
            if key:
                val = getattr(config, key, None)
                if val is None:
                    return ToolResult(data=f"Unknown config key: {key}")
                return ToolResult(data=f"{key} = {val}")
            # Return all config
            items = model_to_dict(config)
            # Redact sensitive values
            if "api_key" in items:
                items["api_key"] = "***" if items["api_key"] else "(not set)"
            lines = [f"{k} = {v}" for k, v in sorted(items.items())]
            return ToolResult(data="\n".join(lines))

        # set action
        if not key:
            return ToolResult(data="Error: 'key' is required for set action")
        value = tool_input.get("value", "")
        return ToolResult(data=f"Config update requested: {key} = {value} (runtime config changes not persisted)")
