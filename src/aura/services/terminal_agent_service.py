from __future__ import annotations

import logging
import os
import shlex
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, TYPE_CHECKING

from src.aura.models.agent_task import AgentSpecification, TerminalSession
from src.aura.models.event_types import (
    AGENT_OUTPUT,
    TERMINAL_EXECUTE_COMMAND,
    TERMINAL_OUTPUT_RECEIVED,
)
from src.aura.models.events import Event
from src.aura.services.agents_md_formatter import format_specification_for_codex
from src.aura.services.terminal_bridge import TerminalBridge

if TYPE_CHECKING:
    from src.aura.app.event_bus import EventBus
    from src.aura.services.llm_service import LLMService


logger = logging.getLogger(__name__)


class TerminalAgentService:
    """
    Coordinate terminal-based agent sessions through the embedded xterm.js terminal.

    The service persists task specifications, prepares AGENTS.md handoff files, and
    relays execution commands to the TerminalBridge so the user can watch the live shell.
    """

    SPEC_DIR_NAME = ".aura"
    _DEFAULT_LLM_AGENT = "architect_agent"

    def __init__(
        self,
        workspace_root: Path,
        llm_service: LLMService,
        event_bus: EventBus,
        *,
        agent_command_template: Optional[str] = None,
        question_agent_name: str = _DEFAULT_LLM_AGENT,
        terminal_bridge: Optional[TerminalBridge] = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.spec_dir = self.workspace_root / self.SPEC_DIR_NAME
        self.spec_dir.mkdir(parents=True, exist_ok=True)

        self.llm_service = llm_service
        self.event_bus = event_bus
        self.question_agent_name = question_agent_name

        template = (agent_command_template or "").strip()
        self.agent_command_template = template or "claude --dangerously-skip-permissions"

        self._terminal_bridge = terminal_bridge or TerminalBridge(event_bus=event_bus)
        self._terminal_bridge.start()

        self._sessions: Dict[str, TerminalSession] = {}

        self.event_bus.subscribe(TERMINAL_OUTPUT_RECEIVED, self._handle_terminal_output)

        logger.info(
            "TerminalAgentService initialized with embedded terminal bridge (workspace=%s, template=%s)",
            self.workspace_root,
            self.agent_command_template,
        )

    # ------------------------------------------------------------------ Public API

    def spawn_agent(
        self,
        spec: AgentSpecification,
        *,
        command_override: Optional[Sequence[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> TerminalSession:
        """
        Prepare the workspace and instruct the embedded terminal to execute Claude Code.
        """
        if not spec.task_id:
            raise ValueError("Agent specification must include a task_id")

        project_root = self._resolve_project_root(spec)
        spec_path = self._persist_specification(spec)
        prompt_document = format_specification_for_codex(spec)
        agents_md_path = self._write_agents_md(project_root, prompt_document, spec)
        prompt_path = self._write_prompt_file(spec.task_id, prompt_document)

        command_tokens = self._build_command(spec, command_override)
        env_map = self._build_session_environment(spec_path, spec.task_id, env or {})
        terminal_command = self._compose_terminal_command(
            command_tokens,
            project_root=project_root,
            prompt_path=prompt_path,
            environment=env_map,
        )

        log_path = project_root / self.SPEC_DIR_NAME / f"{spec.task_id}.output.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch(exist_ok=True)

        try:
            self._terminal_bridge.start_session(spec.task_id, log_path)
        except Exception as exc:
            logger.error("Failed to start terminal bridge session for task %s: %s", spec.task_id, exc, exc_info=True)
            raise

        try:
            self.event_bus.dispatch(
                Event(
                    event_type=TERMINAL_EXECUTE_COMMAND,
                    payload={
                        "task_id": spec.task_id,
                        "command": terminal_command,
                        "project_root": str(project_root),
                        "agents_md_path": str(agents_md_path),
                    },
                )
            )
        except Exception as exc:
            logger.error("Failed to dispatch terminal command for task %s: %s", spec.task_id, exc, exc_info=True)
            self._terminal_bridge.end_session()
            raise

        session = self._record_session(spec, command_tokens, spec_path, log_path)
        logger.info("Terminal command dispatched for task %s", spec.task_id)
        return session

    # ------------------------------------------------------------------ Event handling

    def _handle_terminal_output(self, event: Event) -> None:
        payload = event.payload or {}
        task_id = payload.get("task_id")
        text = payload.get("text")
        if not task_id or not isinstance(text, str):
            return
        if task_id not in self._sessions:
            return

        timestamp = payload.get("timestamp") or datetime.utcnow().isoformat()
        if not text.strip():
            return

        self.event_bus.dispatch(
            Event(
                event_type=AGENT_OUTPUT,
                payload={
                    "task_id": task_id,
                    "text": text,
                    "timestamp": timestamp,
                },
            )
        )

    # ------------------------------------------------------------------ Helpers

    def _resolve_project_root(self, spec: AgentSpecification) -> Path:
        name = (spec.project_name or "").strip()
        if not name:
            return self.workspace_root

        project_root = (self.workspace_root / name).resolve()
        project_root.mkdir(parents=True, exist_ok=True)
        (project_root / self.SPEC_DIR_NAME).mkdir(parents=True, exist_ok=True)
        return project_root

    def _persist_specification(self, spec: AgentSpecification) -> Path:
        spec_path = self.spec_dir / f"{spec.task_id}.md"
        spec_path.write_text(spec.prompt, encoding="utf-8")
        logger.debug("Persisted agent specification for task %s to %s", spec.task_id, spec_path)
        return spec_path

    def _write_agents_md(self, project_root: Path, document: str, spec: AgentSpecification) -> Path:
        agents_md_path = project_root / "AGENTS.md"
        try:
            agents_md_path.write_text(document, encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to write AGENTS.md for task %s: %s", spec.task_id, exc, exc_info=True)
            raise RuntimeError(f"Failed to write AGENTS.md for task {spec.task_id}") from exc

        logger.info("Wrote AGENTS.md for task %s to %s", spec.task_id, agents_md_path)
        return agents_md_path

    def _write_prompt_file(self, task_id: str, prompt_document: str) -> Path:
        prompt_path = self.spec_dir / f"{task_id}.prompt.txt"
        prompt_path.write_text(prompt_document, encoding="utf-8")
        logger.debug("Persisted prompt document for task %s to %s", task_id, prompt_path)
        return prompt_path

    def _build_command(
        self,
        spec: AgentSpecification,
        command_override: Optional[Sequence[str]],
    ) -> List[str]:
        if command_override:
            tokens = list(command_override)
            logger.info("Using command override for task %s: %s", spec.task_id, tokens)
            return tokens

        tokens = self._render_template_command(spec)
        tokens = self._ensure_claude_flags(tokens)
        if not tokens:
            raise ValueError("Resolved agent command is empty")
        return tokens

    def _render_template_command(self, spec: AgentSpecification) -> List[str]:
        template = (self.agent_command_template or "").strip()
        if not template:
            raise ValueError("agent_command_template resolved to empty command")

        args = {
            "spec_path": str(self.spec_dir / f"{spec.task_id}.md"),
            "task_id": spec.task_id,
            "prompt": spec.prompt,
            "project_name": (spec.project_name or "").strip(),
            "request": spec.request,
        }

        try:
            rendered = template.format(**args)
        except KeyError as exc:
            raise RuntimeError(f"Unknown placeholder in agent command template: {exc.args[0]}") from exc

        rendered = rendered.strip()
        if not rendered:
            raise ValueError("agent_command_template produced an empty command")

        return shlex.split(rendered, posix=not sys.platform.startswith("win"))

    def _ensure_claude_flags(self, tokens: Sequence[str]) -> List[str]:
        updated = list(tokens)
        if not updated:
            return updated

        executable = updated[0].lower()
        flags = {token.lower() for token in updated[1:]}
        if "claude" in executable and "--dangerously-skip-permissions" not in flags:
            updated.append("--dangerously-skip-permissions")
        return updated

    def _compose_terminal_command(
        self,
        command_tokens: Sequence[str],
        *,
        project_root: Path,
        prompt_path: Path,
        environment: Dict[str, str],
    ) -> str:
        if sys.platform.startswith("win"):
            env_statements = [
                f"$env:{key} = {self._powershell_quote(value)}"
                for key, value in environment.items()
            ]
            invocation = self._powershell_invoke(command_tokens)
            parts = [
                f"Set-Location -Path {self._powershell_quote(str(project_root))}",
                *env_statements,
                "$ErrorActionPreference = 'Stop'",
                f"$promptPath = {self._powershell_quote(str(prompt_path))}",
                "$prompt = Get-Content -LiteralPath $promptPath -Raw",
                f"{invocation} -p $prompt",
            ]
            return "; ".join(filter(None, parts))

        env_exports = " ".join(f"{key}={shlex.quote(value)}" for key, value in environment.items())
        command_str = " ".join(shlex.quote(token) for token in command_tokens)

        segments = [f"cd {shlex.quote(str(project_root))}"]
        if env_exports:
            segments.append(f"export {env_exports}")
        segments.append(f"prompt=$(cat {shlex.quote(str(prompt_path))})")
        segments.append(f"{command_str} -p \"$prompt\"")
        return " && ".join(segments)

    def _powershell_invoke(self, command_tokens: Sequence[str]) -> str:
        if not command_tokens:
            raise ValueError("Command tokens must not be empty for PowerShell invocation")

        executable = self._powershell_quote(str(command_tokens[0]))
        args = " ".join(self._powershell_quote(str(arg)) for arg in command_tokens[1:])
        return f"& {executable}{(' ' + args) if args else ''}"

    def _powershell_quote(self, value: str) -> str:
        escaped = value.replace("'", "''")
        return f"'{escaped}'"

    def _build_session_environment(
        self,
        spec_path: Path,
        task_id: str,
        overrides: Dict[str, str],
    ) -> Dict[str, str]:
        env_map: Dict[str, str] = {
            "AURA_AGENT_SPEC_PATH": str(spec_path),
            "AURA_AGENT_TASK_ID": task_id,
        }
        if sys.platform.startswith("win"):
            env_map.setdefault("PYTHONUTF8", "1")
            env_map.setdefault("PYTHONIOENCODING", "utf-8")
        env_map.update(overrides)
        return env_map

    def _record_session(
        self,
        spec: AgentSpecification,
        command_tokens: Sequence[str],
        spec_path: Path,
        log_path: Path,
    ) -> TerminalSession:
        session = TerminalSession(
            task_id=spec.task_id,
            command=list(command_tokens),
            spec_path=str(spec_path),
            process_id=None,
            log_path=str(log_path),
        )
        self._sessions[spec.task_id] = session
        return session
