"""Tests for TerminalSessionManager - session lifecycle and completion detection."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from src.aura.models.agent_task import TerminalSession
from src.aura.models.event_types import (
    TERMINAL_SESSION_STARTED,
    TERMINAL_SESSION_COMPLETED,
    TERMINAL_SESSION_FAILED,
    TERMINAL_SESSION_TIMEOUT,
    TERMINAL_SESSION_ABORTED,
)
from src.aura.models.events import Event
from src.aura.services.terminal_session_manager import (
    TerminalSessionManager,
    SessionStatus,
)


class StubWorkspaceMonitor:
    """Stub workspace monitor for testing."""

    def __init__(self) -> None:
        self.changes_log: List[Dict[str, Any]] = []
        self.snapshot_index = 0

    def snapshot(self) -> Any:
        """Return the next snapshot from the changes log."""
        if self.snapshot_index < len(self.changes_log):
            changes = self.changes_log[self.snapshot_index]
            self.snapshot_index += 1
            return StubWorkspaceChanges(**changes)
        return StubWorkspaceChanges()

    def queue_changes(self, **kwargs: Any) -> None:
        """Queue a snapshot result."""
        self.changes_log.append(kwargs)

    def reset(self) -> None:
        """Reset the snapshot index."""
        self.snapshot_index = 0


class StubWorkspaceChanges:
    """Stub workspace changes for testing."""

    def __init__(
        self,
        created: List[str] | None = None,
        modified: List[str] | None = None,
        deleted: List[str] | None = None,
    ) -> None:
        self.created = created or []
        self.modified = modified or []
        self.deleted = deleted or []

    def has_changes(self) -> bool:
        return bool(self.created or self.modified or self.deleted)


class StubEventBus:
    """Stub event bus that records dispatched events."""

    def __init__(self) -> None:
        self.events: List[Event] = []

    def dispatch(self, event: Event) -> None:
        self.events.append(event)

    def get_events_by_type(self, event_type: str) -> List[Event]:
        return [e for e in self.events if e.event_type == event_type]


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    """Create a temporary workspace root."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".aura").mkdir()
    return workspace


@pytest.fixture
def workspace_monitor() -> StubWorkspaceMonitor:
    """Create a stub workspace monitor."""
    return StubWorkspaceMonitor()


@pytest.fixture
def event_bus() -> StubEventBus:
    """Create a stub event bus."""
    return StubEventBus()


@pytest.fixture
def session_manager(
    workspace_root: Path,
    workspace_monitor: StubWorkspaceMonitor,
    event_bus: StubEventBus,
) -> TerminalSessionManager:
    """Create a session manager for testing."""
    return TerminalSessionManager(
        workspace_root=workspace_root,
        workspace_monitor=workspace_monitor,
        event_bus=event_bus,
        stabilization_seconds=5,
        timeout_seconds=60,
    )


def _create_test_session(task_id: str = "test-task-123", process_id: int | None = 9999) -> TerminalSession:
    """Helper to create a test terminal session."""
    return TerminalSession(
        task_id=task_id,
        command=["python", "script.py"],
        spec_path="/workspace/.aura/specs/test-spec.json",
        process_id=process_id,
    )


# -- Registration tests ----------------------------------------------------------------


def test_register_session_adds_to_active_sessions(session_manager: TerminalSessionManager) -> None:
    """Test that registering a session adds it to active sessions."""
    session = _create_test_session()

    session_manager.register_session(session)

    assert session.task_id in session_manager.active_sessions
    status = session_manager.active_sessions[session.task_id]
    assert status.session is session
    assert status.status == "running"


def test_register_session_dispatches_started_event(
    session_manager: TerminalSessionManager,
    event_bus: StubEventBus,
) -> None:
    """Test that registering a session dispatches TERMINAL_SESSION_STARTED event."""
    session = _create_test_session()

    session_manager.register_session(session)

    started_events = event_bus.get_events_by_type(TERMINAL_SESSION_STARTED)
    assert len(started_events) == 1
    assert started_events[0].payload["task_id"] == session.task_id
    assert started_events[0].payload["process_id"] == session.process_id


def test_register_multiple_sessions(session_manager: TerminalSessionManager) -> None:
    """Test that multiple sessions can be registered and tracked."""
    session1 = _create_test_session(task_id="task-1", process_id=1001)
    session2 = _create_test_session(task_id="task-2", process_id=1002)
    session3 = _create_test_session(task_id="task-3", process_id=1003)

    session_manager.register_session(session1)
    session_manager.register_session(session2)
    session_manager.register_session(session3)

    assert len(session_manager.active_sessions) == 3
    assert "task-1" in session_manager.active_sessions
    assert "task-2" in session_manager.active_sessions
    assert "task-3" in session_manager.active_sessions


