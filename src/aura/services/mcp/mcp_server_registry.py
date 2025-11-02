from __future__ import annotations

"""
MCP Server Registry

Tracks active MCP servers and their runtime state in a thread-safe manner.

Design notes:
- Single responsibility: maintain state only (no subprocess or I/O logic).
- Interface-driven accessors: callers interact via explicit methods.
- Thread safety: a single lock protects all mutations and reads.
"""

import logging
import threading
import time
import uuid
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MCPServerStatus(str, Enum):
    STARTING = "starting"
    READY = "ready"
    ERROR = "error"
    STOPPED = "stopped"


class MCPTool(BaseModel):
    """Discovered MCP tool contract."""

    name: str
    description: Optional[str] = None
    input_schema: Optional[dict] = Field(default=None, alias="inputSchema")


class MCPServerInfo(BaseModel):
    """Runtime metadata for an MCP server instance."""

    server_id: str
    name: str
    project_name: Optional[str] = None
    status: MCPServerStatus
    pid: Optional[int] = None
    started_at: float = Field(default_factory=lambda: time.time())
    error_message: Optional[str] = None
    tools: List[MCPTool] = Field(default_factory=list)


class MCPServerRegistry:
    """Thread-safe registry of active MCP servers and their state."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._servers: Dict[str, MCPServerInfo] = {}

    def create_server_id(self) -> str:
        """Generate a unique server identifier."""
        return str(uuid.uuid4())

    def register(self, *, server_id: str, name: str, project_name: Optional[str] = None) -> MCPServerInfo:
        """Register a new server with initial STARTING status."""
        with self._lock:
            info = MCPServerInfo(server_id=server_id, name=name, project_name=project_name, status=MCPServerStatus.STARTING)
            self._servers[server_id] = info
            logger.info("MCP server registered: %s (status=%s)", server_id, info.status)
            return info

    def set_pid(self, server_id: str, pid: int) -> None:
        with self._lock:
            info = self._require(server_id)
            info.pid = pid
            logger.debug("MCP server %s assigned pid=%s", server_id, pid)

    def set_status(self, server_id: str, status: MCPServerStatus, *, error_message: Optional[str] = None) -> None:
        with self._lock:
            info = self._require(server_id)
            info.status = status
            info.error_message = error_message
            if error_message:
                logger.error("MCP server %s status=%s: %s", server_id, status, error_message)
            else:
                logger.info("MCP server %s status=%s", server_id, status)

    def set_tools(self, server_id: str, tools: List[MCPTool]) -> None:
        with self._lock:
            info = self._require(server_id)
            info.tools = tools
            logger.info("MCP server %s tools discovered: %d", server_id, len(tools))

    def get(self, server_id: str) -> MCPServerInfo:
        with self._lock:
            return self._require(server_id)

    def list_by_status(self, status: MCPServerStatus) -> List[MCPServerInfo]:
        with self._lock:
            return [s for s in self._servers.values() if s.status == status]

    def list_all(self) -> List[MCPServerInfo]:
        with self._lock:
            return list(self._servers.values())

    def list_by_project(self, project_name: str) -> List[MCPServerInfo]:
        with self._lock:
            return [s for s in self._servers.values() if s.project_name == project_name]

    def get_tools(self, server_id: str) -> List[MCPTool]:
        with self._lock:
            info = self._require(server_id)
            return list(info.tools)

    def remove(self, server_id: str) -> None:
        with self._lock:
            if server_id in self._servers:
                del self._servers[server_id]
                logger.info("MCP server removed from registry: %s", server_id)

    def _require(self, server_id: str) -> MCPServerInfo:
        info = self._servers.get(server_id)
        if not info:
            raise KeyError(f"Unknown MCP server: {server_id}")
        return info
