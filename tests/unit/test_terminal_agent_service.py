from __future__ import annotations

from src.aura.services.terminal_agent_service import TerminalAgentService


def _make_service(tmp_path, monkeypatch) -> TerminalAgentService:
    # Ensure the Windows-specific PowerShell detection does not fail under test.
    monkeypatch.setattr(
        TerminalAgentService,
        "_resolve_powershell_executable",
        lambda self: "pwsh.exe",
        raising=True,
    )
    service = TerminalAgentService(
        workspace_root=tmp_path,
        default_command=None,
        agent_command_template="claude",
        terminal_shell_preference="auto",
    )
    return service


def _extract_tokens(service: TerminalAgentService, command: str, require_stdin: bool) -> list[str]:
    return service._apply_autonomy_flags(command, require_stdin=require_stdin)  # noqa: SLF001


def test_claude_stdin_adds_verbose_flag(tmp_path, monkeypatch) -> None:
    service = _make_service(tmp_path, monkeypatch)

    tokens = _extract_tokens(service, "claude", require_stdin=True)

    assert tokens[0] == "claude"
    assert "-" in tokens, "stdin marker should be appended for streaming agents"
    assert tokens.count("--verbose") == 1, "claude stdin streaming must include --verbose once"
    assert tokens[-1] in {"--verbose", "stream-json"}


def test_existing_verbose_flag_not_duplicated(tmp_path, monkeypatch) -> None:
    service = _make_service(tmp_path, monkeypatch)

    tokens = _extract_tokens(service, "claude --verbose -", require_stdin=True)

    assert tokens.count("--verbose") == 1, "existing --verbose flag should be preserved without duplication"
    assert "-" in tokens, "stdin marker should be preserved"
