"""memdir — file-based memory system for AgentX.

Provides persistent cross-session memory for user preferences, project context,
feedback, and external system references.

Avoids top-level cross-module re-exports to prevent circular imports.
Import from submodules directly in calling code.
"""
