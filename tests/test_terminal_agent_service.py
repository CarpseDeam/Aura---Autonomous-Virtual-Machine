from __future__ import annotations

import shlex
from pathlib import Path
from typing import Callable, List
from unittest.mock import MagicMock

import pytest

from src.aura.models.event_types import (
    AGENT_OUTPUT,
    TERMINAL_EXECUTE_COMMAND,
    TERMINAL_OUTPUT_RECEIVED,
)
from src.aura.models.events import Event
from src.aura.models.agent_task import AgentSpecification, TerminalSession
from src.aura.services.terminal_bridge import TerminalBridge
from tests.conftest import RecordingEventBus, TerminalServiceHarness


def test_build_command_default_includes_json_flag(
    terminal_service_factory: Callable[..., TerminalServiceHarness],
    agent_spec_factory: Callable[..., AgentSpecification],
) -> None:
    harness: TerminalServiceHarness = terminal_service_factory()
    tokens: List[str] = harness.service._build_command(agent_spec_factory(), command_override=None)

    assert "-p" in tokens
    assert "--output-format" in tokens
    assert "json" in tokens
    assert "--yolo" in tokens


def test_build_command_override_preserves_tokens(
    terminal_service_factory: Callable[..., TerminalServiceHarness],
    agent_spec_factory: Callable[..., AgentSpecification],
) -> None:
    harness: TerminalServiceHarness = terminal_service_factory()
    override: List[str] = ["python", "-m", "aura.cli"]
    tokens: List[str] = harness.service._build_command(agent_spec_factory(), command_override=override)

    assert tokens == override


def test_spawn_agent_session_flow_records_command_dispatch(
    terminal_service_factory: Callable[..., TerminalServiceHarness],
    agent_spec_factory: Callable[..., AgentSpecification],
) -> None:
    harness: TerminalServiceHarness = terminal_service_factory()
    spec = agent_spec_factory(project_name="flow-project")

    session = harness.service.spawn_agent(spec)

    harness.bridge.start_session.assert_called_once()
    _args, kwargs = harness.bridge.start_session.call_args
    env_kwargs = kwargs.get("environment") or {}
    assert env_kwargs.get("AURA_AGENT_TASK_ID") == spec.task_id
    command_events = [event for event in harness.event_bus.dispatched if event.event_type == TERMINAL_EXECUTE_COMMAND]
    assert command_events, "Expected TERMINAL_EXECUTE_COMMAND dispatch"

    command_payload = command_events[0].payload
    assert command_payload["task_id"] == spec.task_id
    assert Path(command_payload["gemini_md_path"]).name == "GEMINI.md"
    assert session.command[0] == "gemini"
    assert session.log_path is not None
    assert spec.task_id in harness.service._sessions


@pytest.mark.parametrize(
    ("text_payload", "should_emit"),
    [
        ("Step 1 complete", True),
        ("   ", False),
    ],
)
def test_handle_terminal_output_emission_rules(
    terminal_service_factory: Callable[..., TerminalServiceHarness],
    terminal_session_factory: Callable[..., TerminalSession],
    text_payload: str,
    should_emit: bool,
) -> None:
    harness: TerminalServiceHarness = terminal_service_factory()
    session = terminal_session_factory(task_id="stream-task")
    harness.service._sessions[session.task_id] = session

    event = Event(
        event_type=TERMINAL_OUTPUT_RECEIVED,
        payload={"task_id": session.task_id, "text": text_payload, "timestamp": "2025-01-01T00:00:00Z"},
    )
    harness.service._handle_terminal_output(event)

    output_events = [evt for evt in harness.event_bus.dispatched if evt.event_type == AGENT_OUTPUT]
    assert bool(output_events) is should_emit


