"""MCP tools — strict translation of ListMcpResourcesTool + ReadMcpResourceTool."""

from __future__ import annotations

from typing import Any

from AgentX.tools.base import BaseTool, ToolParameter
from AgentX.tools.tool_names import LIST_MCP_RESOURCES_TOOL_NAME, READ_MCP_RESOURCE_TOOL_NAME
from AgentX.data_types import ToolResult


class ListMcpResourcesTool(BaseTool):
    """List available MCP server resources."""

    name = LIST_MCP_RESOURCES_TOOL_NAME
    is_read_only = True
    is_concurrency_safe = True
    should_defer = True

    def get_description(self) -> str:
        return "List available resources from MCP servers."

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="server",
                type="string",
                description="Optional server name to filter resources by",
                required=False,
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        server = tool_input.get("server")
        mcp_clients = kwargs.get("mcp_clients", {})

        if not mcp_clients:
            return ToolResult(data="No MCP servers connected")

        lines: list[str] = []
        for name, client in mcp_clients.items():
            if server and name != server:
                continue
            resources = getattr(client, "resources", [])
            lines.append(f"Server: {name}")
            if resources:
                for r in resources:
                    uri = getattr(r, "uri", str(r))
                    desc = getattr(r, "description", "")
                    lines.append(f"  - {uri}" + (f": {desc}" if desc else ""))
            else:
                lines.append("  (no resources)")

        return ToolResult(data="\n".join(lines) if lines else "No matching resources found")


class ReadMcpResourceTool(BaseTool):
    """Read a resource from an MCP server."""

    name = READ_MCP_RESOURCE_TOOL_NAME
    is_read_only = True
    is_concurrency_safe = True
    should_defer = True

    def get_description(self) -> str:
        return "Read a specific resource from an MCP server."

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="server",
                type="string",
                description="The MCP server name",
            ),
            ToolParameter(
                name="uri",
                type="string",
                description="The resource URI to read",
            ),
        ]

    async def execute(self, *, tool_input: dict[str, Any], cwd: str, **kwargs: Any) -> ToolResult:
        server_name = tool_input.get("server", "")
        uri = tool_input.get("uri", "")

        if not server_name or not uri:
            return ToolResult(data="Error: Both 'server' and 'uri' are required")

        mcp_clients = kwargs.get("mcp_clients", {})
        client = mcp_clients.get(server_name)

        if client is None:
            return ToolResult(data=f"Error: MCP server '{server_name}' not found")

        try:
            result = await client.read_resource(uri)
            return ToolResult(data=str(result))
        except Exception as exc:
            return ToolResult(data=f"Error reading resource: {exc}")
