from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from unittest.mock import MagicMock

import pytest

from src.aura.models.agent_task import AgentSpecification, TaskSummary, TerminalSession
from src.aura.models.event_types import (
    TERMINAL_SESSION_COMPLETED,
    TERMINAL_SESSION_FAILED,
    TERMINAL_SESSION_STARTED,
)
from src.aura.services.agent_supervisor import AgentSupervisor, TaskPlanningResult
from tests.conftest import RecordingEventBus


@dataclass
class SupervisorHarness:
    supervisor: AgentSupervisor
    event_bus: RecordingEventBus
    workspace_root: Path
    llm_service: MagicMock
    terminal_service: MagicMock


class StubSession:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.command = ["cmd"]
        self.spec_path = "spec.md"
        self.log_path: str | None = None
        self.poll = MagicMock(return_value=None)
        self.wait = MagicMock(return_value=None)


@pytest.fixture
def supervisor_factory(tmp_path_factory: pytest.TempPathFactory) -> Callable[..., SupervisorHarness]:
    def _factory() -> SupervisorHarness:
        workspace_root = Path(tmp_path_factory.mktemp("supervisor-workspace"))
        event_bus = RecordingEventBus()
        llm_service = MagicMock()
        terminal_service = MagicMock()
        workspace_service = MagicMock()
        workspace_service.workspace_root = workspace_root

        supervisor = AgentSupervisor(
            llm_service=llm_service,
            terminal_service=terminal_service,
            workspace_service=workspace_service,
            event_bus=event_bus,
        )
        return SupervisorHarness(
            supervisor=supervisor,
            event_bus=event_bus,
            workspace_root=workspace_root,
            llm_service=llm_service,
            terminal_service=terminal_service,
        )

    return _factory


def test_build_specification_combines_condensed_and_original(
    supervisor_factory: Callable[[], SupervisorHarness],
) -> None:
    harness = supervisor_factory()
    result = harness.supervisor._build_specification(
        "abc123",
        "my-project",
        "Create something great",
        "# Task: Implement feature",
    )

    assert isinstance(result, AgentSpecification)
    assert "# Task: Implement feature" in result.prompt
    assert "## Original Request" in result.prompt
    assert "Create something great" in result.prompt
    assert result.metadata["generated_by"] == "AgentSupervisor"


def test_generate_task_plan_produces_plan_event(
    supervisor_factory: Callable[[], SupervisorHarness],
) -> None:
    harness = supervisor_factory()
    harness.llm_service.run_for_agent.return_value = (
        "<detailed_plan>Steps</detailed_plan>\n"
        "<task_spec># Task: Build</task_spec>"
    )

    result = harness.supervisor._generate_task_plan("Ship it")

    assert isinstance(result, TaskPlanningResult)
    assert result.detailed_plan == "Steps"
    assert result.task_spec == "# Task: Build"
    harness.llm_service.run_for_agent.assert_called_once()


@pytest.mark.parametrize(
    ("log_content", "expect_stats"),
    [
        (
            "json{\"stats\": {\"tools\": {\"totalCalls\": 3, \"byName\": {\"write_file\": {\"count\": 2}}},"
            "\"files\": {\"totalLinesAdded\": 10, \"totalLinesRemoved\": 4}, \"extra\": 1}}",
            True,
        ),
        ("plain text without json", False),
    ],
)
def test_parse_cli_stats_handles_formats(
    supervisor_factory: Callable[[], SupervisorHarness],
    log_content: str,
    expect_stats: bool,
) -> None:
    harness = supervisor_factory()
    log_path = harness.workspace_root / ".aura" / "task.output.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(log_content, encoding="utf-8")

    stats = harness.supervisor._parse_cli_stats(log_path)

    if expect_stats:
        assert stats is not None
        assert stats["files_created_count"] == 2
        assert stats["lines_added"] == 10
        assert stats["lines_removed"] == 4
    else:
        assert stats is None


def test_parse_cli_stats_prefers_most_recent_block(
    supervisor_factory: Callable[[], SupervisorHarness],
) -> None:
    harness = supervisor_factory()
    log_path = harness.workspace_root / ".aura" / "latest.output.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "json{\"stats\": {\"files\": {\"totalLinesAdded\": 1}}}\n"
        "json{\"stats\": {\"files\": {\"totalLinesAdded\": 42}}}",
        encoding="utf-8",
    )

    stats = harness.supervisor._parse_cli_stats(log_path)

    assert stats is not None
    assert stats["lines_added"] == 42


