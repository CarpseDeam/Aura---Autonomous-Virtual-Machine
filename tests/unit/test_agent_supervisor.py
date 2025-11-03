from __future__ import annotations

import pytest
from pathlib import Path

from src.aura.models.agent_task import AgentSpecification, TerminalSession
from src.aura.models.event_types import TERMINAL_SESSION_FAILED, TERMINAL_SESSION_STARTED
from src.aura.models.events import Event
from src.aura.services.agent_supervisor import AgentSupervisor


class DummyEventBus:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self._subscribers: dict[str, list] = {}

    def subscribe(self, event_type: str, callback) -> None:
        self._subscribers.setdefault(event_type, []).append(callback)

    def dispatch(self, event: Event) -> None:
        self.events.append(event)
        for callback in self._subscribers.get(event.event_type, []):
            callback(event)


class DummyLLM:
    def __init__(self, response: str = "Generated task", fail: bool = False) -> None:
        self.response = response
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def run_for_agent(self, agent_name: str, prompt: str) -> str:
        self.calls.append((agent_name, prompt))
        if self.fail:
            raise RuntimeError("LLM unavailable")
        return self.response


class DummyTerminalService:
    def __init__(self, session: TerminalSession) -> None:
        self.session = session
        self.calls = 0
        self.received_spec: AgentSpecification | None = None

    def spawn_agent(self, spec: AgentSpecification, **_kwargs) -> TerminalSession:
        self.calls += 1
        self.received_spec = spec
        return self.session


class FailingTerminalService:
    def spawn_agent(self, _spec: AgentSpecification, **_kwargs) -> TerminalSession:
        raise RuntimeError("spawn failed")


class DummyWorkspace:
    def __init__(self, root: Path) -> None:
        self.workspace_root = root


def build_session(task_id: str, spec_path: Path) -> TerminalSession:
    return TerminalSession(
        task_id=task_id,
        command=["claude"],
        spec_path=str(spec_path),
        process_id=12345,
    )


def test_process_message_creates_agents_md_and_spawns_agent(tmp_path: Path) -> None:
    event_bus = DummyEventBus()
    workspace = DummyWorkspace(tmp_path)
    llm = DummyLLM(response="Implement feature X for the user")
    spec_path = tmp_path / ".aura" / "placeholder.md"
    session = build_session("task123", spec_path)
    terminal_service = DummyTerminalService(session)

    supervisor = AgentSupervisor(llm, terminal_service, workspace, event_bus)
    supervisor._start_monitor_thread = lambda *args, **kwargs: None  # type: ignore[attr-defined]

    supervisor.process_message("build something cool", "demo_project")

    agents_md = tmp_path / "demo_project" / "AGENTS.md"
    assert agents_md.exists()
    content = agents_md.read_text()
    assert "Task Description" in content
    assert "Implement feature X for the user" in content

    assert terminal_service.calls == 1
    assert terminal_service.received_spec is not None
    assert terminal_service.received_spec.project_name == "demo_project"
    assert terminal_service.received_spec.request == "build something cool"

    started_events = [event for event in event_bus.events if event.event_type == TERMINAL_SESSION_STARTED]
    assert started_events, "Supervisor should dispatch TERMINAL_SESSION_STARTED"


def test_process_message_uses_user_request_when_llm_fails(tmp_path: Path) -> None:
    event_bus = DummyEventBus()
    workspace = DummyWorkspace(tmp_path)
    llm = DummyLLM(fail=True)
    spec_path = tmp_path / ".aura" / "placeholder.md"
    session = build_session("task456", spec_path)
    terminal_service = DummyTerminalService(session)

    supervisor = AgentSupervisor(llm, terminal_service, workspace, event_bus)
    supervisor._start_monitor_thread = lambda *args, **kwargs: None  # type: ignore[attr-defined]

    supervisor.process_message("refactor the parser", "demo_project")

    assert terminal_service.received_spec is not None
    assert "refactor the parser" in terminal_service.received_spec.prompt


def test_process_message_dispatches_failure_event_on_spawn_error(tmp_path: Path) -> None:
    event_bus = DummyEventBus()
    workspace = DummyWorkspace(tmp_path)
    llm = DummyLLM(response="Do something")
    terminal_service = FailingTerminalService()
    supervisor = AgentSupervisor(llm, terminal_service, workspace, event_bus)

    with pytest.raises(RuntimeError):
        supervisor.process_message("create module", "demo_project")

    failure_events = [event for event in event_bus.events if event.event_type == TERMINAL_SESSION_FAILED]
    assert failure_events, "Supervisor should dispatch TERMINAL_SESSION_FAILED on spawn errors"
    payload = failure_events[0].payload
    assert payload.get("failure_reason") == "spawn_failed"
