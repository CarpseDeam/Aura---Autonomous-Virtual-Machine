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

from src.aura.models.agent_task import TerminalSession
from src.aura.models.event_types import (
    TERMINAL_SESSION_STARTED,
    TERMINAL_SESSION_PROGRESS,
    TERMINAL_SESSION_COMPLETED,
    TERMINAL_SESSION_FAILED,
    TERMINAL_SESSION_TIMEOUT,
    TERMINAL_SESSION_ABORTED,
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

    def register_session(self, session: TerminalSession) -> None:
        """
        Register a new terminal session for tracking.

        Args:
            session: The TerminalSession to track
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
            return {
                "status": "completed",
                "reason": "Completion marker file found",
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
