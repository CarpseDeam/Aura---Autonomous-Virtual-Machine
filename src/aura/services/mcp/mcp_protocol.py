"""
JSON-RPC 2.0 protocol handler for Model Context Protocol (MCP) communication.

This module provides the core protocol layer for building requests and parsing
responses when communicating with MCP servers over stdin/stdout.
"""

import json
import logging
import threading
from typing import Any, Dict

from aura.models.mcp_models import MCPError, MCPRequest, MCPResponse

logger = logging.getLogger(__name__)


class MCPProtocol:
    """
    Handler for MCP JSON-RPC 2.0 protocol operations.

    Responsible for:
    - Building valid JSON-RPC 2.0 request messages
    - Parsing JSON-RPC 2.0 response messages
    - Validating message structure
    - Thread-safe request ID generation
    """

    def __init__(self) -> None:
        """Initialize the protocol handler with request ID counter."""
        self._request_id_counter: int = 0
        self._request_id_lock: threading.Lock = threading.Lock()
        logger.debug("MCPProtocol initialized")

    def build_initialize_request(self, protocol_version: str = "2024-11-05") -> str:
        """
        Build a JSON-RPC initialization request.

        This is typically the first request sent to an MCP server to establish
        the protocol version and exchange capabilities.

        Args:
            protocol_version: MCP protocol version string (default: "2024-11-05")

        Returns:
            JSON string ready to send to server stdin
        """
        request = MCPRequest(
            id=self._next_request_id(),
            method="initialize",
            params={
                "protocolVersion": protocol_version,
                "capabilities": {}
            }
        )
        json_str = json.dumps(request.model_dump(), separators=(',', ':'))
        logger.debug(f"Built initialize request: {json_str}")
        return json_str

    def build_list_tools_request(self) -> str:
        """
        Build a JSON-RPC request to list available tools.

        Queries the MCP server for all tools it can execute. The server responds
        with an array of MCPTool objects.

        Returns:
            JSON string ready to send to server stdin
        """
        request = MCPRequest(
            id=self._next_request_id(),
            method="tools/list",
            params=None
        )
        json_str = json.dumps(request.model_dump(exclude_none=True), separators=(',', ':'))
        logger.debug(f"Built list tools request: {json_str}")
        return json_str

    def build_call_tool_request(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """
        Build a JSON-RPC request to invoke a tool.

        Args:
            tool_name: Name of the tool to invoke (must be non-empty)
            arguments: Tool arguments as a dictionary

        Returns:
            JSON string ready to send to server stdin

        Raises:
            ValueError: If tool_name is empty
        """
        if not tool_name or not tool_name.strip():
            raise ValueError("Tool name cannot be empty")

        request = MCPRequest(
            id=self._next_request_id(),
            method="tools/call",
            params={
                "name": tool_name,
                "arguments": arguments
            }
        )
        json_str = json.dumps(request.model_dump(), separators=(',', ':'))
        logger.debug(f"Built call tool request: {json_str}")
        return json_str

    def parse_response(self, json_str: str) -> MCPResponse:
        """
        Parse a JSON-RPC response from an MCP server.

        Validates the JSON structure and ensures it conforms to JSON-RPC 2.0
        specification. Handles both success and error responses.

        Args:
            json_str: Raw JSON string from server stdout

        Returns:
            Parsed and validated MCPResponse instance

        Raises:
            ValueError: If JSON is malformed or doesn't conform to JSON-RPC 2.0
        """
        try:
            payload = json.loads(json_str)
        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON in MCP response: {str(e)}"
            logger.error(error_msg)
            raise ValueError(error_msg) from e

        # Validate JSON-RPC structure
        if not self.validate_json_rpc_structure(payload):
            error_msg = f"Invalid JSON-RPC 2.0 structure in response: {json_str}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        try:
            response = MCPResponse(**payload)
            logger.debug(f"Parsed response: id={response.id}, success={response.is_success()}")
            return response
        except Exception as e:
            error_msg = f"Failed to parse response into MCPResponse model: {str(e)}"
            logger.error(error_msg)
            raise ValueError(error_msg) from e

    def validate_json_rpc_structure(self, payload: Dict[str, Any]) -> bool:
        """
        Validate that a payload conforms to JSON-RPC 2.0 structure.

        Checks for:
        - Required "jsonrpc" field with value "2.0"
        - Required "id" field
        - At least one of "result" or "error" present (for responses)

        Args:
            payload: Dictionary parsed from JSON

        Returns:
            True if structure is valid, False otherwise
        """
        # Check required fields
        if "jsonrpc" not in payload:
            logger.debug("Missing 'jsonrpc' field")
            return False

        if "id" not in payload:
            logger.debug("Missing 'id' field")
            return False

        # Verify JSON-RPC version
        if payload["jsonrpc"] != "2.0":
            logger.debug(f"Invalid jsonrpc version: {payload['jsonrpc']}")
            return False

        # For responses, either result or error must be present
        has_result = "result" in payload
        has_error = "error" in payload

        if not has_result and not has_error:
            logger.debug("Response missing both 'result' and 'error' fields")
            return False

        return True

    def _next_request_id(self) -> int:
        """
        Generate the next request ID in a thread-safe manner.

        Request IDs are unique integers that increment sequentially.
        Thread-safe for concurrent request generation.

        Returns:
            Next unique request ID
        """
        with self._request_id_lock:
            self._request_id_counter += 1
            request_id = self._request_id_counter
            logger.debug(f"Generated request ID: {request_id}")
            return request_id
