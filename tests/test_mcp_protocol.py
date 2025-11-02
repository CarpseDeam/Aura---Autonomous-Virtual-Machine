"""
Comprehensive test suite for MCP JSON-RPC 2.0 protocol layer.

Tests all request building, response parsing, validation, and thread safety
for the MCPProtocol class and supporting Pydantic models.
"""

import json
import threading
from datetime import datetime
from typing import List

import pytest

from aura.models.mcp_models import (
    MCPError,
    MCPRequest,
    MCPResponse,
    MCPServerConfig,
    MCPServerInfo,
    MCPTool,
    MCPToolInputSchema,
)
from aura.services.mcp.mcp_protocol import MCPProtocol


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def protocol() -> MCPProtocol:
    """Create a fresh MCPProtocol instance for each test."""
    return MCPProtocol()


@pytest.fixture
def valid_success_response() -> str:
    """Sample valid JSON-RPC 2.0 success response."""
    return json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"status": "ok", "data": [1, 2, 3]}
    })


@pytest.fixture
def valid_error_response() -> str:
    """Sample valid JSON-RPC 2.0 error response."""
    return json.dumps({
        "jsonrpc": "2.0",
        "id": 2,
        "error": {
            "code": -32601,
            "message": "Method not found",
            "data": {"method": "unknown/method"}
        }
    })


# ============================================================================
# REQUEST BUILDING TESTS
# ============================================================================

def test_build_initialize_request(protocol: MCPProtocol) -> None:
    """Verify initialize request has correct structure and method."""
    json_str = protocol.build_initialize_request()

    # Parse JSON
    payload = json.loads(json_str)

    # Verify JSON-RPC structure
    assert payload["jsonrpc"] == "2.0"
    assert "id" in payload
    assert isinstance(payload["id"], int)
    assert payload["id"] > 0

    # Verify method
    assert payload["method"] == "initialize"

    # Verify params
    assert "params" in payload
    assert payload["params"]["protocolVersion"] == "2024-11-05"
    assert payload["params"]["capabilities"] == {}


def test_build_initialize_request_custom_version(protocol: MCPProtocol) -> None:
    """Verify initialize request accepts custom protocol version."""
    json_str = protocol.build_initialize_request(protocol_version="2024-10-01")
    payload = json.loads(json_str)

    assert payload["params"]["protocolVersion"] == "2024-10-01"


def test_build_initialize_request_increments_id(protocol: MCPProtocol) -> None:
    """Verify request IDs increment with each call."""
    json_str1 = protocol.build_initialize_request()
    json_str2 = protocol.build_initialize_request()

    payload1 = json.loads(json_str1)
    payload2 = json.loads(json_str2)

    assert payload2["id"] == payload1["id"] + 1


def test_build_list_tools_request(protocol: MCPProtocol) -> None:
    """Verify list tools request has correct structure and method."""
    json_str = protocol.build_list_tools_request()

    # Parse JSON
    payload = json.loads(json_str)

    # Verify JSON-RPC structure
    assert payload["jsonrpc"] == "2.0"
    assert "id" in payload
    assert isinstance(payload["id"], int)

    # Verify method
    assert payload["method"] == "tools/list"

    # Verify no params or params is not in the JSON (exclude_none)
    assert "params" not in payload or payload.get("params") is None


def test_build_call_tool_request(protocol: MCPProtocol) -> None:
    """Verify call tool request has correct structure and parameters."""
    tool_name = "test_tool"
    arguments = {
        "arg1": "value1",
        "arg2": 42,
        "arg3": {"nested": "data"}
    }

    json_str = protocol.build_call_tool_request(tool_name, arguments)

    # Parse JSON
    payload = json.loads(json_str)

    # Verify JSON-RPC structure
    assert payload["jsonrpc"] == "2.0"
    assert "id" in payload
    assert isinstance(payload["id"], int)

    # Verify method
    assert payload["method"] == "tools/call"

    # Verify params
    assert "params" in payload
    assert payload["params"]["name"] == tool_name
    assert payload["params"]["arguments"] == arguments


