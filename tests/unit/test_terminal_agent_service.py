from __future__ import annotations

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


@pytest.fixture()
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TerminalAgentService:
    monkeypatch.setattr(TerminalAgentService, "_load_expect_module", lambda self: DummyExpectModule)
    return TerminalAgentService(workspace_root=tmp_path, llm_service=DummyLLM())


def test_ensure_claude_flags_appends_skip_permissions(service: TerminalAgentService) -> None:
    tokens = service._ensure_claude_flags(["claude-code"])  # noqa: SLF001
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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific behavior")
def test_prepare_spawn_command_wraps_with_pwsh_on_windows(
    service: TerminalAgentService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.aura.services.terminal_agent_service.subprocess.list2cmdline",
        lambda items: " ".join(items),
    )
    monkeypatch.setattr(
        service,
        "_resolve_powershell_executable",
        lambda: r"C:\Program Files\PowerShell\7\pwsh.exe",
    )

    command, kwargs = service._prepare_spawn_command(["codex"])  # noqa: SLF001

    assert command[0].lower().endswith("pwsh.exe")
    assert command[1:3] == ["-NoExit", "-Command"]
    assert command[3].startswith("& codex")
    assert kwargs.get("interact") is True


def test_prepare_spawn_command_returns_original_on_non_windows(
    service: TerminalAgentService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    command, kwargs = service._prepare_spawn_command(["codex", "--flag"])  # noqa: SLF001

    assert command == ["codex", "--flag"]
    assert kwargs == {}


def test_resolve_powershell_executable_errors_when_missing(
    service: TerminalAgentService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.aura.services.terminal_agent_service.shutil.which",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError) as exc:
        service._resolve_powershell_executable()  # noqa: SLF001

    assert "PowerShell 7 (pwsh.exe) is required" in str(exc.value)
