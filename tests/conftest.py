from __future__ import annotations

from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Callable, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from src.aura.models.agent_task import AgentSpecification, TerminalSession
from src.aura.models.events import Event
from src.aura.services.terminal_agent_service import TerminalAgentService
from src.aura.services.terminal_bridge import TerminalBridge


class RecordingEventBus:
    """Synchronous in-memory event bus used by tests."""

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable[[Event], None]]] = {}
        self.dispatched: List[Event] = []

    def subscribe(self, event_type: str, callback: Callable[[Event], None]) -> None:
        self._subscribers.setdefault(event_type, []).append(callback)

    def dispatch(self, event: Event) -> None:
        self.dispatched.append(event)
        for callback in self._subscribers.get(event.event_type, []):
            callback(event)


_task_counter = count(1)


@pytest.fixture
def agent_spec_factory() -> Callable[..., AgentSpecification]:
    """Factory fixture that produces AgentSpecification instances with sensible defaults."""

    def _factory(**overrides: object) -> AgentSpecification:
        task_suffix = next(_task_counter)
        defaults: Dict[str, object] = {
            "task_id": f"task-{task_suffix}",
            "request": "Implement feature X",
            "project_name": "demo-project",
            "prompt": "# Task\n- Do something useful",
            "files_to_watch": ["src/app.py"],
            "blueprint": {"files": [{"file_path": "src/app.py"}]},
        }
        defaults.update(overrides)
        return AgentSpecification(**defaults)

    return _factory


@pytest.fixture
def terminal_session_factory() -> Callable[..., TerminalSession]:
    """Factory fixture for TerminalSession objects."""

    def _factory(**overrides: object) -> TerminalSession:
        task_suffix = next(_task_counter)
        defaults: Dict[str, object] = {
            "task_id": overrides.get("task_id", f"session-{task_suffix}"),
            "command": ["gemini", "--help"],
            "spec_path": "/tmp/spec.md",
            "log_path": "/tmp/task.log",
        }
        defaults.update(overrides)
        return TerminalSession(**defaults)

    return _factory


@dataclass
class TerminalServiceHarness:
    service: TerminalAgentService
    event_bus: RecordingEventBus
    bridge: MagicMock
    settings_manager: MagicMock


@pytest.fixture
def terminal_service_factory(tmp_path_factory: pytest.TempPathFactory) -> Callable[..., TerminalServiceHarness]:
    """Factory that constructs TerminalAgentService instances with mocked dependencies."""

    def _factory(
        *,
        event_bus: Optional[RecordingEventBus] = None,
        bridge: Optional[MagicMock] = None,
        model_name: str = "gemini-2.5-pro",
    ) -> TerminalServiceHarness:
        workspace_root = tmp_path_factory.mktemp("workspace")

        settings_manager = MagicMock()
        settings_manager.get_gemini_model.return_value = model_name

        event_bus_instance = event_bus or RecordingEventBus()
        bridge_instance = bridge or MagicMock(spec=TerminalBridge)
        bridge_instance.start.return_value = None

        service = TerminalAgentService(
            workspace_root=Path(workspace_root),
            llm_service=MagicMock(),
            event_bus=event_bus_instance,
            terminal_bridge=bridge_instance,
            agent_command_template="gemini",
            settings_manager=settings_manager,
        )

        return TerminalServiceHarness(
            service=service,
            event_bus=event_bus_instance,
            bridge=bridge_instance,
            settings_manager=settings_manager,
        )

    return _factory