def test_build_call_tool_request_complex_arguments(protocol: MCPProtocol) -> None:
    """Verify call tool request handles complex nested arguments."""
    arguments = {
        "list_arg": [1, 2, 3, {"nested": "in_list"}],
        "dict_arg": {
            "level1": {
                "level2": {
                    "level3": "deep_value"
                }
            }
        },
        "mixed": [{"a": 1}, {"b": 2}]
    }

    json_str = protocol.build_call_tool_request("complex_tool", arguments)
    payload = json.loads(json_str)

    assert payload["params"]["arguments"] == arguments


def test_call_tool_request_rejects_empty_tool_name(protocol: MCPProtocol) -> None:
    """Verify call tool request raises ValueError for empty tool name."""
    with pytest.raises(ValueError, match="Tool name cannot be empty"):
        protocol.build_call_tool_request("", {})


def test_call_tool_request_rejects_whitespace_tool_name(protocol: MCPProtocol) -> None:
    """Verify call tool request raises ValueError for whitespace-only tool name."""
    with pytest.raises(ValueError, match="Tool name cannot be empty"):
        protocol.build_call_tool_request("   ", {})


# ============================================================================
# RESPONSE PARSING TESTS
# ============================================================================

def test_parse_response_success(protocol: MCPProtocol, valid_success_response: str) -> None:
    """Verify parsing of valid success response."""
    response = protocol.parse_response(valid_success_response)

    # Verify response structure
    assert response.jsonrpc == "2.0"
    assert response.id == 1
    assert response.result == {"status": "ok", "data": [1, 2, 3]}
    assert response.error is None

    # Verify is_success()
    assert response.is_success() is True


def test_parse_response_error(protocol: MCPProtocol, valid_error_response: str) -> None:
    """Verify parsing of valid error response."""
    response = protocol.parse_response(valid_error_response)

    # Verify response structure
    assert response.jsonrpc == "2.0"
    assert response.id == 2
    assert response.result is None
    assert response.error is not None

    # Verify error structure
    assert response.error.code == -32601
    assert response.error.message == "Method not found"
    assert response.error.data == {"method": "unknown/method"}

    # Verify is_success()
    assert response.is_success() is False


def test_parse_response_malformed_json(protocol: MCPProtocol) -> None:
    """Verify parsing raises ValueError for malformed JSON."""
    malformed_json = '{"jsonrpc": "2.0", "id": 1, "result": '

    with pytest.raises(ValueError, match="Invalid JSON in MCP response"):
        protocol.parse_response(malformed_json)


def test_parse_response_missing_jsonrpc(protocol: MCPProtocol) -> None:
    """Verify parsing raises ValueError when jsonrpc field is missing."""
    missing_jsonrpc = json.dumps({
        "id": 1,
        "result": {"status": "ok"}
    })

    with pytest.raises(ValueError, match="Invalid JSON-RPC 2.0 structure"):
        protocol.parse_response(missing_jsonrpc)


def test_parse_response_invalid_version(protocol: MCPProtocol) -> None:
    """Verify parsing raises ValueError for non-2.0 jsonrpc version."""
    invalid_version = json.dumps({
        "jsonrpc": "1.0",
        "id": 1,
        "result": {"status": "ok"}
    })

    with pytest.raises(ValueError, match="Invalid JSON-RPC 2.0 structure"):
        protocol.parse_response(invalid_version)


def test_parse_response_missing_id(protocol: MCPProtocol) -> None:
    """Verify parsing raises ValueError when id field is missing."""
    missing_id = json.dumps({
        "jsonrpc": "2.0",
        "result": {"status": "ok"}
    })

    with pytest.raises(ValueError, match="Invalid JSON-RPC 2.0 structure"):
        protocol.parse_response(missing_id)


def test_parse_response_missing_result_and_error(protocol: MCPProtocol) -> None:
    """Verify parsing raises ValueError when both result and error are missing."""
    missing_both = json.dumps({
        "jsonrpc": "2.0",
        "id": 1
    })

    with pytest.raises(ValueError, match="Invalid JSON-RPC 2.0 structure"):
        protocol.parse_response(missing_both)


# ============================================================================
# VALIDATION TESTS
# ============================================================================

