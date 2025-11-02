from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from src.aura.models.agent_task import AgentSpecification, TerminalSession
from src.aura.services.agents_md_formatter import format_specification_for_codex

logger = logging.getLogger(__name__)


class TerminalAgentService:
    """
    Launches external coding agents in dedicated terminal sessions.

    Responsibilities:
    - Persist agent specifications to the workspace handoff directory.
    - Spawn terminal windows running the configured tooling command.
    - Track spawned session metadata for later monitoring.
    """

    SPEC_DIR_NAME = ".aura"

    def __init__(
        self,
        workspace_root: Path,
        default_command: Optional[Sequence[str]] = None,
        agent_command_template: Optional[str] = None,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.default_command = list(default_command) if default_command else None
        self.agent_command_template = agent_command_template or "cat {spec_path}"
        self.spec_dir = self.workspace_root / self.SPEC_DIR_NAME
        self.spec_dir.mkdir(parents=True, exist_ok=True)
        logger.info("TerminalAgentService ready (spec dir: %s, template: %s)",
                   self.spec_dir, self.agent_command_template)

    def _build_terminal_command(self, spec_path: Path, project_root: Path) -> List[str]:
        """
        Build a platform-specific command that opens a visible terminal and runs the agent.

        Args:
            spec_path: Path to the persisted specification file
            project_root: Path to the project root directory

        Returns:
            Command list ready for subprocess.Popen
        """
        # Construct AGENTS.md path in project root
        agents_md_path = project_root / "AGENTS.md"

        # Format the agent command with the spec path
        agent_command = self.agent_command_template.format(
            spec_path=str(spec_path),
            task_id=spec_path.stem,
        ).strip()

        agent_command = self._apply_autonomy_flags(agent_command)

        if sys.platform.startswith("win"):
            # Windows: Pipe AGENTS.md with 2-second delay
            agents_md_literal = str(agents_md_path).replace("'", "''")
            reader_command = f"Get-Content -Raw -Encoding UTF8 '{agents_md_literal}'"
            delayed_command = f"Start-Sleep -Seconds 2; {reader_command} | {agent_command}"

            # Windows: Use PowerShell with -NoExit to keep window open
            return [
                "pwsh.exe",
                "-NoExit",
                "-Command",
                delayed_command,
            ]
        else:
            # Unix: Pipe AGENTS.md with 2-second delay
            agents_md_quoted = shlex.quote(str(agents_md_path))
            delayed_command = f"sleep 2 && cat {agents_md_quoted} | {agent_command}"

            # Unix: Try to find an available terminal emulator
            terminal_emulators = [
                ("gnome-terminal", ["--", "bash", "-c", f"{delayed_command}; exec bash"]),
                ("konsole", ["-e", "bash", "-c", f"{delayed_command}; exec bash"]),
                ("xterm", ["-hold", "-e", "bash", "-c", delayed_command]),
            ]

            # Try each terminal emulator until we find one that exists
            import shutil
            for emulator, args in terminal_emulators:
                if shutil.which(emulator):
                    logger.debug("Using terminal emulator: %s", emulator)
                    return [emulator] + args

            # Fallback: just run bash directly (won't be visible on Unix without terminal)
            logger.warning("No terminal emulator found, falling back to direct bash execution")
            return ["bash", "-c", delayed_command]

    def _apply_autonomy_flags(self, agent_command: str) -> str:
        """
        Ensure Codex and Claude Code run in autonomous mode and read specs from stdin.
        """
        normalized = agent_command.strip()
        if not normalized:
            return agent_command

        try:
            tokens = shlex.split(normalized, posix=not sys.platform.startswith("win"))
        except ValueError:
            # Fallback: naive split when quoting is invalid
            tokens = normalized.split()

        if not tokens:
            return agent_command

        executable = tokens[0].lower()

        def _ensure_flag(flag: str) -> None:
            if flag not in tokens:
                tokens.insert(1, flag)

        def _ensure_stdin_marker() -> None:
            if "-" not in tokens:
                tokens.append("-")

        if executable in {"codex", "codex.exe"}:
            _ensure_flag("--full-auto")
            _ensure_stdin_marker()
        elif executable in {"claude-code", "claude", "claude.exe"}:
            _ensure_flag("--dangerously-skip-permissions")
            _ensure_stdin_marker()

        return " ".join(tokens)

    def spawn_agent(
        self,
        spec: AgentSpecification,
        *,
        command_override: Optional[Sequence[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> TerminalSession:
        """
        Persist the specification and launch the external agent.
        """
        project_root = self._resolve_project_root(spec)
        spec_path = self._persist_specification(spec)

        self._write_agents_md(project_root, spec)

        # Use command override if provided, otherwise build from template
        if command_override:
            command = list(command_override)
        elif self.default_command:
            command = self.default_command
        else:
            # Build command using template and spec path
            command = self._build_terminal_command(spec_path, project_root)

        session_env = os.environ.copy()
        session_env.update(env or {})
        session_env["AURA_AGENT_SPEC_PATH"] = str(spec_path)
        session_env["AURA_AGENT_TASK_ID"] = spec.task_id

        # Prepare subprocess creation flags for visible terminal windows
        popen_kwargs = {
            "cwd": str(project_root),
            "env": session_env,
        }

        if sys.platform.startswith("win"):
            # On Windows, create a new visible console window
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
            logger.debug("Using CREATE_NEW_CONSOLE flag for Windows terminal visibility")
        else:
            # On Unix-like systems, the terminal emulator command itself creates a visible window
            logger.debug("Using native terminal emulator for Unix terminal visibility")

        try:
            process = subprocess.Popen(command, **popen_kwargs)
            logger.info(
                "Spawned terminal agent (task=%s, pid=%s, command=%s)",
                spec.task_id,
                process.pid if process else None,
                command,
            )
        except Exception as exc:
            logger.error("Failed to spawn terminal agent for task %s: %s", spec.task_id, exc, exc_info=True)
            raise

        return TerminalSession(
            task_id=spec.task_id,
            command=command,
            spec_path=str(spec_path),
            process_id=process.pid if process else None,
        )

    def _persist_specification(self, spec: AgentSpecification) -> Path:
        spec_file = self.spec_dir / f"{spec.task_id}.md"
        spec_file.write_text(spec.prompt, encoding="utf-8")
        logger.debug("Wrote agent specification for task %s to %s", spec.task_id, spec_file)
        return spec_file

    def _resolve_project_root(self, spec: AgentSpecification) -> Path:
        base_root = self.workspace_root
        if spec.project_name:
            candidate = (base_root / spec.project_name).resolve()
            try:
                candidate.relative_to(base_root.resolve())
            except ValueError:
                logger.warning(
                    "Project name '%s' resolved outside workspace; defaulting to workspace root.",
                    spec.project_name,
                )
                candidate = base_root
        else:
            candidate = base_root

        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    def _write_agents_md(self, project_root: Path, spec: AgentSpecification) -> Path:
        agents_md = project_root / "AGENTS.md"
        content = format_specification_for_codex(spec)
        try:
            agents_md.write_text(content, encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to write AGENTS.md for task %s: %s", spec.task_id, exc, exc_info=True)
            raise RuntimeError(f"Failed to write AGENTS.md for task {spec.task_id}") from exc

        logger.info("Wrote AGENTS.md for task %s to %s", spec.task_id, agents_md)
        return agents_md
