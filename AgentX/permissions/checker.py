"""Permission checker — strict translation of permissions checking logic."""

from __future__ import annotations

import logging
from typing import Any

from AgentX.permissions.modes import get_default_behavior
from AgentX.data_types import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
    PermissionRule,
)

logger = logging.getLogger(__name__)


class PermissionChecker:
    """Check tool permissions against mode, rules, and user settings.

    Translation of permission checking from permissions.ts.
    """

    def __init__(
        self,
        mode: PermissionMode = PermissionMode.DEFAULT,
        allow_rules: list[PermissionRule] | None = None,
        deny_rules: list[PermissionRule] | None = None,
    ) -> None:
        self._mode = mode
        self._allow_rules = allow_rules or []
        self._deny_rules = deny_rules or []
        # Session-granted permissions (tool_name -> set of rule_content patterns)
        self._session_allows: dict[str, set[str]] = {}

    @property
    def mode(self) -> PermissionMode:
        return self._mode

    @mode.setter
    def mode(self, value: PermissionMode) -> None:
        self._mode = value

    def check(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        is_read_only: bool = False,
    ) -> PermissionResult:
        """Check if a tool invocation is allowed.

        Returns PermissionResult with behavior='allow', 'deny', or 'ask'.
        """
        # 1. Check explicit deny rules first
        for rule in self._deny_rules:
            if rule.rule_value.tool_name == tool_name:
                if _matches_rule(rule, tool_input):
                    return PermissionResult(
                        behavior=PermissionBehavior.DENY,
                        message=f"Denied by {rule.source} rule",
                    )

        # 2. Check explicit allow rules
        for rule in self._allow_rules:
            if rule.rule_value.tool_name == tool_name:
                if _matches_rule(rule, tool_input):
                    return PermissionResult(
                        behavior=PermissionBehavior.ALLOW,
                        updated_input=tool_input,
                    )

        # 3. Check session-granted permissions
        if tool_name in self._session_allows:
            return PermissionResult(
                behavior=PermissionBehavior.ALLOW,
                updated_input=tool_input,
            )

        # 4. Fall back to mode-based default
        behavior = get_default_behavior(self._mode, tool_name, is_read_only)
        return PermissionResult(
            behavior=behavior,
            updated_input=tool_input if behavior == PermissionBehavior.ALLOW else None,
        )

    def grant_session_permission(self, tool_name: str, pattern: str = "") -> None:
        """Grant a session-level permission for a tool."""
        if tool_name not in self._session_allows:
            self._session_allows[tool_name] = set()
        self._session_allows[tool_name].add(pattern)

    def revoke_session_permission(self, tool_name: str) -> None:
        """Revoke session-level permission for a tool."""
        self._session_allows.pop(tool_name, None)

    def add_allow_rule(self, rule: PermissionRule) -> None:
        self._allow_rules = [*self._allow_rules, rule]

    def add_deny_rule(self, rule: PermissionRule) -> None:
        self._deny_rules = [*self._deny_rules, rule]


def _matches_rule(rule: PermissionRule, tool_input: dict[str, Any]) -> bool:
    """Check if a permission rule matches the given tool input."""
    content = rule.rule_value.rule_content
    if not content:
        # No content restriction means it matches all invocations of this tool
        return True

    # Simple pattern matching: check if rule_content appears in any input value
    for value in tool_input.values():
        if isinstance(value, str) and content in value:
            return True

    return False