def test_validate_json_rpc_structure_valid_response(protocol: MCPProtocol) -> None:
    """Verify validation accepts valid JSON-RPC 2.0 structure."""
    valid_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"data": "test"}
    }

    assert protocol.validate_json_rpc_structure(valid_payload) is True


def test_validate_json_rpc_structure_valid_error(protocol: MCPProtocol) -> None:
    """Verify validation accepts valid JSON-RPC 2.0 error structure."""
    valid_error_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32600, "message": "Invalid Request"}
    }

    assert protocol.validate_json_rpc_structure(valid_error_payload) is True


def test_validate_json_rpc_structure_missing_jsonrpc(protocol: MCPProtocol) -> None:
    """Verify validation rejects payload missing jsonrpc field."""
    invalid_payload = {
        "id": 1,
        "result": {"data": "test"}
    }

    assert protocol.validate_json_rpc_structure(invalid_payload) is False


def test_validate_json_rpc_structure_missing_id(protocol: MCPProtocol) -> None:
    """Verify validation rejects payload missing id field."""
    invalid_payload = {
        "jsonrpc": "2.0",
        "result": {"data": "test"}
    }

    assert protocol.validate_json_rpc_structure(invalid_payload) is False


def test_validate_json_rpc_structure_wrong_version(protocol: MCPProtocol) -> None:
    """Verify validation rejects non-2.0 jsonrpc version."""
    invalid_payload = {
        "jsonrpc": "1.0",
        "id": 1,
        "result": {"data": "test"}
    }

    assert protocol.validate_json_rpc_structure(invalid_payload) is False


def test_validate_json_rpc_structure_missing_result_and_error(protocol: MCPProtocol) -> None:
    """Verify validation rejects payload with neither result nor error."""
    invalid_payload = {
        "jsonrpc": "2.0",
        "id": 1
    }

    assert protocol.validate_json_rpc_structure(invalid_payload) is False


# ============================================================================
# THREAD SAFETY TESTS
# ============================================================================

def test_request_id_thread_safety() -> None:
    """
    Verify request ID generation is thread-safe.

    Spawns multiple threads that each generate request IDs concurrently,
    then verifies all IDs are unique and no race conditions occurred.
    """
    protocol = MCPProtocol()
    num_threads = 10
    ids_per_thread = 100

    # Collect IDs from all threads
    all_ids: List[int] = []
    lock = threading.Lock()

    def generate_ids() -> None:
        """Generate IDs in a thread and add to shared list."""
        thread_ids = []
        for _ in range(ids_per_thread):
            thread_ids.append(protocol._next_request_id())

        with lock:
            all_ids.extend(thread_ids)

    # Spawn threads
    threads = []
    for _ in range(num_threads):
        thread = threading.Thread(target=generate_ids)
        threads.append(thread)
        thread.start()

    # Wait for all threads to complete
    for thread in threads:
        thread.join()

    # Verify all IDs are unique
    assert len(all_ids) == num_threads * ids_per_thread
    assert len(set(all_ids)) == len(all_ids), "Duplicate IDs detected"

    # Verify IDs are in expected range
    assert min(all_ids) == 1
    assert max(all_ids) == num_threads * ids_per_thread


# ============================================================================
# PYDANTIC MODEL TESTS
# ============================================================================

def test_mcp_request_model_valid() -> None:
    """Verify MCPRequest model validates correct data."""
    request = MCPRequest(
        id=1,
        method="test/method",
        params={"arg": "value"}
    )

    assert request.jsonrpc == "2.0"
    assert request.id == 1
    assert request.method == "test/method"
    assert request.params == {"arg": "value"}


def test_mcp_request_model_no_params() -> None:
    """Verify MCPRequest model works without params."""
    request = MCPRequest(
        id=1,
        method="test/method"
    )

    assert request.params is None


def test_mcp_error_model_valid() -> None:
    """Verify MCPError model validates correct error data."""
    error = MCPError(
        code=-32600,
        message="Invalid Request",
        data={"details": "missing field"}
    )

    assert error.code == -32600
    assert error.message == "Invalid Request"
    assert error.data == {"details": "missing field"}


