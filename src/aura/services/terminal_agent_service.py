from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from src.aura.models.agent_task import AgentSpecification, TerminalSession

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
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.default_command = list(default_command) if default_command else None
        self.spec_dir = self.workspace_root / self.SPEC_DIR_NAME
        self.spec_dir.mkdir(parents=True, exist_ok=True)
        logger.info("TerminalAgentService ready (spec dir: %s)", self.spec_dir)

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
        spec_path = self._persist_specification(spec)
        command = list(command_override) if command_override else (self.default_command or [])
        if not command:
            raise RuntimeError(
                "No command configured for TerminalAgentService. Provide default_command or command_override."
            )

        session_env = os.environ.copy()
        session_env.update(env or {})
        session_env["AURA_AGENT_SPEC_PATH"] = str(spec_path)
        session_env["AURA_AGENT_TASK_ID"] = spec.task_id

        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.workspace_root),
                env=session_env,
            )
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