# -- Session status checking -----------------------------------------------------------


def test_check_session_returns_active_session(session_manager: TerminalSessionManager) -> None:
    """Test that check_session returns status for active sessions."""
    session = _create_test_session()
    session_manager.register_session(session)

    status = session_manager.check_session(session.task_id)

    assert status is not None
    assert status.session is session
    assert status.status == "running"


def test_check_session_returns_none_for_unknown_task(session_manager: TerminalSessionManager) -> None:
    """Test that check_session returns None for unknown task IDs."""
    status = session_manager.check_session("unknown-task-id")

    assert status is None


def test_check_session_returns_completed_session(session_manager: TerminalSessionManager) -> None:
    """Test that check_session can find completed sessions."""
    session = _create_test_session()
    session_manager.register_session(session)

    # Manually mark as completed
    status = session_manager.active_sessions[session.task_id]
    status.status = "completed"
    status.completion_reason = "Test completion"
    session_manager.completed_sessions.append(status)
    del session_manager.active_sessions[session.task_id]

    # Should still find it in completed sessions
    found_status = session_manager.check_session(session.task_id)
    assert found_status is not None
    assert found_status.status == "completed"


# -- Completion detection via stabilization -------------------------------------------


@patch("src.aura.services.terminal_session_manager.psutil")
def test_completion_via_workspace_stabilization(
    mock_psutil: Any,
    session_manager: TerminalSessionManager,
    workspace_monitor: StubWorkspaceMonitor,
    event_bus: StubEventBus,
) -> None:
    """Test that sessions complete when workspace changes stabilize."""
    # Mock process to appear as still running
    mock_process = MagicMock()
    mock_process.is_running.return_value = True
    mock_psutil.Process.return_value = mock_process

    session = _create_test_session()
    session_manager.register_session(session)

    # First check: some changes detected
    workspace_monitor.queue_changes(created=["file1.py"], modified=[], deleted=[])
    session_manager.check_all_sessions()
    assert session.task_id in session_manager.active_sessions

    # Second check: more changes
    workspace_monitor.queue_changes(created=[], modified=["file1.py"], deleted=[])
    session_manager.check_all_sessions()
    assert session.task_id in session_manager.active_sessions

    # Simulate time passing and stabilization
    status = session_manager.active_sessions[session.task_id]
    status.last_change_detected = datetime.now() - timedelta(seconds=10)

    # Third check: no changes, workspace stabilized
    workspace_monitor.queue_changes()
    newly_completed = session_manager.check_all_sessions()

    assert len(newly_completed) == 1
    assert newly_completed[0].status == "completed"
    assert "stable" in newly_completed[0].completion_reason.lower()
    assert session.task_id not in session_manager.active_sessions

    # Verify completion event dispatched
    completed_events = event_bus.get_events_by_type(TERMINAL_SESSION_COMPLETED)
    assert len(completed_events) == 1
    assert completed_events[0].payload["task_id"] == session.task_id


@patch("src.aura.services.terminal_session_manager.psutil")
def test_no_completion_without_initial_changes(
    mock_psutil: Any,
    session_manager: TerminalSessionManager,
    workspace_monitor: StubWorkspaceMonitor,
) -> None:
    """Test that sessions don't complete via stabilization without seeing initial changes."""
    # Mock process to appear as still running
    mock_process = MagicMock()
    mock_process.is_running.return_value = True
    mock_psutil.Process.return_value = mock_process

    session = _create_test_session()
    session_manager.register_session(session)

    # No changes detected at all
    workspace_monitor.queue_changes()
    workspace_monitor.queue_changes()
    workspace_monitor.queue_changes()

    newly_completed = session_manager.check_all_sessions()

    # Should not complete since we never saw any changes
    assert len(newly_completed) == 0
    assert session.task_id in session_manager.active_sessions


# -- Completion detection via marker file ----------------------------------------------


@patch("src.aura.services.terminal_session_manager.psutil")
def test_completion_via_marker_file(
    mock_psutil: Any,
    session_manager: TerminalSessionManager,
    workspace_root: Path,
    event_bus: StubEventBus,
) -> None:
    """Test that sessions complete when marker file is detected."""
    # Mock process to appear as still running
    mock_process = MagicMock()
    mock_process.is_running.return_value = True
    mock_psutil.Process.return_value = mock_process

    session = _create_test_session(task_id="marker-test")
    session_manager.register_session(session)

    # Create the completion marker file
    marker_file = workspace_root / ".aura" / f"{session.task_id}.done"
    marker_file.touch()

    newly_completed = session_manager.check_all_sessions()

    assert len(newly_completed) == 1
    assert newly_completed[0].status == "completed"
    assert "marker file" in newly_completed[0].completion_reason.lower()

    completed_events = event_bus.get_events_by_type(TERMINAL_SESSION_COMPLETED)
    assert len(completed_events) == 1


