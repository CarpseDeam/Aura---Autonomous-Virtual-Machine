from __future__ import annotations

"""
Proof-of-concept demo for MCP filesystem server.

Steps:
1) Start filesystem server rooted at current directory
2) Discover tools and print them
3) Write, read, and list files via MCP
4) Stop server

Requires: npm i -g @modelcontextprotocol/server-filesystem or available via npx
Run: python examples/mcp_filesystem_demo.py
"""

import logging
import os
from pathlib import Path

from aura.services.mcp.mcp_client_service import MCPClientService
from aura.services.mcp.mcp_server_configs import build_config

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("demo")


def main() -> None:
    client = MCPClientService()

    root = Path.cwd()
    logger.info("Starting filesystem server...")
    cfg = build_config("filesystem", root=root)
    server_id = client.start_server(cfg)
    info = client.get_info(server_id)
    logger.info("✅ Server started (pid=%s)", info.get("pid"))

    tools = client.list_tools(server_id)
    print("\nAvailable tools:")
    for t in tools:
        print(f"  - {t.name}: {t.description or ''}")

    # Attempt to find tool names with expected semantics
    write_tool = next((t for t in tools if "write" in t.name.lower()), None)
    read_tool = next((t for t in tools if "read" in t.name.lower()), None)
    list_tool = next((t for t in tools if "list" in t.name.lower()), None)

    test_file = "mcp_demo_test.txt"
    content = "Hello from MCP!"

    print("\nWriting test file...")
    if not write_tool:
        raise RuntimeError("No write tool discovered.")
    client.call_tool(server_id, tool_name=write_tool.name, arguments={"path": test_file, "content": content})
    print("✅ File written")

    print("\nReading test file...")
    if not read_tool:
        raise RuntimeError("No read tool discovered.")
    read_res = client.call_tool(server_id, tool_name=read_tool.name, arguments={"path": test_file})
    file_content = read_res.get("content") or read_res.get("result") or read_res
    print(f"✅ File content: {file_content}")

    print("\nListing workspace...")
    if not list_tool:
        raise RuntimeError("No list tool discovered.")
    list_res = client.call_tool(server_id, tool_name=list_tool.name, arguments={"path": "."})
    files = list_res.get("files") or list_res.get("items") or list_res
    print(f"✅ Files: {files}")

    print("\nShutting down...")
    client.stop_server(server_id)
    print("✅ Demo complete!")


if __name__ == "__main__":
    main()

