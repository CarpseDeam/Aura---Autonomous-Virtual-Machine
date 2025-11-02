from __future__ import annotations

"""
MCP Client Service

Spawns, initializes, communicates with, and manages the lifecycle of MCP servers
running as subprocesses over stdin/stdout using JSON-RPC 2.0 messages.

Responsibilities:
- Start/stop server processes
- Send requests and await responses with timeouts
- Discover tools and track readiness
- Provide thread-safe, multi-server management

This mirrors the subprocess and background I/O patterns used by terminal agents.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from subprocess import PIPE, Popen
from typing import Any, Dict, Optional

from .mcp_server_registry import MCPServerRegistry, MCPServerStatus, MCPTool
from .mcp_server_configs import MCPServerConfig

logger = logging.getLogger(__name__)


@dataclass
class _PendingRequest:
    event: threading.Event
    result: Optional[dict] = None
    error: Optional[dict] = None


class _ServerContext:
    """Holds runtime objects for a single spawned MCP server process."""

    def __init__(self, *, process: Popen[str]):
        self.process = process
        self.stdout_thread: Optional[threading.Thread] = None
        self.stderr_thread: Optional[threading.Thread] = None
        self.lock = threading.RLock()
        self.next_id = 1
        self.pending: Dict[int, _PendingRequest] = {}
        self.alive = True

    def next_request_id(self) -> int:
        with self.lock:
            rid = self.next_id
            self.next_id += 1
            return rid


class MCPClientService:
    """Client service for managing multiple MCP servers and JSON-RPC I/O."""

    def __init__(self, registry: Optional[MCPServerRegistry] = None) -> None:
        self.registry = registry or MCPServerRegistry()
        self._servers: Dict[str, _ServerContext] = {}
        self._lock = threading.RLock()

    # ---- Public API -----------------------------------------------------
    def start_server(self, config: MCPServerConfig) -> str:
        """Start an MCP server and perform initialization + tool discovery.

        Returns:
            server_id: Unique identifier for the started server.

        Raises:
            RuntimeError: if the process fails to spawn
            TimeoutError: if initialization or discovery times out
        """
        server_id = self.registry.create_server_id()
        self.registry.register(server_id=server_id, name=config.name)

        env = {**config.resolved_env()} if config.env else None
        cwd: Optional[str] = str(config.cwd) if isinstance(config.cwd, Path) else (config.cwd or None)

        try:
            # Text mode for line-by-line JSON messages
            proc = Popen(
                config.command,
                stdin=PIPE,
                stdout=PIPE,
                stderr=PIPE,
                text=True,
                bufsize=1,
                cwd=cwd,
                env=env,
            )
        except Exception as exc:  # noqa: BLE001
            self.registry.set_status(server_id, MCPServerStatus.ERROR, error_message=str(exc))
            raise RuntimeError(f"Failed to spawn MCP server '{config.name}': {exc}") from exc

        ctx = _ServerContext(process=proc)
        with self._lock:
            self._servers[server_id] = ctx

        self.registry.set_pid(server_id, proc.pid or 0)
        logger.info("MCP server started for %s (pid=%s)", server_id, proc.pid)

        # Start background readers
        self._start_reader_threads(server_id, ctx)

        # Initialize
        try:
            init_result = self._request(server_id, ctx, method="initialize", params={"protocolVersion": "1.0"}, timeout=config.init_timeout_seconds)
            logger.debug("MCP initialize result for %s: %s", server_id, init_result)
        except TimeoutError as exc:
            self._terminate_process(server_id, ctx)
            self.registry.set_status(server_id, MCPServerStatus.ERROR, error_message="Initialization timeout")
            raise

        # Discover tools
        try:
            tools_result = self._request(server_id, ctx, method="tools/list", params={}, timeout=config.request_timeout_seconds)
        except TimeoutError:
            self._terminate_process(server_id, ctx)
            self.registry.set_status(server_id, MCPServerStatus.ERROR, error_message="Tool discovery timeout")
            raise

        tools = self._parse_tools(tools_result)
        self.registry.set_tools(server_id, tools)
        self.registry.set_status(server_id, MCPServerStatus.READY)

        return server_id

    def stop_server(self, server_id: str, *, timeout: float = 5.0) -> None:
        ctx = self._require_ctx(server_id)

        # Best-effort graceful shutdown
        try:
            self._request(server_id, ctx, method="shutdown", params={}, timeout=timeout)
        except Exception:  # noqa: BLE001
            pass

        self._terminate_process(server_id, ctx)
        self.registry.set_status(server_id, MCPServerStatus.STOPPED)
        self.registry.remove(server_id)
        with self._lock:
            self._servers.pop(server_id, None)

    def list_tools(self, server_id: str) -> list[MCPTool]:
        return self.registry.get_tools(server_id)

    def call_tool(self, server_id: str, *, tool_name: str, arguments: dict, timeout: Optional[float] = None) -> dict:
        ctx = self._require_ctx(server_id)
        info = self.registry.get(server_id)
        if info.status != MCPServerStatus.READY:
            raise RuntimeError(f"Server {server_id} is not ready (status={info.status})")

        payload = {"name": tool_name, "arguments": arguments}
        result = self._request(server_id, ctx, method="tools/call", params=payload, timeout=timeout or 20.0)
        if "error" in result:
            err = result["error"]
            raise RuntimeError(f"Tool call failed for {tool_name}: {err}")
        return result.get("result", result)

    def get_status(self, server_id: str) -> MCPServerStatus:
        return self.registry.get(server_id).status

    def get_info(self, server_id: str) -> dict:
        info = self.registry.get(server_id)
        return info.model_dump(by_alias=True)

    def shutdown_all(self) -> None:
        with self._lock:
            ids = list(self._servers.keys())
        for server_id in ids:
            try:
                self.stop_server(server_id)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to stop MCP server %s: %s", server_id, exc, exc_info=True)

    # ---- Internal helpers ----------------------------------------------
    def _start_reader_threads(self, server_id: str, ctx: _ServerContext) -> None:
        assert ctx.process.stdout is not None
        assert ctx.process.stderr is not None

        def _stdout_reader() -> None:
            logger.debug("stdout reader started for %s", server_id)
            try:
                for line in ctx.process.stdout:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.error("Malformed JSON from %s: %r", server_id, raw)
                        continue
                    self._handle_incoming(server_id, ctx, msg)
            except Exception as exc:  # noqa: BLE001
                logger.error("stdout reader failed for %s: %s", server_id, exc, exc_info=True)
            finally:
                logger.info("stdout reader exiting for %s", server_id)
                ctx.alive = False

        def _stderr_reader() -> None:
            logger.debug("stderr reader started for %s", server_id)
            try:
                for line in ctx.process.stderr:  # type: ignore[assignment]
                    text = line.rstrip("\n")
                    if text:
                        logger.debug("[%s|stderr] %s", server_id, text)
            except Exception as exc:  # noqa: BLE001
                logger.error("stderr reader failed for %s: %s", server_id, exc, exc_info=True)
            finally:
                logger.info("stderr reader exiting for %s", server_id)

        t_out = threading.Thread(target=_stdout_reader, name=f"mcp-{server_id}-stdout", daemon=True)
        t_err = threading.Thread(target=_stderr_reader, name=f"mcp-{server_id}-stderr", daemon=True)
        ctx.stdout_thread = t_out
        ctx.stderr_thread = t_err
        t_out.start()
        t_err.start()

    def _request(self, server_id: str, ctx: _ServerContext, *, method: str, params: dict, timeout: float) -> dict:
        request_id = ctx.next_request_id()
        pending = _PendingRequest(event=threading.Event())
        with ctx.lock:
            ctx.pending[request_id] = pending

        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        data = json.dumps(message)

        try:
            assert ctx.process.stdin is not None
            ctx.process.stdin.write(data + "\n")
            ctx.process.stdin.flush()
            logger.debug("[%s] -> %s", server_id, data)
        except BrokenPipeError as exc:
            self.registry.set_status(server_id, MCPServerStatus.ERROR, error_message="Broken pipe to server")
            raise RuntimeError("Broken pipe writing to MCP server") from exc

        # Wait for response
        if not pending.event.wait(timeout):
            # Clean up stale pending to avoid leaks
            with ctx.lock:
                ctx.pending.pop(request_id, None)
            raise TimeoutError(f"Request timeout for method {method}")

        if pending.error is not None:
            return {"error": pending.error}
        return pending.result or {}

    def _handle_incoming(self, server_id: str, ctx: _ServerContext, msg: dict) -> None:
        logger.debug("[%s] <- %s", server_id, json.dumps(msg))
        if "id" in msg:
            # Response to a prior request
            req_id = msg.get("id")
            with ctx.lock:
                pending = ctx.pending.pop(int(req_id), None)
            if not pending:
                logger.debug("No pending request for id=%s on %s", req_id, server_id)
                return
            if "error" in msg and msg["error"] is not None:
                pending.error = msg["error"]
            else:
                pending.result = msg
            pending.event.set()
            return

        # Notification or server event; log for diagnostics
        method = msg.get("method")
        if method:
            logger.debug("Notification from %s: %s", server_id, method)

    def _parse_tools(self, msg: dict) -> list[MCPTool]:
        tools: list[MCPTool] = []
        try:
            raw_tools = msg.get("result", {}).get("tools", []) if "result" in msg else msg.get("tools", [])
            for t in raw_tools:
                # Flexible parsing based on server response shape
                name = t.get("name") if isinstance(t, dict) else None
                description = t.get("description") if isinstance(t, dict) else None
                schema = t.get("inputSchema") if isinstance(t, dict) else None
                if name:
                    tools.append(MCPTool(name=name, description=description, inputSchema=schema))
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to parse tools: %s", exc, exc_info=True)
        return tools

    def _terminate_process(self, server_id: str, ctx: _ServerContext) -> None:
        try:
            if ctx.process.poll() is None:
                ctx.process.terminate()
                try:
                    ctx.process.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    ctx.process.kill()
        finally:
            try:
                if ctx.process.stdin:
                    ctx.process.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                if ctx.process.stdout:
                    ctx.process.stdout.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                if ctx.process.stderr:
                    ctx.process.stderr.close()
            except Exception:  # noqa: BLE001
                pass

    def _require_ctx(self, server_id: str) -> _ServerContext:
        with self._lock:
            ctx = self._servers.get(server_id)
        if not ctx:
            raise KeyError(f"Unknown MCP server: {server_id}")
        return ctx

