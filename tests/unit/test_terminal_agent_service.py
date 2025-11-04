from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
import sys

import pytest

from src.aura.models.agent_task import TerminalSession
from src.aura.services.terminal_agent_service import TerminalAgentService


class DummyExpectModule:
    TIMEOUT = TimeoutError
    EOF = EOFError

    @staticmethod
    def spawn(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - should not be invoked in unit tests
        raise AssertionError("spawn should not be called in these unit tests")


class DummyLLM:
    def __init__(self, response: str = "Use a function") -> None:
        self.response = response
        self.calls: list[str] = []

    def run_for_agent(self, agent_name: str, prompt: str) -> str:
        self.calls.append(prompt)
        return self.response


class DummyEventBus:
    def __init__(self) -> None:
        self.dispatched: list[Any] = []

    def dispatch(self, event: Any) -> None:
        self.dispatched.append(event)


@pytest.fixture()
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TerminalAgentService:
    monkeypatch.setattr(TerminalAgentService, "_load_expect_module", lambda self: DummyExpectModule)
    return TerminalAgentService(workspace_root=tmp_path, llm_service=DummyLLM(), event_bus=DummyEventBus())


def test_ensure_claude_flags_appends_skip_permissions(service: TerminalAgentService) -> None:
    tokens = service._ensure_claude_flags(["claude"])  # noqa: SLF001
    assert "--dangerously-skip-permissions" in tokens


def test_detect_question_matches_keywords(service: TerminalAgentService) -> None:
    assert service._detect_question("Should I use a class?")  # noqa: SLF001
    assert service._detect_question("Which option should I pick: 1 or 2?")  # noqa: SLF001
    assert service._detect_question("Confirm deployment? [y/N]")  # noqa: SLF001
    assert service._detect_question("Approve changes (y/n)")  # noqa: SLF001
    assert service._detect_question("Would you like to continue?")  # noqa: SLF001
    assert service._detect_question("verify output please")  # noqa: SLF001
    assert service._detect_question("approve update")  # noqa: SLF001
    assert service._detect_question("choose between plan A and plan B")  # noqa: SLF001
    assert service._detect_question("should i refactor?")  # noqa: SLF001
    assert service._detect_question("Confirm?")  # noqa: SLF001
    assert not service._detect_question("No question here.")  # noqa: SLF001


def test_handle_agent_question_prevents_duplicates(service: TerminalAgentService) -> None:
    session = TerminalSession(task_id="task123", command=["claude"], spec_path="spec.md")

    sent: list[str] = []
    service._send_to_agent = lambda s, message: sent.append(message)  # type: ignore[assignment]  # noqa: SLF001
    service._generate_answer = lambda s, q: "Use a function."  # type: ignore[assignment]  # noqa: SLF001

    service._handle_agent_question(session, "Should I use a function?")  # noqa: SLF001
    service._handle_agent_question(session, "Should I use a function?")  # noqa: SLF001

    assert sent == ["Use a function."]


def test_spawn_with_pty_windows_uses_headless_popen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    captured: dict[str, Any] = {}

    class MockWindowsPTY:
        TIMEOUT = TimeoutError
        EOF = EOFError

        @staticmethod
        def spawn(command: str, args: list[str], **kwargs: Any) -> Any:
            captured["command"] = command
            captured["args"] = args
            captured["kwargs"] = kwargs

            class DummyProcess:
                pid = 1234

            return DummyProcess()

    monkeypatch.setattr(TerminalAgentService, "_load_expect_module", lambda _self: MockWindowsPTY())

    service = TerminalAgentService(workspace_root=tmp_path, llm_service=DummyLLM(), event_bus=DummyEventBus())

    process = service._spawn_with_pty(["claude", "--flag"], tmp_path, {"PATH": "value"})  # noqa: SLF001

    assert process.pid == 1234
    assert captured["command"] == "claude"
    assert captured["args"] == ["--flag"]

    kwargs = captured["kwargs"]
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["env"] == {"PATH": "value"}
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["timeout"] == service._READ_TIMEOUT_SECONDS  # noqa: SLF001


def test_build_command_uses_interactive_mode_on_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    claude_dir = tmp_path / "npm" / "node_modules" / "@anthropic-ai" / "claude-code"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "cli.js").write_text("// dummy cli", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(TerminalAgentService, "_load_expect_module", lambda _self: DummyExpectModule)

    service = TerminalAgentService(workspace_root=tmp_path, llm_service=DummyLLM(), event_bus=DummyEventBus())

    from src.aura.models.agent_task import AgentSpecification

    spec = AgentSpecification(
        task_id="test-task",
        request="Test request",
        prompt="Test prompt",
    )

    prompt_content = "This is the AGENTS.md content for the task"
    with caplog.at_level(logging.INFO):
        command = service._build_command(spec, None, prompt_content)  # noqa: SLF001

    assert command[0].lower().endswith("powershell.exe")
    assert "-NoProfile" in command
    assert len(command) == 4

    powershell_command = command[3]
    assert "-p $input" in powershell_command
    assert "--dangerously-skip-permissions" in powershell_command
    assert "claude.cmd" in powershell_command
    assert "Get-Content" in powershell_command
    assert prompt_content not in powershell_command

    prompt_file = service.spec_dir / "test-task.prompt.txt"
    assert prompt_file.exists()
    assert prompt_file.read_text(encoding="utf-8") == prompt_content
    assert str(prompt_file) in powershell_command

    assert any(
        "Built Windows command for task test-task using PowerShell prompt injection" in record.getMessage()
        for record in caplog.records
    )


def test_build_command_no_print_mode_on_non_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(TerminalAgentService, "_load_expect_module", lambda _self: DummyExpectModule)

    service = TerminalAgentService(workspace_root=tmp_path, llm_service=DummyLLM(), event_bus=DummyEventBus())

    from src.aura.models.agent_task import AgentSpecification

    spec = AgentSpecification(
        task_id="test-task",
        request="Test request",
        prompt="Test prompt",
    )

    prompt_content = "This is the AGENTS.md content for the task"
    command = service._build_command(spec, None, prompt_content)  # noqa: SLF001

    assert "-p" not in command
    assert prompt_content not in command


def test_event_bus_parameter_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify TerminalAgentService requires event_bus parameter."""
    monkeypatch.setattr(TerminalAgentService, "_load_expect_module", lambda _self: DummyExpectModule)

    # Should work with event_bus
    service = TerminalAgentService(
        workspace_root=tmp_path,
        llm_service=DummyLLM(),
        event_bus=DummyEventBus()
    )
    assert service.event_bus is not None


def test_monitor_thread_has_access_to_event_bus(service: TerminalAgentService) -> None:
    """Verify monitoring thread can access event bus."""
    assert hasattr(service, 'event_bus')
    assert service.event_bus is not None