def test_load_task_summary_missing_file_returns_placeholder(
    supervisor_factory: Callable[[], SupervisorHarness],
) -> None:
    harness = supervisor_factory()
    result = harness.supervisor._load_task_summary("missing", harness.workspace_root, wait_seconds=0.0)

    assert result["status"] == "unknown"
    assert result["files_created"] == []


def test_finalize_session_success_dispatches_completed_event(
    supervisor_factory: Callable[[], SupervisorHarness],
) -> None:
    harness = supervisor_factory()
    session = TerminalSession(
        task_id="task-finish",
        command=["gemini"],
        spec_path="spec.md",
        log_path=str(harness.workspace_root / ".aura" / "task-finish.output.log"),
    )
    session.mark_exit(0)
    harness.supervisor._sessions[session.task_id] = session

    aura_dir = harness.workspace_root / "demo" / ".aura"
    aura_dir.mkdir(parents=True, exist_ok=True)
    summary = TaskSummary(
        status="completed",
        files_created=["src/app.py"],
        files_modified=["src/utils.py"],
        files_deleted=[],
        errors=[],
        warnings=[],
        suggestions=[],
    )
    summary_path = aura_dir / f"{session.task_id}.summary.json"
    summary_path.write_text(summary.model_dump_json(), encoding="utf-8")
    log_path = aura_dir / f"{session.task_id}.output.log"
    log_path.write_text(
        "json{\"stats\": {\"tools\": {\"totalCalls\": 4, \"byName\": {\"write_file\": {\"count\": 1}}},"
        "\"files\": {\"totalLinesAdded\": 12, \"totalLinesRemoved\": 2}}}",
        encoding="utf-8",
    )

    harness.supervisor._finalize_session(
        session,
        harness.workspace_root / "demo",
        completion_reason="done-file-detected",
        duration_seconds=2.0,
        timed_out=False,
    )

    terminal_events = [event for event in harness.event_bus.dispatched if event.event_type == TERMINAL_SESSION_COMPLETED]
    assert terminal_events, "Expected TERMINAL_SESSION_COMPLETED event"
    payload = terminal_events[0].payload
    assert payload["task_id"] == session.task_id
    assert payload["files_created_count"] == 1
    assert payload["cli_stats"]["lines_added"] == 12
    assert session.task_id not in harness.supervisor._sessions


def test_finalize_session_timeout_dispatches_failure(
    supervisor_factory: Callable[[], SupervisorHarness],
) -> None:
    harness = supervisor_factory()
    session = TerminalSession(
        task_id="task-timeout",
        command=["gemini"],
        spec_path="spec.md",
        log_path=str(harness.workspace_root / ".aura" / "task-timeout.output.log"),
    )
    harness.supervisor._sessions[session.task_id] = session

    aura_dir = harness.workspace_root / "demo" / ".aura"
    aura_dir.mkdir(parents=True, exist_ok=True)
    log_path = aura_dir / f"{session.task_id}.output.log"
    log_path.write_text("plain output", encoding="utf-8")

    harness.supervisor._finalize_session(
        session,
        harness.workspace_root / "demo",
        completion_reason="timeout",
        duration_seconds=600.0,
        timed_out=True,
    )

    failure_events = [event for event in harness.event_bus.dispatched if event.event_type == TERMINAL_SESSION_FAILED]
    assert failure_events, "Expected TERMINAL_SESSION_FAILED event"
    payload = failure_events[0].payload
    assert payload["failure_reason"] == "timeout"
    assert payload["timed_out"] is True


def test_extract_latest_json_block_recovers_nested_structure(
    supervisor_factory: Callable[[], SupervisorHarness],
) -> None:
    harness = supervisor_factory()
    raw_text = (
        "noise\n"
        'json{"outer": {"inner": {"value": 1}}}\n'
        'json{"outer": {"inner": {"value": 2}}}'
    )

    result = harness.supervisor._extract_latest_json_block(raw_text)

    assert result == {"outer": {"inner": {"value": 2}}}


