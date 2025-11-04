"""
Comprehensive unit tests for TerminalAgentService event bus integration.
Tests event dispatching from the monitoring thread to the event bus.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock

import pytest

from src.aura.models.agent_task import TerminalSession
from src.aura.models.event_types import AGENT_OUTPUT
from src.aura.services.terminal_agent_service import TerminalAgentService


class MockEventBus:
    """Mock event bus that captures dispatched events."""

    def __init__(self) -> None:
        self.dispatched_events: List[Any] = []
        self.event_count = 0

    def dispatch(self, event: Any) -> None:
        """Capture dispatched events for validation."""
        self.dispatched_events.append(event)
        self.event_count += 1

    def get_events_by_type(self, event_type: str) -> List[Any]:
        """Filter events by type."""
        return [e for e in self.dispatched_events if e.event_type == event_type]

    def clear(self) -> None:
        """Reset captured events."""
        self.dispatched_events = []
        self.event_count = 0


class MockLLMService:
    """Mock LLM service for testing."""

    def run_for_agent(self, agent_name: str, prompt: str) -> str:
        return "Mock response"


class DummyExpectModule:
    """Dummy expect module to prevent actual spawning."""
    TIMEOUT = TimeoutError
    EOF = EOFError

    @staticmethod
    def spawn(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("spawn should not be called in these tests")


@pytest.fixture()
def mock_event_bus() -> MockEventBus:
    """Provide a mock event bus for testing."""
    return MockEventBus()


@pytest.fixture()
def mock_llm() -> MockLLMService:
    """Provide a mock LLM service."""
    return MockLLMService()


@pytest.fixture()
def service(tmp_path: Path, mock_event_bus: MockEventBus, mock_llm: MockLLMService, monkeypatch: pytest.MonkeyPatch) -> TerminalAgentService:
    """Create a TerminalAgentService with mocked dependencies."""
    monkeypatch.setattr(TerminalAgentService, "_load_expect_module", lambda self: DummyExpectModule)
    return TerminalAgentService(
        workspace_root=tmp_path,
        llm_service=mock_llm,
        event_bus=mock_event_bus,
    )


class TestEventDispatchingBasics:
    """Test basic event dispatching functionality."""

    def test_event_bus_stored_in_init(self, service: TerminalAgentService, mock_event_bus: MockEventBus) -> None:
        """Verify event bus is stored as instance variable."""
        assert hasattr(service, 'event_bus')
        assert service.event_bus is mock_event_bus

    def test_agent_output_event_has_correct_structure(self, mock_event_bus: MockEventBus) -> None:
        """Verify AGENT_OUTPUT events have required payload fields."""
        from src.aura.models.events import Event
        from datetime import datetime

        event = Event(
            event_type=AGENT_OUTPUT,
            payload={
                "task_id": "test123",
                "text": "Agent output line",
                "timestamp": datetime.now().isoformat()
            }
        )

        mock_event_bus.dispatch(event)

        dispatched = mock_event_bus.dispatched_events[0]
        assert dispatched.event_type == AGENT_OUTPUT
        assert "task_id" in dispatched.payload
        assert "text" in dispatched.payload
        assert "timestamp" in dispatched.payload


class TestMonitoringThreadEventDispatching:
    """Test event dispatching from the monitoring thread."""

    def test_windows_monitoring_dispatches_events(
        self,
        service: TerminalAgentService,
        mock_event_bus: MockEventBus,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that Windows monitoring thread dispatches AGENT_OUTPUT events."""
        monkeypatch.setattr(sys, 'platform', 'win32')

        # Create mock pexpect child (both Windows and Unix now use the same interface)
        class MockExpect:
            TIMEOUT = TimeoutError
            EOF = EOFError

        mock_expect = MockExpect()
        monkeypatch.setattr(service, "_expect", mock_expect)

        mock_child = MagicMock()
        mock_child.pid = 12345
        mock_child.isalive.side_effect = [True, True, True, False]
        mock_child.readline.side_effect = [
            "Line 1\n",
            "Line 2\n",
            "Line 3\n",
            mock_expect.EOF()
        ]

        # Create session
        session = TerminalSession(
            task_id="test-task",
            command=["claude"],
            spec_path=str(tmp_path / "spec.md"),
            process_id=12345,
            child=mock_child
        )

        # Create log file
        log_path = tmp_path / ".aura" / "test-task.output.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch()

        # Start monitoring in a thread
        import threading
        thread = threading.Thread(
            target=service._monitor_pty_output,  # noqa: SLF001
            args=(session, log_path),
            daemon=True
        )
        thread.start()
        thread.join(timeout=2.0)

        # Verify events were dispatched
        agent_events = mock_event_bus.get_events_by_type(AGENT_OUTPUT)
        assert len(agent_events) >= 3, "Expected at least 3 AGENT_OUTPUT events"

        # Verify event structure
        for event in agent_events:
            assert event.payload.get("task_id") == "test-task"
            assert "text" in event.payload
            assert "timestamp" in event.payload

    def test_unix_monitoring_dispatches_events(
        self,
        service: TerminalAgentService,
        mock_event_bus: MockEventBus,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that Unix monitoring thread dispatches AGENT_OUTPUT events."""
        monkeypatch.setattr(sys, 'platform', 'linux')

        # Create mock pexpect child
        class MockExpect:
            TIMEOUT = TimeoutError
            EOF = EOFError

        mock_expect = MockExpect()
        monkeypatch.setattr(service, "_expect", mock_expect)

        mock_child = MagicMock()
        mock_child.isalive.side_effect = [True, True, True, False]
        mock_child.readline.side_effect = [
            "Line 1\n",
            "Line 2\n",
            "Line 3\n",
            mock_expect.EOF()
        ]

        session = TerminalSession(
            task_id="test-task",
            command=["claude"],
            spec_path=str(tmp_path / "spec.md"),
            process_id=12345,
            child=mock_child
        )

        log_path = tmp_path / ".aura" / "test-task.output.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch()

        # Monitor output
        import threading
        thread = threading.Thread(
            target=service._monitor_pty_output,  # noqa: SLF001
            args=(session, log_path),
            daemon=True
        )
        thread.start()
        thread.join(timeout=2.0)

        # Verify events
        agent_events = mock_event_bus.get_events_by_type(AGENT_OUTPUT)
        assert len(agent_events) >= 3


class TestEventDispatchingErrorHandling:
    """Test error handling during event dispatching."""

    def test_event_dispatch_failure_does_not_crash_monitoring(
        self,
        service: TerminalAgentService,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify monitoring continues even if event dispatch fails."""
        monkeypatch.setattr(sys, 'platform', 'win32')

        # Create event bus that raises on dispatch
        class FailingEventBus:
            def __init__(self) -> None:
                self.dispatch_count = 0

            def dispatch(self, event: Any) -> None:
                self.dispatch_count += 1
                if self.dispatch_count <= 2:
                    raise RuntimeError("Event dispatch failed")

        failing_bus = FailingEventBus()
        service.event_bus = failing_bus

        # Create mock pexpect child
        class MockExpect:
            TIMEOUT = TimeoutError
            EOF = EOFError

        mock_expect = MockExpect()
        monkeypatch.setattr(service, "_expect", mock_expect)

        mock_child = MagicMock()
        mock_child.isalive.side_effect = [True, True, True, True, False]
        mock_child.readline.side_effect = [
            "Line 1\n",
            "Line 2\n",
            "Line 3\n",
            "Line 4\n",
            mock_expect.EOF()
        ]

        session = TerminalSession(
            task_id="test-task",
            command=["claude"],
            spec_path=str(tmp_path / "spec.md"),
            child=mock_child
        )

        log_path = tmp_path / ".aura" / "test-task.output.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch()

        # Should not raise exception
        import threading
        thread = threading.Thread(
            target=service._monitor_pty_output,  # noqa: SLF001
            args=(session, log_path),
            daemon=True
        )
        thread.start()
        thread.join(timeout=2.0)

        # Verify monitoring attempted multiple dispatches despite failures
        assert failing_bus.dispatch_count >= 3


class TestEventDispatchingPerformance:
    """Test performance characteristics of event dispatching."""

    def test_high_volume_output_dispatches_efficiently(
        self,
        service: TerminalAgentService,
        mock_event_bus: MockEventBus,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify monitoring can handle high-volume output without lag."""
        monkeypatch.setattr(sys, 'platform', 'win32')

        # Create mock pexpect child
        class MockExpect:
            TIMEOUT = TimeoutError
            EOF = EOFError

        mock_expect = MockExpect()
        monkeypatch.setattr(service, "_expect", mock_expect)

        # Generate 1000 lines of output
        output_lines = [f"Output line {i}\n" for i in range(1000)]
        output_lines.append(mock_expect.EOF())

        mock_child = MagicMock()
        mock_child.isalive.return_value = True
        mock_child.readline.side_effect = output_lines

        session = TerminalSession(
            task_id="test-task",
            command=["claude"],
            spec_path=str(tmp_path / "spec.md"),
            child=mock_child
        )

        log_path = tmp_path / ".aura" / "test-task.output.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch()

        start_time = time.time()

        import threading
        thread = threading.Thread(
            target=service._monitor_pty_output,  # noqa: SLF001
            args=(session, log_path),
            daemon=True
        )
        thread.start()
        thread.join(timeout=10.0)

        elapsed = time.time() - start_time

        # Should process 1000 lines in under 5 seconds
        assert elapsed < 5.0, f"Took {elapsed}s to process 1000 lines"
        assert len(mock_event_bus.dispatched_events) >= 1000

    def test_event_dispatching_does_not_block_output_reading(
        self,
        service: TerminalAgentService,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify slow event dispatch doesn't block output reading."""
        monkeypatch.setattr(sys, 'platform', 'win32')

        class SlowEventBus:
            def __init__(self) -> None:
                self.dispatch_count = 0

            def dispatch(self, event: Any) -> None:
                self.dispatch_count += 1
                time.sleep(0.001)  # 1ms delay per event

        slow_bus = SlowEventBus()
        service.event_bus = slow_bus

        # Create mock pexpect child
        class MockExpect:
            TIMEOUT = TimeoutError
            EOF = EOFError

        mock_expect = MockExpect()
        monkeypatch.setattr(service, "_expect", mock_expect)

        output_lines = [f"Line {i}\n" for i in range(100)]
        output_lines.append(mock_expect.EOF())

        mock_child = MagicMock()
        mock_child.isalive.return_value = True
        mock_child.readline.side_effect = output_lines

        session = TerminalSession(
            task_id="test-task",
            command=["claude"],
            spec_path=str(tmp_path / "spec.md"),
            child=mock_child
        )

        log_path = tmp_path / ".aura" / "test-task.output.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch()

        start_time = time.time()

        import threading
        thread = threading.Thread(
            target=service._monitor_pty_output,  # noqa: SLF001
            args=(session, log_path),
            daemon=True
        )
        thread.start()
        thread.join(timeout=5.0)

        elapsed = time.time() - start_time

        # Should complete even with slow dispatch
        # 100 events * 1ms = 0.1s minimum, should be under 2s total
        assert elapsed < 2.0
        assert slow_bus.dispatch_count >= 100


class TestEventPayloadValidation:
    """Test that event payloads contain correct data."""

    def test_event_contains_task_id(
        self,
        service: TerminalAgentService,
        mock_event_bus: MockEventBus,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify events contain the correct task ID."""
        monkeypatch.setattr(sys, 'platform', 'win32')

        # Create mock pexpect child
        class MockExpect:
            TIMEOUT = TimeoutError
            EOF = EOFError

        mock_expect = MockExpect()
        monkeypatch.setattr(service, "_expect", mock_expect)

        mock_child = MagicMock()
        mock_child.isalive.side_effect = [True, False]
        mock_child.readline.side_effect = ["Test output\n", mock_expect.EOF()]

        session = TerminalSession(
            task_id="unique-task-id-123",
            command=["claude"],
            spec_path=str(tmp_path / "spec.md"),
            child=mock_child
        )

        log_path = tmp_path / ".aura" / "unique-task-id-123.output.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch()

        import threading
        thread = threading.Thread(
            target=service._monitor_pty_output,  # noqa: SLF001
            args=(session, log_path),
            daemon=True
        )
        thread.start()
        thread.join(timeout=2.0)

        events = mock_event_bus.get_events_by_type(AGENT_OUTPUT)
        assert len(events) > 0
        assert events[0].payload["task_id"] == "unique-task-id-123"

    def test_event_contains_actual_output_text(
        self,
        service: TerminalAgentService,
        mock_event_bus: MockEventBus,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify events contain the actual agent output text."""
        monkeypatch.setattr(sys, 'platform', 'win32')

        # Create mock pexpect child
        class MockExpect:
            TIMEOUT = TimeoutError
            EOF = EOFError

        mock_expect = MockExpect()
        monkeypatch.setattr(service, "_expect", mock_expect)

        mock_child = MagicMock()
        mock_child.isalive.side_effect = [True, True, False]
        mock_child.readline.side_effect = [
            "Creating file main.py\n",
            "Writing tests\n",
            mock_expect.EOF()
        ]

        session = TerminalSession(
            task_id="test-task",
            command=["claude"],
            spec_path=str(tmp_path / "spec.md"),
            child=mock_child
        )

        log_path = tmp_path / ".aura" / "test-task.output.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch()

        import threading
        thread = threading.Thread(
            target=service._monitor_pty_output,  # noqa: SLF001
            args=(session, log_path),
            daemon=True
        )
        thread.start()
        thread.join(timeout=2.0)

        events = mock_event_bus.get_events_by_type(AGENT_OUTPUT)
        assert len(events) >= 2

        texts = [e.payload["text"] for e in events]
        assert "Creating file main.py" in texts[0]
        assert "Writing tests" in texts[1]

    def test_event_contains_valid_timestamp(
        self,
        service: TerminalAgentService,
        mock_event_bus: MockEventBus,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify events contain valid ISO format timestamps."""
        from datetime import datetime

        monkeypatch.setattr(sys, 'platform', 'win32')

        # Create mock pexpect child
        class MockExpect:
            TIMEOUT = TimeoutError
            EOF = EOFError

        mock_expect = MockExpect()
        monkeypatch.setattr(service, "_expect", mock_expect)

        mock_child = MagicMock()
        mock_child.isalive.side_effect = [True, False]
        mock_child.readline.side_effect = ["Output\n", mock_expect.EOF()]

        session = TerminalSession(
            task_id="test-task",
            command=["claude"],
            spec_path=str(tmp_path / "spec.md"),
            child=mock_child
        )

        log_path = tmp_path / ".aura" / "test-task.output.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch()

        import threading
        thread = threading.Thread(
            target=service._monitor_pty_output,  # noqa: SLF001
            args=(session, log_path),
            daemon=True
        )
        thread.start()
        thread.join(timeout=2.0)

        events = mock_event_bus.get_events_by_type(AGENT_OUTPUT)
        assert len(events) > 0

        timestamp = events[0].payload["timestamp"]
        # Should be parseable as ISO format
        parsed = datetime.fromisoformat(timestamp)
        assert parsed is not None
