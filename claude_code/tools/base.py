"""Tool base class — strict translation of Tool.ts interface."""

from __future__ import annotations

import abc
from typing import Any

from pydantic import Field, field_validator

from claude_code.data_types import (
    PermissionBehavior,
    PermissionResult,
    ToolParameterType,
    ToolResult,
    ValidationResult,
    coerce_str_enum,
)
from claude_code.pydantic_models import FrozenModel


class ToolParameter(FrozenModel):
    """JSON Schema parameter definition."""

    name: str
    type: ToolParameterType
    description: str
    required: bool = True
    enum: list[str] | None = None
    items: dict[str, Any] | None = None
    default: Any = None

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type(cls, value: ToolParameterType | str | None) -> ToolParameterType:
        return coerce_str_enum(
            ToolParameterType,
            value,
            default=ToolParameterType.STRING,
        )


class BaseTool(abc.ABC):
    """Abstract base for all tools — mirrors Tool.ts interface."""

    # ── Identity ──
    name: str = ""
    aliases: list[str] = []
    search_hint: str | None = None
    user_facing_name_override: str | None = None

    # ── Behavior flags (defaults from buildTool) ──
    is_read_only: bool = False
    is_concurrency_safe: bool = False
    should_defer: bool = False
    strict: bool = False
    always_load: bool = False
    max_result_size_chars: int = 120_000

    @abc.abstractmethod
    def get_description(self) -> str:
        """Return the tool description / prompt text."""

    @abc.abstractmethod
    def get_parameters(self) -> list[ToolParameter]:
        """Return parameter definitions."""

    @abc.abstractmethod
    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        """Execute the tool with the given input. Returns ToolResult."""

    # ── Optional overrides ──

    def is_enabled(self) -> bool:
        return True

    def check_is_read_only(self, tool_input: dict[str, Any]) -> bool:
        return self.is_read_only

    def check_is_concurrency_safe(self, tool_input: dict[str, Any]) -> bool:
        return self.is_concurrency_safe

    async def validate_input(self, tool_input: dict[str, Any]) -> ValidationResult:
        return ValidationResult(result=True)

    async def check_permissions(self, tool_input: dict[str, Any]) -> PermissionResult:
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input=tool_input,
        )

    def get_user_facing_name(self, tool_input: dict[str, Any] | None = None) -> str:
        return self.user_facing_name_override or self.name

    # ── OpenAI function calling schema ──

    def to_openai_tool(self) -> dict[str, Any]:
        """Convert to OpenAI function-calling tool definition."""
        properties: dict[str, Any] = {}
        required: list[str] = []

        for param in self.get_parameters():
            prop: dict[str, Any] = {
                "type": param.type.value,
                "description": param.description,
            }
            if param.enum is not None:
                prop["enum"] = param.enum
            if param.items is not None:
                prop["items"] = param.items
            if param.default is not None:
                prop["default"] = param.default
            properties[param.name] = prop
            if param.required:
                required.append(param.name)

        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.get_description(),
                "parameters": schema,
            },
        }
