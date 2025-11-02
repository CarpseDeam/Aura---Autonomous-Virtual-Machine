"""Tests covering the Codex terminal agent handoff behaviour."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock

import pytest

from src.aura.models.agent_task import AgentSpecification
from src.aura.services.agents_md_formatter import format_specification_for_codex
from src.aura.services.terminal_agent_service import TerminalAgentService


def _sample_specification(**overrides: object) -> AgentSpecification:
    blueprint = {
        "files": [
            {"file_path": "src/app.py"},
            {"file_path": "README.md"},
        ]
    }
    data: Dict[str, object] = {
        "task_id": "task-123",
        "request": "Add greeting endpoint",
        "project_name": "demo",
        "blueprint": blueprint,
        "prompt": "Implement greeting endpoint.\n",
        "files_to_watch": ["src/app.py"],
    }
    data.update(overrides)
    return AgentSpecification(**data)  # type: ignore[arg-type]


def test_agents_md_formatter_content() -> None:
    spec = _sample_specification()

    content = format_specification_for_codex(spec)

    assert content.startswith("# Aura Coding Standards"), "AGENTS.md should open with the coding standards section"
    assert "# Task" in content
    assert content.index("# Task") > content.index("# Aura Coding Standards")
    assert "- src/app.py" in content
    assert "- README.md" in content
    assert "- Project: demo" in content
    assert "- Task ID: task-123" in content
    assert ".aura/task-123.done" in content


def test_spawn_agent_creates_agents_md(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = TerminalAgentService(workspace_root=tmp_path)
    spec = _sample_specification()

    popen_calls: Dict[str, object] = {}

    class FakeProcess:
        pid = 9999

    def fake_popen(command: List[str], **kwargs: object) -> FakeProcess:
        popen_calls["command"] = command
        popen_calls["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    session = service.spawn_agent(spec, command_override=["echo", "hello"])

    project_root = tmp_path / "demo"
    agents_md = project_root / "AGENTS.md"

    assert agents_md.exists(), "AGENTS.md should be written to the project root"
    file_content = agents_md.read_text(encoding="utf-8")
    assert file_content.startswith("# Aura Coding Standards")
    assert "# Task" in file_content
    assert file_content.index("# Task") > file_content.index("# Aura Coding Standards")
    assert popen_calls["kwargs"]["cwd"] == str(project_root)
    assert session.process_id == 9999


def test_spawn_agent_fails_when_agents_md_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = TerminalAgentService(workspace_root=tmp_path)
    spec = _sample_specification()

    original_write_text = Path.write_text

    def failing_write(self: Path, content: str, *args: object, **kwargs: object) -> int:
        if self.name == "AGENTS.md":
            raise OSError("disk full")
        return original_write_text(self, content, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write, raising=False)

    popen_called = {"flag": False}

    def fake_popen(*args: object, **kwargs: object) -> MagicMock:
        popen_called["flag"] = True
        return MagicMock(pid=1)

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    with pytest.raises(RuntimeError):
        service.spawn_agent(spec, command_override=["echo"])

    assert not popen_called["flag"], "Terminal should not spawn when AGENTS.md write fails"


def test_build_terminal_command_injects_codex_autonomy_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "demo"
    project_root.mkdir()

    service = TerminalAgentService(
        workspace_root=tmp_path,
        agent_command_template="codex",
    )

    spec_path = service.spec_dir / "task-123.md"

    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("shutil.which", lambda _: None)

    command = service._build_terminal_command(spec_path, project_root)

    assert isinstance(command, list)
    assert any("--full-auto" in part for part in command), "Codex command should include --full-auto flag"
    assert any("codex --full-auto -".strip() in part for part in command), "Codex command should read from stdin via '-'"


def test_build_terminal_command_injects_claude_autonomy_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "demo"
    project_root.mkdir()

    service = TerminalAgentService(
        workspace_root=tmp_path,
        agent_command_template="claude-code",
    )

    spec_path = service.spec_dir / "task-456.md"

    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("shutil.which", lambda _: None)

    command = service._build_terminal_command(spec_path, project_root)

    assert isinstance(command, list)
    assert any("--dangerously-skip-permissions" in part for part in command), "Claude command should skip approvals"
    assert any("claude-code --dangerously-skip-permissions -".strip() in part for part in command), "Claude command should read from stdin via '-'"
