"""Shared Pydantic model bases for AgentX."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class FrozenModel(BaseModel):
    """Immutable-ish validated model.

    Note: container fields like ``list`` / ``dict`` remain shallowly mutable,
    matching prior dataclass behavior in this codebase.
    """

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        extra="forbid",
        use_enum_values=False,
    )


class MutableModel(BaseModel):
    """Validated mutable model for runtime state containers."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=False,
        revalidate_instances="never",
        use_enum_values=False,
    )


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    """Dump a model as standard Python data."""
    return model.model_dump(mode="python")
