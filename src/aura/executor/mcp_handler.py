from __future__ import annotations

"""
MCP Executor Handler

Adapts high-level executor actions to MCPClientService calls.

Note: This module does not depend on a specific Action model to preserve
interface isolation. The Executor should pass the expected parameters directly
or via lightweight mapping.
"""

import logging
from typing import Any, Dict, Optional

from aura.services.mcp.mcp_client_service import MCPClientService
from aura.services.mcp.mcp_server_configs import MCPServerConfig, build_config

logger = logging.getLogger(__name__)


class MCPHandler:
    """Translate MCP-related actions into client service calls."""

    def __init__(self, client: MCPClientService) -> None:
        self.client = client

    # ---- Action adapters -----------------------------------------------
    def start_server(self, *, template: str, root: Optional[str] = None, overrides: Optional[dict] = None, project_name: Optional[str] = None) -> Dict[str, Any]:
        config: MCPServerConfig = build_config(template, root=root, overrides=overrides)
        server_id = self.client.start_server(config, project_name=project_name)
        info = self.client.get_info(server_id)
        return {"server_id": server_id, "info": info}

    def stop_server(self, *, server_id: str) -> Dict[str, Any]:
        self.client.stop_server(server_id)
        return {"server_id": server_id, "stopped": True}

    def list_tools(self, *, server_id: str) -> Dict[str, Any]:
        tools = self.client.list_tools(server_id)
        return {"server_id": server_id, "tools": [t.model_dump(by_alias=True) for t in tools]}

    def call_tool(self, *, server_id: str, tool_name: str, arguments: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        result = self.client.call_tool(server_id, tool_name=tool_name, arguments=arguments, timeout=timeout)
        return {"server_id": server_id, "tool": tool_name, "result": result}

    def server_status(self, *, server_id: Optional[str] = None) -> Dict[str, Any]:
        if server_id:
            return {"server_id": server_id, "status": self.client.get_status(server_id)}
        # Summarize all known servers
        infos = self.client.registry.list_all()
        return {"servers": [i.model_dump(by_alias=True) for i in infos]}
