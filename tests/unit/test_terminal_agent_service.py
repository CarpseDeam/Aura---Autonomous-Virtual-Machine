from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

from src.aura.models.agent_task import AgentSpecification
from src.aura.models.event_types import TERMINAL_EXECUTE_COMMAND
from src.aura.models.events import Event
from src.aura.services.terminal_agent_service import TerminalAgentService
from src.aura.services.user_settings_manager import DEFAULT_GEMINI_MODEL


class FakeLLMService:
    """Minimal stub to satisfy TerminalAgentService dependencies."""


class FakeTerminalBridge:
    """Stub terminal bridge that records session lifecycle activity."""

    def __init__(self) -> None:
        self.started = False
        self.sessions: List[tuple[str, Path, Optional[Path], Optional[Dict[str, str]]]] = []
        self.ended = False

    def start(self) -> None:
        self.started = True

    def start_session(
        self,
        task_id: str,
        log_path: Path,
        working_dir: Optional[Path] = None,
        environment: Optional[Dict[str, str]] = None,
    ) -> None:
        self.sessions.append((task_id, Path(log_path), working_dir, environment))

    def end_session(self) -> None:
        self.ended = True


class FakeEventBus:
    """Synchronous event bus implementation for unit testing."""

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable[[Event], None]]] = {}
        self.dispatched: List[Event] = []

    def subscribe(self, event_type: str, callback: Callable[[Event], None]) -> None:
        self._subscribers.setdefault(event_type, []).append(callback)

    def dispatch(self, event: Event) -> None:
        self.dispatched.append(event)
        for callback in self._subscribers.get(event.event_type, []):
            callback(event)


class FakeSettingsManager:
    """Lightweight stub exposing the Gemini model accessor expected by the service."""

    def __init__(self, model: str = DEFAULT_GEMINI_MODEL) -> None:
        self._model = model

    def get_gemini_model(self) -> str:
        return self._model


def test_spawn_agent_dispatches_terminal_command(tmp_path: Path) -> None:
    bridge = FakeTerminalBridge()
    bus = FakeEventBus()
    settings_manager = FakeSettingsManager()
    service = TerminalAgentService(
        workspace_root=tmp_path,
        llm_service=FakeLLMService(),
        event_bus=bus,
        terminal_bridge=bridge,
        agent_command_template="gemini",
        settings_manager=settings_manager,
    )

    spec = AgentSpecification(
        task_id="task123",
        request="Implement terminal support",
        project_name="demo_project",
        prompt="Do the work",
    )

    session = service.spawn_agent(spec)

    assert bridge.started
    assert bridge.sessions
    recorded_task, log_path, working_dir, environment = bridge.sessions[0]
    assert recorded_task == "task123"
    assert log_path.exists()
    assert working_dir == tmp_path / "demo_project"
    assert environment is not None
    assert environment["AURA_AGENT_TASK_ID"] == "task123"
    assert environment["PYTHONUNBUFFERED"] == "1"

    spec_file = tmp_path / ".aura" / "task123.md"
    prompt_file = tmp_path / ".aura" / "task123.prompt.txt"
    gemini_md = tmp_path / "demo_project" / "GEMINI.md"
    assert spec_file.exists()
    assert prompt_file.exists()
    assert gemini_md.exists()

    command_event = next(
        event for event in bus.dispatched if event.event_type == TERMINAL_EXECUTE_COMMAND
    )
    assert command_event.payload["gemini_md_path"].endswith("GEMINI.md")
    command = command_event.payload["command"]

    assert command.strip().startswith("gemini")
    assert "--model" in command
    assert "--output-format" in command
    assert "--yolo" in command
    assert "Set-Location" not in command
    assert "$env:" not in command

    if not sys.platform.startswith("win"):
        assert command.startswith("gemini")
        assert "export " not in command
        assert " && " not in command

    assert session.command[0] == "gemini"
    assert session.command[1] == "--model"
    assert session.command[2] == DEFAULT_GEMINI_MODEL
    assert session.command[3] == "-p"
    assert "GEMINI.md" in session.command[4]
    assert session.command[5] == "--output-format"
    assert session.command[6] == "json"
    assert session.command[7] == "--yolo"


def test_spawn_agent_respects_configured_gemini_model(tmp_path: Path) -> None:
    bridge = FakeTerminalBridge()
    bus = FakeEventBus()
    settings_manager = FakeSettingsManager(model="gemini-2.5-flash")
    service = TerminalAgentService(
        workspace_root=tmp_path,
        llm_service=FakeLLMService(),
        event_bus=bus,
        terminal_bridge=bridge,
        agent_command_template="gemini",
        settings_manager=settings_manager,
    )

    spec = AgentSpecification(
        task_id="task456",
        request="Implement terminal support",
        project_name="demo_project",
        prompt="Do the work",
    )

    session = service.spawn_agent(spec)

    assert session.command[0] == "gemini"
    assert session.command[1] == "--model"
    assert session.command[2] == "gemini-2.5-flash"


def test_build_session_environment_enables_unbuffered_output(tmp_path: Path) -> None:
    bridge = FakeTerminalBridge()
    bus = FakeEventBus()
    settings_manager = FakeSettingsManager()
    service = TerminalAgentService(
        workspace_root=tmp_path,
        llm_service=FakeLLMService(),
        event_bus=bus,
        terminal_bridge=bridge,
        agent_command_template="gemini",
        settings_manager=settings_manager,
    )

    spec_path = tmp_path / ".aura" / "env-check.md"
    env_map = service._build_session_environment(spec_path, "task789", {})

    assert env_map["PYTHONUNBUFFERED"] == "1"

    overridden = service._build_session_environment(spec_path, "task789", {"PYTHONUNBUFFERED": "0"})
    assert overridden["PYTHONUNBUFFERED"] == "0"