def test_process_message_launches_terminal_session(
    supervisor_factory: Callable[[], SupervisorHarness],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = supervisor_factory()
    monkeypatch.setattr(
        "src.aura.services.agent_supervisor.uuid4",
        lambda: SimpleNamespace(hex="feedfacecafebeef"),
    )
    session = TerminalSession(
        task_id="feedfacecafe",
        command=["gemini", "--model", "x"],
        spec_path="spec.md",
    )
    harness.terminal_service.spawn_agent.return_value = session
    harness.supervisor._start_monitor_thread = MagicMock()
    harness.llm_service.run_for_agent.return_value = (
        "<detailed_plan>Detailed plan</detailed_plan>\n"
        "<task_spec># Task: Build</task_spec>"
    )

    harness.supervisor.process_message("Implement feature", "project-alpha")

    events = {event.event_type for event in harness.event_bus.dispatched}
    assert TERMINAL_SESSION_STARTED in events
    assert harness.supervisor._start_monitor_thread.called
    project_path = harness.workspace_root / "project-alpha"
    assert (project_path / "GEMINI.md").exists()
    assert session.task_id in harness.supervisor._sessions


def test_monitor_output_loop_detects_done_file(
    supervisor_factory: Callable[[], SupervisorHarness],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = supervisor_factory()
    session = StubSession("monitor-task")
    project_path = harness.workspace_root / "proj"
    aura_dir = project_path / ".aura"
    aura_dir.mkdir(parents=True, exist_ok=True)
    done_path = aura_dir / "monitor-task.done"
    done_path.write_text("done", encoding="utf-8")
    harness.supervisor._sessions[session.task_id] = session
    finalize = MagicMock()
    harness.supervisor._finalize_session = finalize
    monkeypatch.setattr("src.aura.services.agent_supervisor.time.sleep", lambda *_: None)

    harness.supervisor._monitor_output_loop(session, project_path)

    finalize.assert_called_once()
    assert finalize.call_args.kwargs["completion_reason"] == "done-file-detected"


def test_monitor_output_loop_handles_monitor_error(
    supervisor_factory: Callable[[], SupervisorHarness],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = supervisor_factory()
    session = StubSession("error-task")
    project_path = harness.workspace_root / "proj-error"
    (project_path / ".aura").mkdir(parents=True, exist_ok=True)
    session.poll.side_effect = RuntimeError("poll failure")
    harness.supervisor._sessions[session.task_id] = session
    monkeypatch.setattr("src.aura.services.agent_supervisor.time.sleep", lambda *_: None)

    harness.supervisor._monitor_output_loop(session, project_path)

    failure_events = [
        event for event in harness.event_bus.dispatched if event.event_type == TERMINAL_SESSION_FAILED
    ]
    assert failure_events
    assert failure_events[0].payload["failure_reason"] == "monitor_error"


def test_finalize_session_waits_for_exit_code_when_running(
    supervisor_factory: Callable[[], SupervisorHarness],
) -> None:
    harness = supervisor_factory()
    session = StubSession("waiting-task")
    session.poll.return_value = None
    session.wait.return_value = 0
    harness.supervisor._sessions[session.task_id] = session
    project_path = harness.workspace_root / "wait-proj"
    aura_dir = project_path / ".aura"
    aura_dir.mkdir(parents=True, exist_ok=True)
    summary_path = aura_dir / "waiting-task.summary.json"
    summary_path.write_text(
        TaskSummary(status="completed").model_dump_json(),
        encoding="utf-8",
    )
    log_path = aura_dir / "waiting-task.output.log"
    log_path.write_text(
        "json{\"stats\": {\"files\": {\"totalLinesAdded\": 4}, \"tools\": {\"byName\": {\"write_file\": {\"count\": 1}}}}}",
        encoding="utf-8",
    )

    harness.supervisor._finalize_session(
        session,
        project_path,
        completion_reason="process-exited",
        duration_seconds=1.0,
        timed_out=False,
    )

    session.wait.assert_called_once_with(timeout=5.0)


def test_start_monitor_thread_invokes_thread_factory(
    supervisor_factory: Callable[[], SupervisorHarness],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = supervisor_factory()
    session = TerminalSession(task_id="thread-task", command=["cmd"], spec_path="spec.md")
    captured_kwargs: dict[str, object] = {}

    class FakeThread:
        def __init__(self, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)

        def start(self) -> None:
            captured_kwargs["started"] = True

    monkeypatch.setattr("src.aura.services.agent_supervisor.threading.Thread", FakeThread)

    harness.supervisor._start_monitor_thread(session, harness.workspace_root / "proj")

    assert captured_kwargs["daemon"] is True
    assert captured_kwargs["started"] is True