def test_spawn_agent_start_session_failure_raises(
    terminal_service_factory: Callable[..., TerminalServiceHarness],
    agent_spec_factory: Callable[..., AgentSpecification],
) -> None:
    bridge = MagicMock(spec=TerminalBridge)
    bridge.start.return_value = None
    bridge.start_session.side_effect = TimeoutError("start timed out")
    bridge.end_session.return_value = None

    harness: TerminalServiceHarness = terminal_service_factory(bridge=bridge)

    with pytest.raises(TimeoutError):
        harness.service.spawn_agent(agent_spec_factory())

    command_events = [event for event in harness.event_bus.dispatched if event.event_type == TERMINAL_EXECUTE_COMMAND]
    assert not command_events


def test_spawn_agent_dispatch_failure_triggers_cleanup(
    terminal_service_factory: Callable[..., TerminalServiceHarness],
    agent_spec_factory: Callable[..., AgentSpecification],
) -> None:
    class FailingEventBus(RecordingEventBus):
        def dispatch(self, event: Event) -> None:
            if event.event_type == TERMINAL_EXECUTE_COMMAND:
                raise RuntimeError("dispatch failed")
            super().dispatch(event)

    failing_bus = FailingEventBus()
    harness: TerminalServiceHarness = terminal_service_factory(event_bus=failing_bus)
    harness.bridge.end_session.return_value = None

    with pytest.raises(RuntimeError, match="dispatch failed"):
        harness.service.spawn_agent(agent_spec_factory())

    harness.bridge.end_session.assert_called_once()


def test_spawn_agent_requires_task_id(
    terminal_service_factory: Callable[..., TerminalServiceHarness],
    agent_spec_factory: Callable[..., AgentSpecification],
) -> None:
    harness: TerminalServiceHarness = terminal_service_factory()
    spec = agent_spec_factory(task_id="")

    with pytest.raises(ValueError):
        harness.service.spawn_agent(spec)

    harness.bridge.start_session.assert_not_called()


def test_render_template_command_raises_on_unknown_placeholder(
    terminal_service_factory: Callable[..., TerminalServiceHarness],
    agent_spec_factory: Callable[..., AgentSpecification],
) -> None:
    harness: TerminalServiceHarness = terminal_service_factory()
    harness.service.agent_command_template = "cmd {unknown}"

    with pytest.raises(RuntimeError, match="Unknown placeholder"):
        harness.service._render_template_command(agent_spec_factory())


def test_render_template_command_rejects_empty_template(
    terminal_service_factory: Callable[..., TerminalServiceHarness],
    agent_spec_factory: Callable[..., AgentSpecification],
) -> None:
    harness: TerminalServiceHarness = terminal_service_factory()
    harness.service.agent_command_template = "   "

    with pytest.raises(ValueError):
        harness.service._render_template_command(agent_spec_factory())


def test_write_gemini_md_wraps_os_error(
    terminal_service_factory: Callable[..., TerminalServiceHarness],
    agent_spec_factory: Callable[..., AgentSpecification],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness: TerminalServiceHarness = terminal_service_factory()
    project_root = harness.service.workspace_root / "omega"
    project_root.mkdir(parents=True, exist_ok=True)
    spec = agent_spec_factory()

    def _fail_write(self: Path, *_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", _fail_write)

    with pytest.raises(RuntimeError, match="Failed to write GEMINI.md"):
        harness.service._write_gemini_md(project_root, "content", spec)


def test_compose_terminal_command_non_windows_shell(
    terminal_service_factory: Callable[..., TerminalServiceHarness],
    agent_spec_factory: Callable[..., AgentSpecification],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness: TerminalServiceHarness = terminal_service_factory()
    spec = agent_spec_factory()
    tokens = harness.service._build_command(spec, command_override=None)

    monkeypatch.setattr("src.aura.services.terminal_agent_service.sys.platform", "linux")
    command = harness.service._compose_terminal_command(
        tokens,
        project_root=harness.service.workspace_root,
        environment={"A": "1"},
    )

    assert command.startswith("gemini")
    assert "cd " not in command
    assert "export " not in command
    assert shlex.split(command) == tokens
