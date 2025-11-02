"""
Pydantic models for Model Context Protocol (MCP) JSON-RPC 2.0 communication.

This module defines all data structures used in MCP protocol interactions with
external tool servers. All models use Pydantic for runtime validation and type safety.
"""

from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field


class MCPRequest(BaseModel):
    """
    Outgoing JSON-RPC 2.0 request to an MCP server.

    Represents a method invocation sent over stdin to a subprocess MCP server.
    The server processes the request and returns an MCPResponse.
    """

    jsonrpc: str = Field(default="2.0", description="JSON-RPC protocol version")
    id: int = Field(..., description="Unique request identifier for matching responses")
    method: str = Field(..., description="RPC method name to invoke")
    params: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Method parameters, if required"
    )


class MCPError(BaseModel):
    """
    JSON-RPC 2.0 error structure.

    Represents an error returned by an MCP server when a request fails.
    Includes standard JSON-RPC error codes and optional additional data.
    """

    # Standard JSON-RPC 2.0 error codes
    PARSE_ERROR: ClassVar[int] = -32700
    INVALID_REQUEST: ClassVar[int] = -32600
    METHOD_NOT_FOUND: ClassVar[int] = -32601
    INVALID_PARAMS: ClassVar[int] = -32602
    INTERNAL_ERROR: ClassVar[int] = -32603

    code: int = Field(..., description="JSON-RPC error code")
    message: str = Field(..., description="Human-readable error message")
    data: Optional[Any] = Field(
        default=None,
        description="Additional error information"
    )


class MCPResponse(BaseModel):
    """
    Incoming JSON-RPC 2.0 response from an MCP server.

    Represents the result of an MCPRequest. Contains either a successful result
    or an error, never both. Use is_success() to check outcome.
    """

    jsonrpc: str = Field(default="2.0", description="JSON-RPC protocol version")
    id: int = Field(..., description="Request ID this response corresponds to")
    result: Optional[Any] = Field(
        default=None,
        description="Successful result data"
    )
    error: Optional[MCPError] = Field(
        default=None,
        description="Error information if request failed"
    )

    def is_success(self) -> bool:
        """
        Check if the response represents a successful request.

        Returns:
            True if the request succeeded (no error), False otherwise.
        """
        return self.error is None


class MCPToolInputSchema(BaseModel):
    """
    JSON Schema definition for tool input validation.

    Describes the structure and validation rules for arguments that can be
    passed to an MCP tool. Follows JSON Schema specification.
    """

    type: str = Field(
        default="object",
        description="JSON Schema type, typically 'object' for tool inputs"
    )
    properties: Dict[str, Any] = Field(
        ...,
        description="Schema properties defining each input parameter"
    )
    required: Optional[List[str]] = Field(
        default=None,
        description="List of required parameter names"
    )


class MCPTool(BaseModel):
    """
    Tool definition received from an MCP server.

    Represents a capability exposed by an MCP server. Each tool has a unique name,
    description, and input schema that defines how to invoke it.
    """

    name: str = Field(..., description="Unique tool identifier")
    description: str = Field(..., description="Human-readable tool description")
    inputSchema: MCPToolInputSchema = Field(
        ...,
        description="JSON Schema defining valid tool arguments"
    )


class MCPServerConfig(BaseModel):
    """
    Configuration for spawning an MCP server subprocess.

    Defines how to start an MCP server, including the command to run,
    environment variables, and working directory.
    """

    server_id: str = Field(
        ...,
        description="Unique identifier for this server instance"
    )
    command: List[str] = Field(
        ...,
        description="Command and arguments to spawn the server process"
    )
    env: Optional[Dict[str, str]] = Field(
        default=None,
        description="Environment variables for the server process"
    )
    working_directory: Optional[str] = Field(
        default=None,
        description="Working directory for the server process"
    )


class MCPServerInfo(BaseModel):
    """
    Runtime state information for an MCP server.

    Tracks the current status, process information, and discovered tools
    for a running MCP server instance.
    """

    config: MCPServerConfig = Field(..., description="Server configuration")
    status: str = Field(
        ...,
        description="Current server status: 'starting', 'ready', 'error', or 'stopped'"
    )
    pid: Optional[int] = Field(
        default=None,
        description="Process ID of the running server"
    )
    tools: List[MCPTool] = Field(
        default_factory=list,
        description="Tools discovered from the server"
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Error message if status is 'error'"
    )
    started_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when the server was started"
    )
