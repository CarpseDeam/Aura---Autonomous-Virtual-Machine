"""
Integration tests for end-to-end agent output flow.
Tests the full pipeline from agent spawn to GUI display.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import List, Any

import pytest
from PySide6.QtCore import QCoreApplication

from src.aura.app.event_bus import EventBus
from src.aura.models.agent_task import AgentSpecification
from src.aura.models.event_types import AGENT_OUTPUT
from src.aura.services.terminal_agent_service import TerminalAgentService


class MockLLMService:
    """Mock LLM service for integration testing."""

    def run_for_agent(self, agent_name: str, prompt: str) -> str:
        return "Mock response"


class EventCollector:
    """Collects events from the event bus for validation."""

    def __init__(self) -> None:
        self.events: List[Any] = []

    def handle_event(self, event: Any) -> None:
        """Handle incoming events and store them."""
        self.events.append(event)

    def get_agent_output_events(self) -> List[Any]:
        """Get all AGENT_OUTPUT events."""
        return [e for e in self.events if e.event_type == AGENT_OUTPUT]

    def wait_for_events(self, count: int, timeout: float = 5.0) -> bool:
        """Wait for at least count events to arrive."""
        start = time.time()
        while len(self.get_agent_output_events()) < count:
            if time.time() - start > timeout:
                return False
            QCoreApplication.processEvents()
            time.sleep(0.01)
        return True

    def get_events_for_task(self, task_id: str) -> List[Any]:
        """Get all events for a specific task ID."""
        return [
            e for e in self.get_agent_output_events()
            if e.payload.get("task_id") == task_id
        ]


@pytest.fixture(scope="session")
def qapp():
    """Provide a QCoreApplication instance for Qt event processing."""
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication(sys.argv)
    return app


@pytest.fixture()
def event_bus(qapp) -> EventBus:
    """Provide a real event bus for integration testing."""
    return EventBus()


@pytest.fixture()
def event_collector(event_bus: EventBus) -> EventCollector:
    """Provide an event collector subscribed to the bus."""
    collector = EventCollector()
    event_bus.subscribe(AGENT_OUTPUT, collector.handle_event)
    return collector


@pytest.fixture()
def service(tmp_path: Path, event_bus: EventBus) -> TerminalAgentService:
    """Create a real TerminalAgentService for integration testing."""
    return TerminalAgentService(
        workspace_root=tmp_path,
        llm_service=MockLLMService(),
        event_bus=event_bus,
    )


class TestEventBusIntegration:
    """Test event bus integration with real EventBus instance."""

    def test_real_event_bus_dispatches_to_subscribers(
        self,
        event_bus: EventBus,
        event_collector: EventCollector
    ) -> None:
        """Verify real EventBus correctly dispatches to subscribers."""
        from src.aura.models.events import Event
        from datetime import datetime

        # Dispatch a test event
        event = Event(
            event_type=AGENT_OUTPUT,
            payload={
                "task_id": "test-123",
                "text": "Test output",
                "timestamp": datetime.now().isoformat()
            }
        )

        event_bus.dispatch(event)
        QCoreApplication.processEvents()

        # Verify collector received it
        assert len(event_collector.events) == 1
        assert event_collector.events[0].event_type == AGENT_OUTPUT
        assert event_collector.events[0].payload["task_id"] == "test-123"

    def test_multiple_subscribers_receive_same_event(
        self,
        event_bus: EventBus
    ) -> None:
        """Verify multiple subscribers all receive the same event."""
        from src.aura.models.events import Event
        from datetime import datetime

        collector1 = EventCollector()
        collector2 = EventCollector()

        event_bus.subscribe(AGENT_OUTPUT, collector1.handle_event)
        event_bus.subscribe(AGENT_OUTPUT, collector2.handle_event)

        event = Event(
            event_type=AGENT_OUTPUT,
            payload={
                "task_id": "test-456",
                "text": "Shared output",
                "timestamp": datetime.now().isoformat()
            }
        )

        event_bus.dispatch(event)
        QCoreApplication.processEvents()

        # Both collectors should receive the event
        assert len(collector1.events) == 1
        assert len(collector2.events) == 1
        assert collector1.events[0].payload["text"] == "Shared output"
        assert collector2.events[0].payload["text"] == "Shared output"


class TestEventCollectorUtilities:
    """Test EventCollector helper methods."""

    def test_wait_for_events_returns_true_when_count_reached(
        self,
        event_bus: EventBus,
        event_collector: EventCollector
    ) -> None:
        """Verify wait_for_events returns True when target count is reached."""
        from src.aura.models.events import Event
        from datetime import datetime
        import threading

        def dispatch_events() -> None:
            """Dispatch events with small delay."""
            time.sleep(0.1)
            for i in range(5):
                event = Event(
                    event_type=AGENT_OUTPUT,
                    payload={
                        "task_id": "test",
                        "text": f"Line {i}",
                        "timestamp": datetime.now().isoformat()
                    }
                )
                event_bus.dispatch(event)
                time.sleep(0.05)

        thread = threading.Thread(target=dispatch_events, daemon=True)
        thread.start()

        # Wait for 3 events (should succeed before timeout)
        result = event_collector.wait_for_events(3, timeout=5.0)
        assert result is True
        assert len(event_collector.get_agent_output_events()) >= 3

        thread.join(timeout=1.0)

    def test_wait_for_events_returns_false_on_timeout(
        self,
        event_collector: EventCollector
    ) -> None:
        """Verify wait_for_events returns False when timeout is reached."""
        # No events will be dispatched
        result = event_collector.wait_for_events(10, timeout=0.2)
        assert result is False

    def test_get_events_for_task_filters_correctly(
        self,
        event_bus: EventBus,
        event_collector: EventCollector
    ) -> None:
        """Verify get_events_for_task filters by task ID."""
        from src.aura.models.events import Event
        from datetime import datetime

        # Dispatch events for different tasks
        for task_id in ["task-1", "task-2", "task-1", "task-3", "task-1"]:
            event = Event(
                event_type=AGENT_OUTPUT,
                payload={
                    "task_id": task_id,
                    "text": f"Output from {task_id}",
                    "timestamp": datetime.now().isoformat()
                }
            )
            event_bus.dispatch(event)
            QCoreApplication.processEvents()

        # Verify filtering
        task1_events = event_collector.get_events_for_task("task-1")
        assert len(task1_events) == 3

        task2_events = event_collector.get_events_for_task("task-2")
        assert len(task2_events) == 1

        task3_events = event_collector.get_events_for_task("task-3")
        assert len(task3_events) == 1


class TestEndToEndOutputFlow:
    """Test the complete flow from monitoring thread to event subscribers."""

    def test_monitoring_thread_events_reach_subscribers(
        self,
        service: TerminalAgentService,
        event_collector: EventCollector,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that events from monitoring thread reach EventBus subscribers."""
        import sys
        from unittest.mock import MagicMock
        from src.aura.models.agent_task import TerminalSession
        import threading

        monkeypatch.setattr(sys, 'platform', 'win32')

        # Create mock process with output
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        mock_process.stdout.readline.side_effect = [
            "Line 1\n",
            "Line 2\n",
            "Line 3\n",
            ""
        ]

        session = TerminalSession(
            task_id="integration-test",
            command=["claude"],
            spec_path=str(tmp_path / "spec.md"),
            child=mock_process
        )

        log_path = tmp_path / ".aura" / "integration-test.output.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch()

        # Start monitoring
        thread = threading.Thread(
            target=service._monitor_pty_output,  # noqa: SLF001
            args=(session, log_path),
            daemon=True
        )
        thread.start()

        # Wait for events
        event_collector.wait_for_events(3, timeout=2.0)
        thread.join(timeout=1.0)

        # Verify events were received by subscriber
        events = event_collector.get_events_for_task("integration-test")
        assert len(events) >= 3

        # Verify content
        texts = [e.payload["text"] for e in events]
        assert "Line 1" in texts[0]
        assert "Line 2" in texts[1]
        assert "Line 3" in texts[2]

    def test_multiple_concurrent_sessions_events_separated(
        self,
        service: TerminalAgentService,
        event_collector: EventCollector,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that events from multiple sessions are properly tagged."""
        import sys
        from unittest.mock import MagicMock
        from src.aura.models.agent_task import TerminalSession
        import threading

        monkeypatch.setattr(sys, 'platform', 'win32')

        # Create two mock processes
        mock_process1 = MagicMock()
        mock_process1.poll.return_value = 0
        mock_process1.stdout.readline.side_effect = [
            "Session 1 Line 1\n",
            "Session 1 Line 2\n",
            ""
        ]

        mock_process2 = MagicMock()
        mock_process2.poll.return_value = 0
        mock_process2.stdout.readline.side_effect = [
            "Session 2 Line 1\n",
            "Session 2 Line 2\n",
            ""
        ]

        session1 = TerminalSession(
            task_id="task-1",
            command=["claude"],
            spec_path=str(tmp_path / "spec1.md"),
            child=mock_process1
        )

        session2 = TerminalSession(
            task_id="task-2",
            command=["claude"],
            spec_path=str(tmp_path / "spec2.md"),
            child=mock_process2
        )

        log1 = tmp_path / ".aura" / "task-1.output.log"
        log1.parent.mkdir(parents=True, exist_ok=True)
        log1.touch()

        log2 = tmp_path / ".aura" / "task-2.output.log"
        log2.touch()

        # Start both monitoring threads
        thread1 = threading.Thread(
            target=service._monitor_pty_output,  # noqa: SLF001
            args=(session1, log1),
            daemon=True
        )
        thread2 = threading.Thread(
            target=service._monitor_pty_output,  # noqa: SLF001
            args=(session2, log2),
            daemon=True
        )

        thread1.start()
        thread2.start()

        # Wait for events
        event_collector.wait_for_events(4, timeout=2.0)

        thread1.join(timeout=1.0)
        thread2.join(timeout=1.0)

        # Verify events are properly separated
        task1_events = event_collector.get_events_for_task("task-1")
        task2_events = event_collector.get_events_for_task("task-2")

        assert len(task1_events) >= 2
        assert len(task2_events) >= 2

        # Verify content is correct
        task1_texts = [e.payload["text"] for e in task1_events]
        task2_texts = [e.payload["text"] for e in task2_events]

        assert any("Session 1" in text for text in task1_texts)
        assert any("Session 2" in text for text in task2_texts)