def test_mcp_error_constants() -> None:
    """Verify MCPError has standard error code constants."""
    assert MCPError.PARSE_ERROR == -32700
    assert MCPError.INVALID_REQUEST == -32600
    assert MCPError.METHOD_NOT_FOUND == -32601
    assert MCPError.INVALID_PARAMS == -32602
    assert MCPError.INTERNAL_ERROR == -32603


def test_mcp_response_model_valid_success() -> None:
    """Verify MCPResponse model validates success response."""
    response = MCPResponse(
        id=1,
        result={"status": "ok"}
    )

    assert response.jsonrpc == "2.0"
    assert response.id == 1
    assert response.result == {"status": "ok"}
    assert response.error is None
    assert response.is_success() is True


def test_mcp_response_model_valid_error() -> None:
    """Verify MCPResponse model validates error response."""
    error = MCPError(code=-32601, message="Method not found")
    response = MCPResponse(
        id=1,
        error=error
    )

    assert response.jsonrpc == "2.0"
    assert response.id == 1
    assert response.result is None
    assert response.error == error
    assert response.is_success() is False


def test_mcp_tool_input_schema_model_valid() -> None:
    """Verify MCPToolInputSchema model validates correct schema data."""
    schema = MCPToolInputSchema(
        type="object",
        properties={
            "param1": {"type": "string"},
            "param2": {"type": "number"}
        },
        required=["param1"]
    )

    assert schema.type == "object"
    assert schema.properties == {
        "param1": {"type": "string"},
        "param2": {"type": "number"}
    }
    assert schema.required == ["param1"]


def test_mcp_tool_model_valid() -> None:
    """Verify MCPTool model validates complete tool definition."""
    schema = MCPToolInputSchema(
        properties={
            "query": {"type": "string"}
        },
        required=["query"]
    )

    tool = MCPTool(
        name="search_tool",
        description="Search for information",
        inputSchema=schema
    )

    assert tool.name == "search_tool"
    assert tool.description == "Search for information"
    assert tool.inputSchema == schema


def test_mcp_server_config_model_valid() -> None:
    """Verify MCPServerConfig model validates configuration data."""
    config = MCPServerConfig(
        server_id="test_server",
        command=["python", "-m", "test_server"],
        env={"API_KEY": "test123"},
        working_directory="/tmp"
    )

    assert config.server_id == "test_server"
    assert config.command == ["python", "-m", "test_server"]
    assert config.env == {"API_KEY": "test123"}
    assert config.working_directory == "/tmp"


def test_mcp_server_config_model_optional_fields() -> None:
    """Verify MCPServerConfig model works with only required fields."""
    config = MCPServerConfig(
        server_id="test_server",
        command=["python", "-m", "test_server"]
    )

    assert config.server_id == "test_server"
    assert config.command == ["python", "-m", "test_server"]
    assert config.env is None
    assert config.working_directory is None


def test_mcp_server_info_model_valid() -> None:
    """Verify MCPServerInfo model validates runtime server state."""
    config = MCPServerConfig(
        server_id="test_server",
        command=["python", "-m", "test_server"]
    )

    schema = MCPToolInputSchema(
        properties={"arg": {"type": "string"}}
    )
    tool = MCPTool(
        name="test_tool",
        description="Test tool",
        inputSchema=schema
    )

    now = datetime.now()

    info = MCPServerInfo(
        config=config,
        status="ready",
        pid=12345,
        tools=[tool],
        error_message=None,
        started_at=now
    )

    assert info.config == config
    assert info.status == "ready"
    assert info.pid == 12345
    assert len(info.tools) == 1
    assert info.tools[0] == tool
    assert info.error_message is None
    assert info.started_at == now


def test_mcp_server_info_model_default_tools() -> None:
    """Verify MCPServerInfo model defaults tools to empty list."""
    config = MCPServerConfig(
        server_id="test_server",
        command=["python", "-m", "test_server"]
    )

    info = MCPServerInfo(
        config=config,
        status="starting"
    )

    assert info.tools == []
    assert info.pid is None
    assert info.error_message is None
    assert info.started_at is None
