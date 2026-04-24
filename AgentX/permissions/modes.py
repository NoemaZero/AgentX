"""Permission modes — strict translation of types/permissions.ts."""

from __future__ import annotations

from AgentX.data_types import PermissionBehavior, PermissionMode


def get_default_behavior(mode: PermissionMode, tool_name: str, is_read_only: bool) -> PermissionBehavior:
    """Determine default permission behavior for a tool invocation.

    Translation of permission mode logic from permissions.ts.
    """
    if mode == PermissionMode.BYPASS_PERMISSIONS:
        return PermissionBehavior.ALLOW

    if mode == PermissionMode.ACCEPT_EDITS:
        # Allow read-only tools and file edits
        if is_read_only:
            return PermissionBehavior.ALLOW
        if tool_name in ("Edit", "Write"):
            return PermissionBehavior.ALLOW
        return PermissionBehavior.ASK

    if mode == PermissionMode.PLAN:
        # Only allow read-only tools
        return PermissionBehavior.ALLOW if is_read_only else PermissionBehavior.DENY

    if mode == PermissionMode.AUTO:
        return PermissionBehavior.ALLOW

    # Default mode: allow read-only, ask for everything else
    if is_read_only:
        return PermissionBehavior.ALLOW

    return PermissionBehavior.ASK
