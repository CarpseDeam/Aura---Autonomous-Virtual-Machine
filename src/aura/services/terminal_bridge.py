from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, TextIO

import websockets
from websockets.server import WebSocketServerProtocol

from src.aura.models.event_types import TERMINAL_OUTPUT_RECEIVED
from src.aura.models.events import Event

logger = logging.getLogger(__name__)


@dataclass
class _SessionBinding:
    """Track the active task whose output is being captured."""

    task_id: str
    log_path: Path
    stream: TextIO


class TerminalBridge:
    """
    WebSocket bridge that connects the embedded xterm.js terminal to a real PTY/shell.

    Responsibilities:
        - Accept websocket connections from the Qt-embedded terminal.
        - Spawn and manage the underlying shell process with platform-aware PTY shims.
        - Relay terminal input and output between the browser and the PTY.
        - Persist terminal output to log files and broadcast output events.
    """

    _DEFAULT_WINDOWS_SHELL = ["powershell.exe", "-NoLogo", "-NoProfile"]
    _DEFAULT_UNIX_SHELL = ["/bin/bash", "-l"]

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        event_bus=None,
    ) -> None:
        self._host = host
        self._port = port
        self._event_bus = event_bus

        self._server: Optional[websockets.server.Serve] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._active_websocket: Optional[WebSocketServerProtocol] = None

        self._process: Optional[asyncio.subprocess.Process] = None
        self._pty_master_fd: Optional[int] = None

        self._session_lock = threading.RLock()
        self._session: Optional[_SessionBinding] = None
        self._ready_event = threading.Event()

    # ------------------------------------------------------------------ Public API
    def start(self) -> None:
        """Start the websocket server in a background event loop."""
        if self._thread and self._thread.is_alive():
            logger.debug("Terminal bridge already running on %s:%s", self._host, self._port)
            return

        self._ready_event.clear()
        self._thread = threading.Thread(
            target=self._run_event_loop,
            name="aura-terminal-bridge",
            daemon=True,
        )
        self._thread.start()
        logger.info("Terminal bridge boot requested on %s:%s", self._host, self._port)

    def stop(self) -> None:
        """Stop the websocket server and tear down active sessions."""
        self._ready_event.clear()
        loop = self._loop
        if loop is None:
            return

        async def _shutdown() -> None:
            if self._server:
                self._server.close()
                await self._server.wait_closed()
            await self._terminate_process()

        futures = [
            asyncio.run_coroutine_threadsafe(_shutdown(), loop),
        ]
        for future in futures:
            try:
                future.result(timeout=5)
            except Exception as exc:
                logger.warning("Terminal bridge shutdown wait failed: %s", exc)

        loop.call_soon_threadsafe(loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._loop = None
        self._thread = None
        self.end_session()
        logger.info("Terminal bridge stopped")

    def start_session(self, task_id: str, log_path: Path) -> None:
        """
        Begin capturing terminal output for the supplied task.

        Args:
            task_id: Identifier of the agent task.
            log_path: Destination log file path for raw terminal output.
        """
        with self._session_lock:
            self._close_session_locked()
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                stream = log_path.open("a", encoding="utf-8")
            except OSError as exc:
                logger.error("Unable to open terminal log %s: %s", log_path, exc, exc_info=True)
                raise
            self._session = _SessionBinding(task_id=task_id, log_path=log_path, stream=stream)
            logger.info("Terminal bridge capturing output for task %s at %s", task_id, log_path)

    def end_session(self) -> None:
        """Stop capturing output for the active task."""
        with self._session_lock:
            self._close_session_locked()

    def wait_ready(self, timeout: float = 5.0) -> bool:
        """
        Block until the WebSocket server is ready to accept connections.

        Args:
            timeout: Maximum seconds to wait for readiness.

        Returns:
            True when the bridge signalled readiness, False on timeout.
        """
        return self._ready_event.wait(timeout)

    def send_input(self, data: str) -> None:
        """
        Inject input directly into the underlying PTY.

        Useful for backend automation to dispatch commands even when the UI has not issued them yet.
        """
        loop = self._loop
        if loop is None:
            raise RuntimeError("Terminal bridge event loop is not running")
        asyncio.run_coroutine_threadsafe(self._write_to_process(data), loop)

    # ------------------------------------------------------------------ Internal helpers
    def _run_event_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._start_server())
            logger.info("Terminal bridge listening on ws://%s:%s", self._host, self._port)
            loop.run_forever()
        except Exception as exc:
            logger.error("Terminal bridge event loop failed: %s", exc, exc_info=True)
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()
                self._loop = None
                logger.debug("Terminal bridge event loop terminated")

    async def _start_server(self) -> None:
        """Start the WebSocket server within the running event loop."""
        self._server = await websockets.serve(
            self._handle_connection,
            self._host,
            self._port,
            ping_interval=None,
        )
        self._ready_event.set()

    async def _handle_connection(self, websocket: WebSocketServerProtocol) -> None:
        previous = self._active_websocket
        if previous and not previous.closed:
            await previous.close(code=1012, reason="New terminal client connected")
        self._active_websocket = websocket
        logger.info("Terminal client connected from %s", getattr(websocket, "remote_address", "?"))

        output_task: Optional[asyncio.Task] = None
        input_task: Optional[asyncio.Task] = None
        try:
            await self._ensure_process()
            output_task = asyncio.create_task(self._stream_process_output(websocket))
            input_task = asyncio.create_task(self._consume_websocket_input(websocket))
            await asyncio.wait(
                [output_task, input_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
        except Exception as exc:
            logger.error("Terminal bridge connection error: %s", exc, exc_info=True)
        finally:
            if input_task:
                input_task.cancel()
            if output_task:
                output_task.cancel()
            await asyncio.gather(
                *(task for task in (output_task, input_task) if task),
                return_exceptions=True,
            )
            try:
                await websocket.close()
            except Exception:
                pass
            if self._active_websocket is websocket:
                self._active_websocket = None
            await self._terminate_process()
            logger.info("Terminal client disconnected")

    async def _consume_websocket_input(self, websocket: WebSocketServerProtocol) -> None:
        async for message in websocket:
            try:
                payload = json.loads(message)
            except (TypeError, json.JSONDecodeError):
                logger.debug("Ignoring non-JSON message from terminal client: %r", message)
                continue
            msg_type = payload.get("type")
            if msg_type == "input":
                data = payload.get("data", "")
                await self._write_to_process(data)
            elif msg_type == "resize":
                cols = payload.get("cols")
                rows = payload.get("rows")
                await self._resize_pty(cols=cols, rows=rows)
            else:
                logger.debug("Unhandled terminal message type: %s", msg_type)

    async def _stream_process_output(self, websocket: WebSocketServerProtocol) -> None:
        while True:
            data = await self._read_from_process()
            if data is None:
                break
            text = data.decode("utf-8", errors="replace")
            await websocket.send(text)
            self._handle_output(text)

    async def _ensure_process(self) -> None:
        if self._process and self._process.returncode is None:
            return
        if sys.platform.startswith("win"):
            await self._spawn_windows_shell()
        else:
            await self._spawn_unix_shell()

    async def _spawn_windows_shell(self) -> None:
        logger.info("Launching Windows shell for terminal bridge")
        process = await asyncio.create_subprocess_exec(
            *self._DEFAULT_WINDOWS_SHELL,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._process = process

    async def _spawn_unix_shell(self) -> None:
        import fcntl
        import pty
        import struct
        import termios

        logger.info("Launching POSIX shell for terminal bridge")
        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        self._pty_master_fd = None
        master_fd, slave_fd = pty.openpty()
        try:
            process = await asyncio.create_subprocess_exec(
                *self._DEFAULT_UNIX_SHELL,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
            )
            self._process = process
            self._pty_master_fd = master_fd

            # Ensure master is non-blocking
            flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            # Set initial window size
            try:
                packed = struct.pack("HHHH", 24, 120, 0, 0)
                fcntl.ioctl(master_fd, termios.TIOCSWINSZ, packed)
            except OSError as exc:
                logger.debug("Failed to set initial PTY size: %s", exc)
        finally:
            os.close(slave_fd)
            if self._process is None and master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass

    async def _read_from_process(self) -> Optional[bytes]:
        if self._process is None:
            return None
        if sys.platform.startswith("win"):
            if self._process.stdout is None:
                return None
            data = await self._process.stdout.read(4096)
            return data or None

        # POSIX: read from PTY master
        master_fd = self._pty_master_fd
        if master_fd is None:
            return None
        loop = asyncio.get_running_loop()
        try:
            data = await loop.run_in_executor(None, os.read, master_fd, 4096)
        except OSError:
            return None
        return data or None

    async def _write_to_process(self, data: str) -> None:
        if not data:
            return
        encoded = data.encode("utf-8")
        if self._process is None:
            return
        if sys.platform.startswith("win"):
            stdin = self._process.stdin
            if stdin is None:
                return
            stdin.write(encoded)
            try:
                await stdin.drain()
            except Exception:
                pass
            return

        master_fd = self._pty_master_fd
        if master_fd is None:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, os.write, master_fd, encoded)

    async def _resize_pty(self, *, cols: Optional[int], rows: Optional[int]) -> None:
        if cols is None or rows is None:
            return
        if sys.platform.startswith("win"):
            # TODO: Windows ConPTY resizing could be implemented via pywinpty in future.
            return
        master_fd = self._pty_master_fd
        if master_fd is None:
            return
        import fcntl
        import struct
        import termios

        packed = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, packed)
        except OSError as exc:
            logger.debug("Failed to resize PTY: %s", exc)

    async def _terminate_process(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return

        if sys.platform.startswith("win"):
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            except Exception as exc:
                logger.debug("Failed to terminate Windows shell: %s", exc)
            await process.wait()
        else:
            master_fd = self._pty_master_fd
            self._pty_master_fd = None
            if master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            except Exception as exc:
                logger.debug("Failed to terminate POSIX shell: %s", exc)
            await process.wait()

    def _handle_output(self, text: str) -> None:
        if not text:
            return
        session: Optional[_SessionBinding] = None
        task_id: Optional[str] = None
        with self._session_lock:
            session = self._session
            if session:
                task_id = session.task_id
                try:
                    session.stream.write(text)
                    session.stream.flush()
                except Exception as exc:
                    logger.error("Failed writing terminal log for %s: %s", session.task_id, exc, exc_info=True)
        if self._event_bus and task_id:
            payload = {
                "task_id": task_id,
                "text": text,
                "stream_type": "stdout",
                "timestamp": datetime.utcnow().isoformat(),
            }
            try:
                self._event_bus.dispatch(Event(event_type=TERMINAL_OUTPUT_RECEIVED, payload=payload))
            except Exception:
                logger.error("Failed to dispatch terminal output event", exc_info=True)

    def _close_session_locked(self) -> None:
        session = self._session
        if not session:
            return
        try:
            session.stream.flush()
        except Exception:
            pass
        try:
            session.stream.close()
        except Exception:
            pass
        logger.info("Stopped capturing terminal output for task %s", session.task_id)
        self._session = None
