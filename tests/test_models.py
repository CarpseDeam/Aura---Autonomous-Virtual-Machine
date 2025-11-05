from __future__ import annotations

import time
from typing import Callable, Optional
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from src.aura.models.agent_task import AgentSpecification, TaskSummary, TerminalSession


def test_agent_specification_serialization_includes_metadata(
    agent_spec_factory: Callable[..., AgentSpecification],
) -> None:
    spec = agent_spec_factory(metadata={"priority": "high"})
    serialized = spec.model_dump()

    assert serialized["task_id"].startswith("task-")
    assert serialized["metadata"]["priority"] == "high"
    assert serialized["files_to_watch"] == ["src/app.py"]


def test_agent_specification_invalid_files_to_watch_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        AgentSpecification(
            task_id="invalid",
            request="Do work",
            prompt="Prompt",
            files_to_watch="not-a-list",  # type: ignore[arg-type]
        )


def test_terminal_session_is_alive_transitions_when_child_exits() -> None:
    session = TerminalSession(task_id="s1", command=["cmd"], spec_path="spec.md")
    child = MagicMock()
    state = {"alive": True}

    def _poll_side_effect() -> int | None:
        if state["alive"]:
            state["alive"] = False
            return None
        return 0

    child.poll.side_effect = _poll_side_effect
    session.child = child

    assert session.is_alive() is True
    assert session.is_alive() is False
    assert session.poll() == 0


def test_terminal_session_wait_timeout_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    session = TerminalSession(task_id="s2", command=["cmd"], spec_path="spec.md")
    child = MagicMock()
    child.poll.return_value = None
    session.child = child

    monkeypatch.setattr(time, "sleep", lambda _: None)

    assert session.wait(timeout=0.05) is None


def test_task_summary_short_outcome_formats_counts() -> None:
    summary = TaskSummary(
        status="completed",
        files_created=["a.py", "b.py"],
        files_modified=["c.py"],
        files_deleted=[],
    )

    assert summary.short_outcome() == "completed: created 2, modified 1"


def test_task_summary_short_outcome_handles_no_changes() -> None:
    summary = TaskSummary(status="partial")
    assert summary.short_outcome() == "partial: no file changes"


def test_terminal_session_is_alive_handles_child_exception() -> None:
    session = TerminalSession(task_id="s3", command=["cmd"], spec_path="spec.md")

    class FaultyChild:
        def poll(self) -> None:
            raise RuntimeError("poll failed")

    session.child = FaultyChild()
    assert session.is_alive() is False


def test_terminal_session_poll_returns_none_when_child_active() -> None:
    session = TerminalSession(task_id="s4", command=["cmd"], spec_path="spec.md")
    child = MagicMock()
    child.poll.return_value = None
    session.child = child

    assert session.poll() is None


def test_terminal_session_wait_without_timeout_calls_child_wait() -> None:
    session = TerminalSession(task_id="s5", command=["cmd"], spec_path="spec.md")
    child = MagicMock()
    child.poll.return_value = 0
    session.child = child

    exit_code = session.wait()

    child.wait.assert_called_once()
    assert exit_code == 0


def test_capture_exit_code_uses_pexpect_status() -> None:
    session = TerminalSession(task_id="s6", command=["cmd"], spec_path="spec.md")

    class PexpectStub:
        exitstatus: Optional[int] = None
        status: int = 3

    session.child = PexpectStub()
    assert session._capture_exit_code() == 3


def test_task_summary_short_outcome_includes_deletions() -> None:
    summary = TaskSummary(status="failed", files_deleted=["obsolete.py"])
    assert summary.short_outcome() == "failed: deleted 1"


def test_terminal_session_is_alive_returns_false_without_child() -> None:
    session = TerminalSession(task_id="s7", command=["cmd"], spec_path="spec.md")
    assert session.is_alive() is False


def test_terminal_session_is_alive_uses_isalive() -> None:
    session = TerminalSession(task_id="s8", command=["cmd"], spec_path="spec.md")

    class PexpectChild:
        def isalive(self) -> bool:
            return True

    session.child = PexpectChild()
    assert session.is_alive() is True


def test_terminal_session_wait_returns_cached_exit_when_no_child() -> None:
    session = TerminalSession(task_id="s9", command=["cmd"], spec_path="spec.md")
    session.mark_exit(5)
    assert session.wait() == 5


def test_terminal_session_wait_handles_pexpect_child(monkeypatch: pytest.MonkeyPatch) -> None:
    session = TerminalSession(task_id="s10", command=["cmd"], spec_path="spec.md")

    class PexpectChild:
        def __init__(self) -> None:
            self.exitstatus: Optional[int] = None
            self.status: int = 4

        def wait(self) -> None:
            self.status = 6

    child = PexpectChild()
    session.child = child
    monkeypatch.setattr("time.sleep", lambda _value: None)

    assert session.wait(timeout=None) == 6


def test_terminal_session_mark_exit_and_capture_without_child() -> None:
    session = TerminalSession(task_id="s11", command=["cmd"], spec_path="spec.md")
    session.mark_exit(2)
    assert session._capture_exit_code() == 2