# -- Completion detection via timeout --------------------------------------------------


def test_completion_via_timeout(
    session_manager: TerminalSessionManager,
    event_bus: StubEventBus,
) -> None:
    """Test that sessions complete when timeout is exceeded."""
    session = _create_test_session()
    session_manager.register_session(session)

    # Simulate timeout by backdating the start time
    status = session_manager.active_sessions[session.task_id]
    status.started_at = datetime.now() - timedelta(seconds=120)  # 2 minutes ago

    newly_completed = session_manager.check_all_sessions()

    assert len(newly_completed) == 1
    assert newly_completed[0].status == "timeout"
    assert "timeout" in newly_completed[0].completion_reason.lower()

    timeout_events = event_bus.get_events_by_type(TERMINAL_SESSION_TIMEOUT)
    assert len(timeout_events) == 1
    assert timeout_events[0].payload["timeout_seconds"] == 60


# -- Completion detection via process exit --------------------------------------------


@patch("src.aura.services.terminal_session_manager.psutil")
def test_completion_via_process_exit_success(
    mock_psutil: Any,
    session_manager: TerminalSessionManager,
    event_bus: StubEventBus,
) -> None:
    """Test that sessions complete when process exits with code 0."""
    session = _create_test_session(process_id=1234)
    session_manager.register_session(session)

    # Mock process that has exited successfully
    mock_process = MagicMock()
    mock_process.is_running.return_value = False
    mock_process.wait.return_value = 0
    mock_psutil.Process.return_value = mock_process

    newly_completed = session_manager.check_all_sessions()

    assert len(newly_completed) == 1
    assert newly_completed[0].status == "completed"
    assert "exited successfully" in newly_completed[0].completion_reason.lower()
    assert newly_completed[0].process_exit_code == 0


@patch("src.aura.services.terminal_session_manager.psutil")
def test_completion_via_process_exit_failure(
    mock_psutil: Any,
    session_manager: TerminalSessionManager,
    event_bus: StubEventBus,
) -> None:
    """Test that sessions fail when process exits with non-zero code."""
    session = _create_test_session(process_id=1234)
    session_manager.register_session(session)

    # Mock process that has exited with error
    mock_process = MagicMock()
    mock_process.is_running.return_value = False
    mock_process.wait.return_value = 1
    mock_psutil.Process.return_value = mock_process

    newly_completed = session_manager.check_all_sessions()

    assert len(newly_completed) == 1
    assert newly_completed[0].status == "failed"
    assert "code 1" in newly_completed[0].completion_reason.lower()
    assert newly_completed[0].process_exit_code == 1

    failed_events = event_bus.get_events_by_type(TERMINAL_SESSION_FAILED)
    assert len(failed_events) == 1


@patch("src.aura.services.terminal_session_manager.psutil")
def test_no_completion_when_process_still_running(
    mock_psutil: Any,
    session_manager: TerminalSessionManager,
) -> None:
    """Test that sessions don't complete when process is still running."""
    session = _create_test_session(process_id=1234)
    session_manager.register_session(session)

    # Mock process that is still running
    mock_process = MagicMock()
    mock_process.is_running.return_value = True
    mock_psutil.Process.return_value = mock_process

    newly_completed = session_manager.check_all_sessions()

    assert len(newly_completed) == 0
    assert session.task_id in session_manager.active_sessions


# -- Session abort and cleanup ---------------------------------------------------------


@patch("src.aura.services.terminal_session_manager.psutil")
def test_abort_session_terminates_process(
    mock_psutil: Any,
    session_manager: TerminalSessionManager,
    event_bus: StubEventBus,
) -> None:
    """Test that abort_session terminates the process and marks as aborted."""
    session = _create_test_session(process_id=1234)
    session_manager.register_session(session)

    mock_process = MagicMock()
    mock_psutil.Process.return_value = mock_process

    result = session_manager.abort_session(session.task_id)

    assert result is True
    mock_process.terminate.assert_called_once()
    assert session.task_id not in session_manager.active_sessions
    assert len(session_manager.completed_sessions) == 1
    assert session_manager.completed_sessions[0].status == "aborted"

    abort_events = event_bus.get_events_by_type(TERMINAL_SESSION_ABORTED)
    assert len(abort_events) == 1


