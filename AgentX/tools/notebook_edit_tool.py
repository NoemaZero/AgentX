"""NotebookEdit tool — strict translation of NotebookEditTool."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from AgentX.data_types import (
    NotebookCellType,
    NotebookEditMode,
    ToolParameterType,
    ToolResult,
    coerce_str_enum,
)
from AgentX.tools.base import BaseTool, ToolParameter
from AgentX.tools.tool_names import NOTEBOOK_EDIT_TOOL_NAME


class NotebookEditTool(BaseTool):
    """Replace, insert, or delete cells in a Jupyter notebook."""

    name = NOTEBOOK_EDIT_TOOL_NAME
    should_defer = True
    search_hint = "edit Jupyter notebook cells (.ipynb)"

    def get_description(self) -> str:
        return "Replace the contents of a specific cell in a Jupyter notebook."

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="notebook_path",
                type=ToolParameterType.STRING,
                description="The absolute path to the Jupyter notebook file to edit (must be absolute, not relative)",
            ),
            ToolParameter(
                name="cell_id",
                type=ToolParameterType.STRING,
                description=(
                    "The ID of the cell to edit. When inserting a new cell, "
                    "the new cell will be inserted after the cell with this ID, "
                    "or at the beginning if not specified."
                ),
                required=False,
            ),
            ToolParameter(
                name="new_source",
                type=ToolParameterType.STRING,
                description="The new source for the cell",
            ),
            ToolParameter(
                name="cell_type",
                type=ToolParameterType.STRING,
                description=(
                    "The type of the cell (code or markdown). If not specified, "
                    "it defaults to the current cell type. If using edit_mode=insert, this is required."
                ),
                required=False,
                enum=[cell_type.value for cell_type in NotebookCellType],
            ),
            ToolParameter(
                name="edit_mode",
                type=ToolParameterType.STRING,
                description=(
                    "The type of edit to make (replace, insert, delete). Defaults to replace."
                ),
                required=False,
                enum=[mode.value for mode in NotebookEditMode],
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        notebook_path = tool_input.get("notebook_path", "")
        cell_id = tool_input.get("cell_id")
        new_source = tool_input.get("new_source", "")
        cell_type = coerce_str_enum(
            NotebookCellType,
            tool_input.get("cell_type"),
            default=NotebookCellType.CODE,
        ) if tool_input.get("cell_type") else None
        edit_mode = coerce_str_enum(
            NotebookEditMode,
            tool_input.get("edit_mode"),
            default=NotebookEditMode.REPLACE,
        )

        path = Path(notebook_path)
        if not path.is_absolute():
            path = Path(cwd) / path

        if not path.exists():
            return ToolResult(data=f"Error: Notebook not found: {path}")

        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return ToolResult(data=f"Error reading notebook: {exc}")

        cells: list[dict[str, Any]] = content.get("cells", [])

        if edit_mode == NotebookEditMode.INSERT:
            if not cell_type:
                return ToolResult(data="Error: cell_type is required for insert mode")
            new_cell: dict[str, Any] = {
                "cell_type": cell_type.value,
                "source": new_source.split("\n"),
                "metadata": {},
            }
            if cell_type == NotebookCellType.CODE:
                new_cell["outputs"] = []
                new_cell["execution_count"] = None

            if cell_id:
                idx = _find_cell_index(cells, cell_id)
                if idx is None:
                    return ToolResult(data=f"Error: Cell with id '{cell_id}' not found")
                cells.insert(idx + 1, new_cell)
            else:
                cells.insert(0, new_cell)

        elif edit_mode == NotebookEditMode.DELETE:
            if not cell_id:
                return ToolResult(data="Error: cell_id is required for delete mode")
            idx = _find_cell_index(cells, cell_id)
            if idx is None:
                return ToolResult(data=f"Error: Cell with id '{cell_id}' not found")
            cells.pop(idx)

        else:  # replace
            if not cell_id:
                return ToolResult(data="Error: cell_id is required for replace mode")
            idx = _find_cell_index(cells, cell_id)
            if idx is None:
                return ToolResult(data=f"Error: Cell with id '{cell_id}' not found")
            cells[idx]["source"] = new_source.split("\n")
            if cell_type:
                cells[idx]["cell_type"] = cell_type.value

        content["cells"] = cells
        path.write_text(json.dumps(content, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

        return ToolResult(data=f"Successfully {edit_mode.value}d cell in {path}")


def _find_cell_index(cells: list[dict[str, Any]], cell_id: str) -> int | None:
    """Find a cell by its id metadata or positional index."""
    for i, cell in enumerate(cells):
        cid = cell.get("id") or cell.get("metadata", {}).get("id")
        if cid == cell_id:
            return i
    # Fallback: try interpreting cell_id as an integer index
    try:
        idx = int(cell_id)
        if 0 <= idx < len(cells):
            return idx
    except ValueError:
        pass
    return None
