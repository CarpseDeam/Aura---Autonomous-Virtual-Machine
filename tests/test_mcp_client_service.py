from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from aura.services.mcp.mcp_client_service import MCPClientService
from aura.services.mcp.mcp_server_configs import MCPServerConfig, build_config


def _has_npx() -> bool:
    if shutil.which("npx") is None:
        return False
    # verify npx is runnable
    import subprocess

    try:
        proc = subprocess.run(["npx", "--version"], capture_output=True, text=True, timeout=5)
        return proc.returncode == 0
    except Exception:
        return False


@pytest.mark.integration
@pytest.mark.skipif(not _has_npx(), reason="npx is required for MCP server tests")
def test_server_lifecycle(tmp_path: Path) -> None:
    client = MCPClientService()
    cfg = build_config("filesystem", root=tmp_path)
    server_id = client.start_server(cfg)
    assert client.get_status(server_id) == "ready"

    client.stop_server(server_id)
    # best-effort stop; ensure it no longer listed in registry
    with pytest.raises(KeyError):
        client.get_info(server_id)


@pytest.mark.integration
@pytest.mark.skipif(not _has_npx(), reason="npx is required for MCP server tests")
def test_tool_discovery(tmp_path: Path) -> None:
    client = MCPClientService()
    cfg = build_config("filesystem", root=tmp_path)
    server_id = client.start_server(cfg)
    tools = client.list_tools(server_id)
    assert isinstance(tools, list)
    assert any(t.name for t in tools)
    client.stop_server(server_id)


@pytest.mark.integration
@pytest.mark.skipif(not _has_npx(), reason="npx is required for MCP server tests")
def test_tool_invocation_read_write_list(tmp_path: Path) -> None:
    client = MCPClientService()
    cfg = build_config("filesystem", root=tmp_path)
    server_id = client.start_server(cfg)

    tools = client.list_tools(server_id)
    write_tool = next((t for t in tools if "write" in t.name.lower()), None)
    read_tool = next((t for t in tools if "read" in t.name.lower()), None)
    list_tool = next((t for t in tools if "list" in t.name.lower()), None)

    assert write_tool and read_tool and list_tool

    test_file = "test.txt"
    content = "Hello from MCP!"
    client.call_tool(server_id, tool_name=write_tool.name, arguments={"path": test_file, "content": content}, timeout=15)
    res = client.call_tool(server_id, tool_name=read_tool.name, arguments={"path": test_file}, timeout=15)
    assert content in str(res)

    listed = client.call_tool(server_id, tool_name=list_tool.name, arguments={"path": "."}, timeout=15)
    assert "test" in str(listed).lower()

    client.stop_server(server_id)


def test_invalid_command_fails() -> None:
    client = MCPClientService()
    cfg = MCPServerConfig(name="invalid", command=["definitely-not-a-real-binary-xyz"])
    with pytest.raises(RuntimeError):
        client.start_server(cfg)


def test_initialization_timeout() -> None:
    client = MCPClientService()
    # Spawn a long-running dummy process that never speaks JSON-RPC
    cfg = MCPServerConfig(
        name="dummy",
        command=[sys.executable, "-c", "import time; time.sleep(60)"],
        init_timeout_seconds=0.5,
    )
    with pytest.raises(TimeoutError):
        client.start_server(cfg)
