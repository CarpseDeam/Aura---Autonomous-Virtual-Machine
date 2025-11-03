"""Tests covering the Codex terminal agent handoff behaviour."""

from __future__ import annotations

import sys
from pathlib import Path
import shutil
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

    spec = _sample_specification()
    spec_path = service.spec_dir / f"{spec.task_id}.md"

    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("shutil.which", lambda _: None)

    command = service._build_terminal_command(spec_path, project_root, spec)

    assert isinstance(command, list)
    assert any("--full-auto" in part for part in command), "Codex command should include --full-auto flag"
    assert any("codex --full-auto -" in part for part in command), "Codex command should read from stdin via '- '"

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

    spec = _sample_specification(task_id="task-456")
    spec_path = service.spec_dir / f"{spec.task_id}.md"

    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("shutil.which", lambda _: None)

    command = service._build_terminal_command(spec_path, project_root, spec)

    assert isinstance(command, list)
    # On Unix with no emulator, we run: ["bash", "-c", launch_cmd]
    # Ensure dangerous skip flag is present and prompt is injected via CLAUDE_PROMPT
    assert any("--dangerously-skip-permissions" in str(part) for part in command), "Claude command should skip approvals"
    combined = " ".join(str(p) for p in command)
    assert "CLAUDE_PROMPT=$(cat" in combined, "Claude command should load AGENTS.md into CLAUDE_PROMPT"
    assert "--dangerously-skip-permissions" in combined, "Claude command should include the skip permissions flag"
    assert " -p " not in combined and not combined.rstrip().endswith(" -p"), \
        ("Interactive Claude launch should not request print-only mode")
    assert "AGENTS.md" in combined, "Claude command should source AGENTS.md content"

def test_build_terminal_command_supports_prompt_placeholder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = tmp_path / "demo"
    project_root.mkdir()

    service = TerminalAgentService(
        workspace_root=tmp_path,
        agent_command_template="echo {prompt}",
    )

    spec = _sample_specification(
        prompt="Render {escaped} braces and describe {weather} data.",
        task_id="prompt-001",
    )
    spec_path = service.spec_dir / f"{spec.task_id}.md"

    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("shutil.which", lambda _: None)

    command = service._build_terminal_command(spec_path, project_root, spec)

    rendered = " ".join(command)
    assert "Render {escaped} braces" in rendered
    assert "{weather}" in rendered

def test_windows_claude_command_loads_agents_md(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    service = TerminalAgentService(
        workspace_root=tmp_path,
        agent_command_template="claude",
    )

    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: str(tmp_path / f"{name}.exe") if name.lower() in {"wt", "wt.exe", "claude"} else None,
    )

    spec = _sample_specification(task_id="task-789")
    spec_path = service.spec_dir / f"{spec.task_id}.md"
    project_root = tmp_path / "demo"
    project_root.mkdir()

    command = service._build_terminal_command(spec_path, project_root, spec)

    assert command[:4] == ["wt.exe", "-d", str(project_root), "powershell.exe"]
    assert command[4:6] == ["-NoExit", "-Command"]
    script = command[6]
    assert "Get-Content -LiteralPath" in script
    assert "claude --dangerously-skip-permissions" in script
    assert " -p " not in script and not script.rstrip().endswith(" -p")
    assert "Write-Host ('Launching Claude Code for task '" in script

def test_windows_claude_prefers_powershell_terminal_when_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    service = TerminalAgentService(
        workspace_root=tmp_path,
        agent_command_template="claude",
        terminal_shell_preference="powershell",
    )

    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: str(tmp_path / f"{name}.exe") if name.lower() in {"wt", "wt.exe", "claude"} else None,
    )

    spec = _sample_specification(task_id="task-preference")
    spec_path = service.spec_dir / f"{spec.task_id}.md"
    project_root = tmp_path / "demo"
    project_root.mkdir()

    command = service._build_terminal_command(spec_path, project_root, spec)

    assert command[0].lower() == "pwsh.exe"
    assert "-NoExit" in command, "PowerShell launch should keep the shell open"
    assert "-Command" in command, "PowerShell launch should execute the bootstrap script"
    script = command[-1]
    assert "claude --dangerously-skip-permissions" in script
    assert " -p " not in script and not script.rstrip().endswith(" -p")

def test_spawn_agent_creates_codex_config_on_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home_dir))

    service = TerminalAgentService(
        workspace_root=tmp_path,
        agent_command_template="codex",
    )
    spec = _sample_specification()

    popen_calls: Dict[str, object] = {}

    class FakeProcess:
        pid = 4321

    def fake_popen(command: List[str], **kwargs: object) -> FakeProcess:
        popen_calls["command"] = command
        popen_calls["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    service.spawn_agent(spec)

    config_path = home_dir / ".codex" / "config.toml"
    assert config_path.exists(), "Codex config should be created when missing on Windows"
    config_contents = config_path.read_text(encoding="utf-8")
    assert 'approval_policy = "never"' in config_contents
    assert 'sandbox_mode = "danger-full-access"' in config_contents
    assert "[sandbox_workspace_write]" in config_contents
    assert "network_access = true" in config_contents
    assert "[tui]" in config_contents
    assert "notifications = false" in config_contents

    command_list = popen_calls["command"]
    assert any("--full-auto" in part for part in command_list), "Windows spawn command should include --full-auto flag"
    script_argument = command_list[-1]
    assert "--working-directory=" in script_argument, "Windows Codex command should set working directory"
    assert "danger-full-access" in script_argument, "Windows Codex fallback should request full access"
    assert "dangerously-bypass-approvals-and-sandbox" in script_argument, "Windows Codex script should include nuclear bypass"
    assert "AGENTS.md" in script_argument, "Windows Codex task should instruct Codex to read AGENTS.md"

def test_windows_gemini_command_uses_double_quotes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    service = TerminalAgentService(
        workspace_root=tmp_path,
        agent_command_template="gemini-cli",
    )

    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: str(tmp_path / f"{name}.exe") if name.lower() in {"wt", "wt.exe", "gemini-cli"} else None,
    )

    spec = _sample_specification(task_id="task-gemini")
    spec_path = service.spec_dir / f"{spec.task_id}.md"
    project_root = tmp_path / "demo"
    project_root.mkdir()

    command = service._build_terminal_command(spec_path, project_root, spec)

    assert command[:4] == ["wt.exe", "-d", str(project_root), "cmd"]
    assert command[4] == "/c"
    script = command[5]
    assert script.startswith('gemini -p "')
    assert script.endswith('" --dangerously-skip-permissions')
