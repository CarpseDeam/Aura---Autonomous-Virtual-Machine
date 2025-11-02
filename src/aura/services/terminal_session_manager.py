"""
Terminal Session Manager - Tracks and monitors active terminal agent sessions.

Responsibilities:
- Track active terminal sessions by task ID
- Monitor sessions for completion signals
- Detect workspace change stabilization
- Check for completion marker files
- Handle session timeouts
- Manage process lifecycle and cleanup
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from collections import deque
import threading
from typing import Deque, Optional as _Optional
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set
import subprocess

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None  # type: ignore[assignment]
    PSUTIL_AVAILABLE = False

from src.aura.models.agent_task import TerminalSession, TaskSummary
from src.aura.models.event_types import (
    TERMINAL_SESSION_STARTED,
    TERMINAL_SESSION_PROGRESS,
    TERMINAL_SESSION_COMPLETED,
    TERMINAL_SESSION_FAILED,
    TERMINAL_SESSION_TIMEOUT,
    TERMINAL_SESSION_ABORTED,
    TERMINAL_OUTPUT_RECEIVED,
)
from src.aura.models.events import Event
from src.aura.services.workspace_monitor import WorkspaceChangeMonitor

logger = logging.getLogger(__name__)


@dataclass
class SessionStatus:
    """Status information for a tracked terminal session."""

    session: TerminalSession
    started_at: datetime
    last_change_detected: Optional[datetime] = None
    changes_since_last_check: int = 0
    status: str = "running"  # running, completed, failed, timeout
    completion_reason: Optional[str] = None
    process_exit_code: Optional[int] = None


class TerminalSessionManager:
    """
    Manages active terminal agent sessions and detects completion.

    Completion is determined by multiple signals:
    1. Workspace changes stabilizing (no changes for stabilization_seconds)
    2. Completion marker file present (.aura/{task_id}.done)
    3. Process has exited
    4. Timeout exceeded
    """

    def __init__(
        self,
        workspace_root: Path,
        workspace_monitor: WorkspaceChangeMonitor,
        event_bus=None,
        stabilization_seconds: int = 90,
        timeout_seconds: int = 600,
    ) -> None:
        """
        Initialize the session manager.

        Args:
            workspace_root: Root directory of the workspace
            workspace_monitor: WorkspaceChangeMonitor for detecting file changes
            event_bus: EventBus for dispatching lifecycle events (optional)
            stabilization_seconds: Seconds of no changes before considering stable
            timeout_seconds: Maximum seconds before timing out a session
        """
        self.workspace_root = Path(workspace_root)
        self.workspace_monitor = workspace_monitor
        self.event_bus = event_bus
        self.stabilization_seconds = stabilization_seconds
        self.timeout_seconds = timeout_seconds

        self.active_sessions: Dict[str, SessionStatus] = {}
        self.completed_sessions: List[SessionStatus] = []

        logger.info(
            "TerminalSessionManager initialized (stabilization=%ds, timeout=%ds)",
            stabilization_seconds,
            timeout_seconds,
        )

    def register_session(self, session: TerminalSession, process: _Optional[subprocess.Popen] = None) -> None:
        """
        Register a new terminal session for tracking.

        Args:
            session: The TerminalSession to track
            process: Optional subprocess handle when spawned with pipes for I/O capture
        """
        status = SessionStatus(
            session=session,
            started_at=datetime.now(),
            status="running",
        )
        self.active_sessions[session.task_id] = status
        logger.info(
            "Registered terminal session for task %s (pid=%s)",
            session.task_id,
            session.process_id,
        )

        # Attach I/O capture if a piped process is provided
        if process is not None:
            try:
                self._attach_io_capture(session.task_id, process)
            except Exception as exc:
                logger.warning("Failed to attach I/O capture for task %s: %s", session.task_id, exc, exc_info=True)

        # Dispatch session started event
        if self.event_bus:
            self.event_bus.dispatch(
                Event(
                    event_type=TERMINAL_SESSION_STARTED,
                    payload={
                        "task_id": session.task_id,
                        "process_id": session.process_id,
                        "command": session.command,
                        "started_at": status.started_at.isoformat(),
                    },
                )
            )

    def check_session(self, task_id: str) -> Optional[SessionStatus]:
        """
        Check the status of a specific session.

        Args:
            task_id: The task ID to check

        Returns:
            SessionStatus if found, None otherwise
        """
        return self.active_sessions.get(task_id) or next(
            (s for s in self.completed_sessions if s.session.task_id == task_id),
            None,
        )

    def check_all_sessions(self) -> List[SessionStatus]:
        """
        Check all active sessions for completion signals.

        This should be called periodically (e.g., every few seconds) to monitor progress.

        Returns:
            List of sessions that have completed in this check
        """
        newly_completed = []

        for task_id, status in list(self.active_sessions.items()):
            completion_result = self._check_completion_signals(status)

            if completion_result:
                status.status = completion_result["status"]
                status.completion_reason = completion_result["reason"]
                status.process_exit_code = completion_result.get("exit_code")

                logger.info(
                    "Session %s completed: %s (reason: %s)",
                    task_id,
                    status.status,
                    status.completion_reason,
                )

                # Dispatch appropriate completion event
                if self.event_bus:
                    duration = (datetime.now() - status.started_at).total_seconds()
                    payload = {
                        "task_id": task_id,
                        "completion_reason": status.completion_reason,
                        "duration_seconds": duration,
                        "changes_made": status.changes_since_last_check,
                    }

                    if status.process_exit_code is not None:
                        payload["exit_code"] = status.process_exit_code

                    # Attach summary.json if present
                    try:
                        summary_path = self.workspace_root / ".aura" / f"{task_id}.summary.json"
                        if summary_path.exists():
                            import json as _json
                            raw = summary_path.read_text(encoding="utf-8")
                            parsed = _json.loads(raw)
                            try:
                                payload["summary"] = TaskSummary(**parsed).model_dump()
                            except Exception:
                                if isinstance(parsed, dict):
                                    payload["summary"] = parsed
                    except Exception as exc:
                        logger.warning(
                            "Unable to attach summary for task %s: %s", task_id, exc
                        )

                    # Choose event type based on status
                    if status.status == "completed":
                        event_type = TERMINAL_SESSION_COMPLETED
                    elif status.status == "timeout":
                        event_type = TERMINAL_SESSION_TIMEOUT
                        payload["timeout_seconds"] = self.timeout_seconds
                    elif status.status == "failed":
                        event_type = TERMINAL_SESSION_FAILED
                        payload["failure_reason"] = status.completion_reason
                    else:
                        event_type = TERMINAL_SESSION_COMPLETED  # Default

                    self.event_bus.dispatch(Event(event_type=event_type, payload=payload))

                # Move to completed sessions
                self.completed_sessions.append(status)
                del self.active_sessions[task_id]
                newly_completed.append(status)

        return newly_completed

    # ----------------------------- I/O capture support ------------------------------

    @dataclass
    class _ProcessIO:
        process: subprocess.Popen
        stdout_buffer: Deque[str] = field(default_factory=lambda: deque(maxlen=100))
        stderr_buffer: Deque[str] = field(default_factory=lambda: deque(maxlen=100))
        lock: threading.Lock = field(default_factory=threading.Lock)
        stdin_closed: bool = False

    _io_registry: Dict[str, "TerminalSessionManager._ProcessIO"] = {}

    def _attach_io_capture(self, task_id: str, process: subprocess.Popen) -> None:
        """
        Start background readers to buffer last N lines of stdout/stderr.

        Args:
            task_id: The task identifier.
            process: subprocess with pipes open.
        """
        if not hasattr(process, "stdout") or not hasattr(process, "stderr"):
            logger.debug("Process for task %s has no pipes; skipping I/O capture", task_id)
            return

        pid = getattr(process, "pid", None)
        logger.debug("Starting I/O capture for task %s (pid=%s)", task_id, pid)
        logger.debug(
            "Process has stdout/stderr pipes: %s/%s",
            process.stdout is not None,
            process.stderr is not None,
        )

        io_state = TerminalSessionManager._ProcessIO(process=process)
        self._io_registry[task_id] = io_state
        logger.debug("Created I/O state for task %s", task_id)

        def _reader(stream, target: Deque[str], channel: str) -> None:
            logger.debug("I/O reader thread started for task %s (channel=%s)", task_id, channel)
            try:
                # Read line by line to avoid blocking on partial buffers
                while True:
                    line = stream.readline()
                    if not line:
                        logger.debug("EOF reached for task %s (%s)", task_id, channel)
                        break
                    try:
                        text = line.decode("utf-8", errors="replace") if isinstance(line, (bytes, bytearray)) else str(line)
                    except Exception:
                        text = str(line)
                    preview = text[:100].rstrip("\n")
                    logger.debug("Read from %s (task=%s): %s", channel, task_id, preview)
                    # Strip trailing newline and buffer + notify
                    self._buffer_and_dispatch(task_id, io_state, target, text.rstrip("\n"), channel)
            except Exception as exc:
                logger.error("I/O reader for task %s (%s) failed: %s", task_id, channel, exc, exc_info=True)

        # Launch daemon threads
        if process.stdout is not None:
            logger.debug("Launching stdout reader thread for task %s", task_id)
            t_out = threading.Thread(target=_reader, args=(process.stdout, io_state.stdout_buffer, "stdout"), daemon=True)
            t_out.start()
        if process.stderr is not None:
            logger.debug("Launching stderr reader thread for task %s", task_id)
            t_err = threading.Thread(target=_reader, args=(process.stderr, io_state.stderr_buffer, "stderr"), daemon=True)
            t_err.start()

    def read_terminal_output(self, task_id: str, max_lines: int = 100, include_stderr: bool = True) -> str:
        """
        Return recent terminal output for the given session as a single text block.

        Args:
            task_id: The task/session identifier.
            max_lines: Maximum total lines to return across channels.
            include_stderr: Whether to include stderr buffer.

        Returns:
            Concatenated recent output (stdout first, then stderr section if requested).
        """
        io_state = self._io_registry.get(task_id)
        if not io_state:
            logger.debug("No I/O state found for task %s", task_id)
            return ""

        with io_state.lock:
            stdout_lines = list(io_state.stdout_buffer)[-max_lines:]
            stderr_lines = list(io_state.stderr_buffer)[-max_lines:] if include_stderr else []

        output = "\n".join(stdout_lines)
        if include_stderr and stderr_lines:
            output = f"{output}\n[stderr]\n" + "\n".join(stderr_lines) if output else "[stderr]\n" + "\n".join(stderr_lines)
        return output

    def send_to_terminal(self, task_id: str, message: str, append_newline: bool = True) -> None:
        """
        Send a message to the process stdin for the given task.

        Args:
            task_id: The task/session identifier.
            message: The text to write to stdin.
            append_newline: Append a trailing newline to the message.

        Raises:
            RuntimeError: If stdin is unavailable or the process has terminated.
        """
        io_state = self._io_registry.get(task_id)
        if not io_state:
            raise RuntimeError(f"No active I/O for task {task_id}")

        proc = io_state.process
        if proc.poll() is not None:
            raise RuntimeError(f"Process for task {task_id} is not running (exit={proc.returncode})")

        stdin = getattr(proc, "stdin", None)
        if stdin is None:
            raise RuntimeError(f"Process for task {task_id} has no stdin pipe")

        try:
            data = message + ("\n" if append_newline else "")
            stdin.write(data)
            stdin.flush()
        except Exception as exc:
            logger.error("Failed to write to stdin for task %s: %s", task_id, exc, exc_info=True)
            raise RuntimeError(f"Failed to send input to task {task_id}") from exc

    def _buffer_and_dispatch(
        self,
        task_id: str,
        io_state: "TerminalSessionManager._ProcessIO",
        target: Deque[str],
        text: str,
        channel: str,
    ) -> None:
        """
        Append text to the appropriate buffer and dispatch an output event.

        Args:
            task_id: Session identifier
            io_state: Shared I/O state containing the buffers and lock
            target: The buffer to append to
            text: Line text without trailing newline
            channel: 'stdout' or 'stderr'
        """
        with io_state.lock:
            target.append(text)

        if self.event_bus:
            try:
                payload = {
                    "task_id": task_id,
                    "text": text,
                    "stream_type": channel,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                }
                logger.debug(
                    "About to dispatch TERMINAL_OUTPUT_RECEIVED (task=%s, stream=%s, text_length=%d)",
                    task_id,
                    channel,
                    len(text),
                )
                self.event_bus.dispatch(Event(event_type=TERMINAL_OUTPUT_RECEIVED, payload=payload))
                logger.debug("Event dispatched successfully (task=%s, stream=%s)", task_id, channel)
            except Exception as exc:
                logger.error("Failed dispatching terminal output event for %s: %s", task_id, exc, exc_info=True)

    def _check_completion_signals(self, status: SessionStatus) -> Optional[Dict]:
        """
        Check various completion signals for a session.

        Args:
            status: The session status to check

        Returns:
            Dictionary with completion info if completed, None if still running
        """
        now = datetime.now()
        task_id = status.session.task_id

        # Signal 1: Check for timeout
        elapsed = (now - status.started_at).total_seconds()
        if elapsed > self.timeout_seconds:
            return {
                "status": "timeout",
                "reason": f"Session exceeded timeout of {self.timeout_seconds}s",
            }

        # Signal 2: Check if process has exited
        exit_code = self._check_process_exit(status)
        if exit_code is not None:
            if exit_code == 0:
                return {
                    "status": "completed",
                    "reason": "Process exited successfully",
                    "exit_code": exit_code,
                }
            else:
                return {
                    "status": "failed",
                    "reason": f"Process exited with code {exit_code}",
                    "exit_code": exit_code,
                }

        # Signal 3: Check for completion marker file
        marker_file = self.workspace_root / ".aura" / f"{task_id}.done"
        if marker_file.exists():
            summary_path = self.workspace_root / ".aura" / f"{task_id}.summary.json"
            summary_data: Optional[dict] = None
            if summary_path.exists():
                try:
                    raw = summary_path.read_text(encoding="utf-8")
                    import json as _json
                    parsed = _json.loads(raw)
                    try:
                        summary = TaskSummary(**parsed)
                        summary_data = summary.model_dump()
                    except Exception:
                        summary_data = parsed if isinstance(parsed, dict) else None
                except Exception as exc:
                    logger.error(
                        "Failed to read summary.json for task %s: %s", task_id, exc, exc_info=True
                    )
            return {
                "status": "completed",
                "reason": "Completion marker file found",
                "summary": summary_data,
            }

        # Signal 4: Check for workspace change stabilization
        changes = self.workspace_monitor.snapshot()
        if changes.has_changes():
            # Update last change time
            status.last_change_detected = now
            status.changes_since_last_check += len(changes.created) + len(changes.modified)
            logger.debug(
                "Session %s: detected %d changes",
                task_id,
                len(changes.created) + len(changes.modified),
            )
        elif status.last_change_detected and status.changes_since_last_check > 0:
            # No changes in this check, but we've seen changes before
            time_since_last_change = (now - status.last_change_detected).total_seconds()
            if time_since_last_change >= self.stabilization_seconds:
                return {
                    "status": "completed",
                    "reason": f"Workspace stable for {self.stabilization_seconds}s after {status.changes_since_last_check} changes",
                }

        # Still running
        return None

    def _check_process_exit(self, status: SessionStatus) -> Optional[int]:
        """
        Check if a process has exited and return its exit code.

        Args:
            status: The session status to check

        Returns:
            Exit code if process has exited, None if still running
        """
        if not status.session.process_id:
            return None

        if not PSUTIL_AVAILABLE:
            # psutil not available, use subprocess polling as fallback
            logger.debug("psutil not available, using basic process checking")
            # We can't check without recreating the Popen object, so skip this check
            return None

        try:
            process = psutil.Process(status.session.process_id)
            if process.is_running():
                return None
            else:
                # Process has terminated
                return process.wait() if hasattr(process, 'wait') else 0
        except psutil.NoSuchProcess:
            # Process doesn't exist, assume it exited
            logger.debug("Process %d no longer exists", status.session.process_id)
            return 0

    def abort_session(self, task_id: str) -> bool:
        """
        Abort an active session by terminating its process.

        Args:
            task_id: The task ID to abort

        Returns:
            True if aborted successfully, False if session not found or already completed
        """
        status = self.active_sessions.get(task_id)
        if not status:
            logger.warning("Cannot abort session %s: not found in active sessions", task_id)
            return False

        if status.session.process_id:
            if not PSUTIL_AVAILABLE:
                logger.warning("psutil not available, cannot terminate process")
                return False

            try:
                process = psutil.Process(status.session.process_id)
                process.terminate()
                logger.info("Terminated process %d for session %s", status.session.process_id, task_id)

                # Mark as aborted
                status.status = "aborted"
                status.completion_reason = "Manually aborted by user"

                # Dispatch abort event
                if self.event_bus:
                    self.event_bus.dispatch(
                        Event(
                            event_type=TERMINAL_SESSION_ABORTED,
                            payload={
                                "task_id": task_id,
                                "aborted_by": "user",
                            },
                        )
                    )

                self.completed_sessions.append(status)
                del self.active_sessions[task_id]
                return True
            except Exception as exc:
                logger.error("Failed to terminate process %d: %s", status.session.process_id, exc)
                return False

        return False

    def cleanup_all_sessions(self) -> int:
        """
        Terminate all active sessions. Call this on application shutdown.

        Returns:
            Number of sessions terminated
        """
        count = 0
        for task_id in list(self.active_sessions.keys()):
            if self.abort_session(task_id):
                count += 1
        logger.info("Cleaned up %d terminal sessions on shutdown", count)
        return count

    def get_active_sessions(self) -> List[SessionStatus]:
        """Get a list of all active sessions."""
        return list(self.active_sessions.values())

    def get_completed_sessions(self, limit: int = 50) -> List[SessionStatus]:
        """
        Get a list of recently completed sessions.

        Args:
            limit: Maximum number of sessions to return

        Returns:
            List of completed sessions, most recent first
        """
        return self.completed_sessions[-limit:][::-1]
