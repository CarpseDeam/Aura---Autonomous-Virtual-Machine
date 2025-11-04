from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Deque, List, Optional, Tuple
import sys

import pytest

from src.aura.models.agent_task import AgentSpecification
from src.aura.services.terminal_agent_service import TerminalAgentService


ScriptEvent = Tuple[str, Optional[str]]


class ScriptedChild:
    """Deterministic PTY stub that replays scripted output back to the service."""

    def __init__(
        self,
        script: Deque[ScriptEvent],
        expect_module: Any,
        sent_messages: List[Tuple[str, str]],
    ) -> None:
        self._script = script
        self._expect = expect_module
        self._sent_messages = sent_messages
        self.logfile_read = None
        self.delaybeforesend = 0.0
        self.exitstatus = 0
        self.status = 0
        self._closed = False

    def readline(self) -> str:
        if not self._script:
            raise self._expect.EOF()
        kind, payload = self._script.popleft()
        if kind == "timeout":
            raise self._expect.TIMEOUT()
        if kind == "line" and payload is not None:
            if self.logfile_read is not None:
                self.logfile_read.write(payload + "\n")
            return payload + "\n"
        if kind == "eof":
            raise self._expect.EOF()
        raise AssertionError(f"Unknown script event: {kind}")

    def send(self, data: str) -> None:
        self._sent_messages.append(("send", data))

    def sendline(self, data: str) -> None:
        self._sent_messages.append(("sendline", data))

    def isalive(self) -> bool:
        return not self._closed

    def close(self, force: bool = False) -> None:  # noqa: ARG002 - matches contract
        self._closed = True

    def wait(self) -> None:
        self._closed = True


class ScriptedExpectModule:
    """Minimal pexpect-compatible shim used to drive the monitor thread."""

    class TIMEOUT(Exception):
        pass

    class EOF(Exception):
        pass

    def __init__(self, script: Deque[ScriptEvent], sent_messages: List[Tuple[str, str]]) -> None:
        self._script = script
        self._sent_messages = sent_messages

    def spawn(self, *args: Any, **kwargs: Any) -> ScriptedChild:  # noqa: ANN401 - mirrors pexpect API
        return ScriptedChild(self._script, self, self._sent_messages)


class DummyLLMService:
    """LLM stub that records prompts and yields scripted responses."""

    def __init__(self, responses: List[Any]) -> None:
        self._responses: Deque[Any] = deque(responses)
        self.calls: List[Tuple[str, str]] = []

    def run_for_agent(self, agent_name: str, prompt: str) -> str:
        self.calls.append((agent_name, prompt))
        if not self._responses:
            return ""
        result = self._responses.popleft()
        if isinstance(result, Exception):
            raise result
        return str(result)


def _build_specification(task_id: str) -> AgentSpecification:
    return AgentSpecification(
        task_id=task_id,
        request="Refactor the database layer",
        project_name="demo",
        prompt="Refactor the database layer using the repository pattern.",
    )


def test_terminal_agent_answers_questions_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    script = deque(
        [
            ("line", "Boot complete."),
            ("line", "Should I use async or sync for the ORM?"),
            ("line", "Should I use async or sync for the ORM?"),
            ("line", "Confirm deployment? (y/n)"),
            ("line", "Proceed with deploy?"),
            ("eof", None),
        ]
    )
    sent_messages: List[Tuple[str, str]] = []
    expect_module = ScriptedExpectModule(script, sent_messages)
    monkeypatch.setattr(TerminalAgentService, "_load_expect_module", lambda self: expect_module)

    llm = DummyLLMService(
        [
            "Use async - it matches the FastAPI stack.",
            "",  # Empty response should not reach the agent
            RuntimeError("provider unavailable"),  # Simulate LLM failure
        ]
    )

    service = TerminalAgentService(
        workspace_root=tmp_path,
        llm_service=llm,
        agent_command_template="claude-code",
    )
    spec = _build_specification("test-io-pty-001")

    session = service.spawn_agent(spec)

    monitor = session.monitor_thread
    assert monitor is not None
    monitor.join(timeout=2.0)
    assert not monitor.is_alive()

    prompts = [prompt for _, prompt in llm.calls]
    assert len(prompts) == 3
    assert sum("Should I use async or sync for the ORM?" in prompt for prompt in prompts) == 1
    assert any("Confirm deployment? (y/n)" in prompt for prompt in prompts)
    assert any("Proceed with deploy?" in prompt for prompt in prompts)

    responses = [message for kind, message in sent_messages if kind == "sendline"]
    assert responses == ["Use async - it matches the FastAPI stack."]

    answered = session.answered_questions
    assert answered == {
        "Should I use async or sync for the ORM?",
        "Confirm deployment? (y/n)",
        "Proceed with deploy?",
    }

    initial_prompts = [message for kind, message in sent_messages if kind == "send"]
    assert initial_prompts and initial_prompts[0]
