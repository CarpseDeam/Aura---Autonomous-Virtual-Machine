from __future__ import annotations

import asyncio
import json
import socket
import sys
import time
from typing import Callable

import websockets

from src.aura.services.terminal_bridge import TerminalBridge


def _allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_for(predicate: Callable[[], bool], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise TimeoutError("Predicate did not become true in time")


async def _exercise_terminal_bridge() -> None:
    host = "127.0.0.1"
    port = _allocate_port()
    bridge = TerminalBridge(host=host, port=port)
    bridge.start()
    try:
        await _wait_for(lambda: bridge._server is not None, timeout=5)
        uri = f"ws://{host}:{port}"
        async with websockets.connect(uri) as websocket:
            command_token = "aura-integration-check"
            if sys.platform.startswith("win"):
                command_text = f"Write-Output '{command_token}'"
                terminator = "\r\n"
            else:
                command_text = f"echo {command_token}"
                terminator = "\n"
            await asyncio.sleep(0.2)
            await websocket.send(json.dumps({"type": "input", "data": f"{command_text}{terminator}"}))
            output = ""
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and command_token not in output:
                chunk = await asyncio.wait_for(websocket.recv(), timeout=2)
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8", errors="replace")
                output += chunk
            assert command_token in output
    finally:
        bridge.stop()


def test_terminal_bridge_executes_command() -> None:
    asyncio.run(_exercise_terminal_bridge())
