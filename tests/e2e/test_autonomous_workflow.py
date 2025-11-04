"""
End-to-end test for Aura's autonomous agent supervision workflow.

This test simulates the complete user journey:
1. User provides a request to create FastAPI hello world
2. AgentSpecification is created with task details
3. TerminalAgentService spawns Claude Code in PTY
4. PTY monitor thread starts watching output
5. Claude Code asks questions during execution
6. LLM autonomously generates answers
7. Answers are sent back to Claude via PTY
8. Duplicate questions are filtered
9. Session completes with exit code

This proves the "magic" - Aura seamlessly bridges user intent and terminal agents.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Deque, List, Optional, Tuple

import pytest

from src.aura.models.agent_task import AgentSpecification
from src.aura.services.terminal_agent_service import TerminalAgentService


ScriptEvent = Tuple[str, Optional[str]]


class ScriptedChild:
    """Mock PTY child that replays scripted Claude Code output."""

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
        self.pid = 12345
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

    def close(self, force: bool = False) -> None:  # noqa: ARG002
        self._closed = True

    def wait(self) -> None:
        self._closed = True


class ScriptedExpectModule:
    """Mock pexpect/wexpect module for testing."""

    class TIMEOUT(Exception):
        pass

    class EOF(Exception):
        pass

    def __init__(self, script: Deque[ScriptEvent], sent_messages: List[Tuple[str, str]]) -> None:
        self._script = script
        self._sent_messages = sent_messages

    def spawn(self, *args: Any, **kwargs: Any) -> ScriptedChild:  # noqa: ANN401
        return ScriptedChild(self._script, self, self._sent_messages)


class MockLLMService:
    """Mock LLM service that records calls and returns scripted answers."""

    def __init__(self, responses: List[str]) -> None:
        self._responses: Deque[str] = deque(responses)
        self.calls: List[Tuple[str, str]] = []

    def run_for_agent(self, agent_name: str, prompt: str) -> str:
        self.calls.append((agent_name, prompt))
        if not self._responses:
            return ""
        return self._responses.popleft()


def test_autonomous_workflow_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Test the complete autonomous supervision workflow from user request to completion.

    Simulates:
    - User asks: "Create a simple FastAPI hello world endpoint"
    - Aura spawns Claude Code in PTY
    - Claude asks questions during work
    - LLM autonomously answers
    - Work completes successfully
    """
    # Simulate realistic Claude Code output with questions
    script = deque(
        [
            ("line", "Claude Code starting..."),
            ("line", "Analyzing your request..."),
            ("line", "Should I use async def or regular def for the endpoint?"),
            ("line", "Working on implementation..."),
            ("line", "Should I add input validation with Pydantic models?"),
            ("line", "Should I add input validation with Pydantic models?"),  # Duplicate - should be ignored
            ("line", "Creating FastAPI application..."),
            ("line", "Would you like me to add CORS middleware?"),
            ("line", "Implementation complete."),
            ("eof", None),
        ]
    )

    sent_messages: List[Tuple[str, str]] = []
    expect_module = ScriptedExpectModule(script, sent_messages)
    monkeypatch.setattr(TerminalAgentService, "_load_expect_module", lambda self: expect_module)

    # Mock LLM responses to Claude's questions
    llm = MockLLMService(
        [
            "Use async def - FastAPI is async-native and it provides better performance.",
            "Yes, add Pydantic models for input validation - it's a FastAPI best practice.",
            "Yes, add CORS middleware for development. Use CORSMiddleware from fastapi.middleware.cors.",
        ]
    )

    # Create the service
    service = TerminalAgentService(
        workspace_root=tmp_path,
        llm_service=llm,
        agent_command_template="claude-code --dangerously-skip-permissions",
    )

    # Create specification for FastAPI hello world task
    spec = AgentSpecification(
        task_id="fastapi-hello-001",
        request="Create a simple FastAPI hello world endpoint",
        project_name="fastapi-demo",
        prompt=(
            "Create a simple FastAPI application with a GET / endpoint that returns "
            "{'message': 'Hello World'}. Use modern Python best practices."
        ),
    )

    # Spawn the agent - this starts the autonomous supervision flow
    session = service.spawn_agent(spec)

    # Verify session was created correctly
    assert session.task_id == "fastapi-hello-001"
    assert session.process_id == 12345
    assert session.child is not None
    assert session.monitor_thread is not None

    # Wait for monitor thread to process all output
    session.monitor_thread.join(timeout=2.0)
    assert not session.monitor_thread.is_alive()

    # Verify specification was persisted
    spec_path = Path(session.spec_path)
    assert spec_path.exists()
    assert spec_path.read_text(encoding="utf-8") == spec.prompt

    # Verify AGENTS.md was created in project root
    project_root = tmp_path / "fastapi-demo"
    agents_md = project_root / "AGENTS.md"
    assert agents_md.exists()
    agents_md_content = agents_md.read_text(encoding="utf-8")
    assert "Create a simple FastAPI application" in agents_md_content
    assert "fastapi-hello-001" in agents_md_content
    assert "Aura Coding Standards" in agents_md_content

    # Verify log file was created
    log_path = Path(session.log_path)
    assert log_path.exists()

    # Verify questions were detected and sent to LLM (3 unique questions)
    assert len(llm.calls) == 3

    # Check that all questions were sent to the correct agent
    agent_names = [agent_name for agent_name, _ in llm.calls]
    assert all(name == "architect_agent" for name in agent_names)

    # Verify questions contain context
    prompts = [prompt for _, prompt in llm.calls]
    assert any("async def or regular def" in prompt for prompt in prompts)
    assert any("input validation with Pydantic" in prompt for prompt in prompts)
    assert any("CORS middleware" in prompt for prompt in prompts)

    # Verify all prompts include task context
    for prompt in prompts:
        assert "fastapi-hello-001" in prompt
        assert "Aura" in prompt

    # Verify responses were sent back to Claude Code via PTY
    responses = [msg for kind, msg in sent_messages if kind == "sendline"]
    assert len(responses) == 3
    assert "async def" in responses[0]
    assert "Pydantic models" in responses[1]
    assert "CORS" in responses[2]

    # Verify initial AGENTS.md prompt was sent
    initial_sends = [msg for kind, msg in sent_messages if kind == "send"]
    assert len(initial_sends) == 1
    assert "Create a simple FastAPI application" in initial_sends[0]

    # Verify duplicate question was filtered (only asked twice but answered once)
    duplicate_question_count = sum(
        "input validation with Pydantic" in prompt for prompt in prompts
    )
    assert duplicate_question_count == 1

    # Verify answered questions were tracked
    assert len(session.answered_questions) == 3
    assert "Should I use async def or regular def for the endpoint?" in session.answered_questions
    assert "Should I add input validation with Pydantic models?" in session.answered_questions
    assert "Would you like me to add CORS middleware?" in session.answered_questions

    # Verify session completed with correct exit code
    exit_code = session.poll()
    assert exit_code == 0