def test_abort_session_returns_false_for_unknown_task(session_manager: TerminalSessionManager) -> None:
    """Test that abort_session returns False for unknown task IDs."""
    result = session_manager.abort_session("unknown-task")

    assert result is False


@patch("src.aura.services.terminal_session_manager.psutil")
def test_cleanup_all_sessions_terminates_all_active(
    mock_psutil: Any,
    session_manager: TerminalSessionManager,
) -> None:
    """Test that cleanup_all_sessions terminates all active sessions."""
    session1 = _create_test_session(task_id="task-1", process_id=1001)
    session2 = _create_test_session(task_id="task-2", process_id=1002)
    session_manager.register_session(session1)
    session_manager.register_session(session2)

    mock_process = MagicMock()
    mock_psutil.Process.return_value = mock_process

    count = session_manager.cleanup_all_sessions()

    assert count == 2
    assert len(session_manager.active_sessions) == 0
    assert len(session_manager.completed_sessions) == 2


# -- Session retrieval methods ---------------------------------------------------------


def test_get_active_sessions(session_manager: TerminalSessionManager) -> None:
    """Test that get_active_sessions returns all active sessions."""
    session1 = _create_test_session(task_id="task-1")
    session2 = _create_test_session(task_id="task-2")
    session_manager.register_session(session1)
    session_manager.register_session(session2)

    active = session_manager.get_active_sessions()

    assert len(active) == 2
    task_ids = {s.session.task_id for s in active}
    assert task_ids == {"task-1", "task-2"}


def test_get_completed_sessions(session_manager: TerminalSessionManager) -> None:
    """Test that get_completed_sessions returns completed sessions."""
    # Create and complete some sessions
    for i in range(3):
        session = _create_test_session(task_id=f"task-{i}")
        status = SessionStatus(
            session=session,
            started_at=datetime.now(),
            status="completed",
            completion_reason="Test",
        )
        session_manager.completed_sessions.append(status)

    completed = session_manager.get_completed_sessions()

    assert len(completed) == 3
    # Should be in reverse order (most recent first)
    assert completed[0].session.task_id == "task-2"
    assert completed[1].session.task_id == "task-1"
    assert completed[2].session.task_id == "task-0"


def test_get_completed_sessions_respects_limit(session_manager: TerminalSessionManager) -> None:
    """Test that get_completed_sessions respects the limit parameter."""
    for i in range(10):
        session = _create_test_session(task_id=f"task-{i}")
        status = SessionStatus(
            session=session,
            started_at=datetime.now(),
            status="completed",
        )
        session_manager.completed_sessions.append(status)

    completed = session_manager.get_completed_sessions(limit=3)

    assert len(completed) == 3


# -- Integration tests -----------------------------------------------------------------


@patch("src.aura.services.terminal_session_manager.psutil")
def test_full_session_lifecycle_with_changes(
    mock_psutil: Any,
    session_manager: TerminalSessionManager,
    workspace_monitor: StubWorkspaceMonitor,
    event_bus: StubEventBus,
) -> None:
    """Test a complete session lifecycle from registration to completion."""
    # Mock process to appear as still running
    mock_process = MagicMock()
    mock_process.is_running.return_value = True
    mock_psutil.Process.return_value = mock_process

    session = _create_test_session()

    # Register session
    session_manager.register_session(session)
    assert len(event_bus.events) == 1  # Started event

    # Simulate work being done
    workspace_monitor.queue_changes(created=["new_file.py"])
    session_manager.check_all_sessions()

    workspace_monitor.queue_changes(modified=["new_file.py"])
    session_manager.check_all_sessions()

    # Simulate stabilization
    status = session_manager.active_sessions[session.task_id]
    status.last_change_detected = datetime.now() - timedelta(seconds=10)
    workspace_monitor.queue_changes()  # No changes

    # Should complete
    newly_completed = session_manager.check_all_sessions()

    assert len(newly_completed) == 1
    assert newly_completed[0].status == "completed"
    assert newly_completed[0].changes_since_last_check == 2  # Tracked the 2 changes
    assert len(session_manager.completed_sessions) == 1
    assert len(session_manager.active_sessions) == 0

    # Verify all events dispatched
    assert len(event_bus.events) == 2  # Started + Completed
    assert event_bus.events[0].event_type == TERMINAL_SESSION_STARTED
    assert event_bus.events[1].event_type == TERMINAL_SESSION_COMPLETED
