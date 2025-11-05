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
from src.aura.services.agents_md_formatter import format_specification_for_gemini
from src.aura.services.terminal_bridge import TerminalBridge
from src.aura.services.user_settings_manager import DEFAULT_GEMINI_MODEL, UserSettingsManager

if TYPE_CHECKING:
    from src.aura.app.event_bus import EventBus
    from src.aura.services.llm_service import LLMService


logger = logging.getLogger(__name__)


class TerminalAgentService:
    """
    Coordinate terminal-based agent sessions through the embedded xterm.js terminal.

    The service persists task specifications, prepares GEMINI.md handoff files, and
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
        settings_manager: Optional[UserSettingsManager] = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.spec_dir = self.workspace_root / self.SPEC_DIR_NAME
        self.spec_dir.mkdir(parents=True, exist_ok=True)

        self.llm_service = llm_service
        self.event_bus = event_bus
        self.question_agent_name = question_agent_name

        template = (agent_command_template or "").strip()
        self.agent_command_template = template or "gemini"

        self._terminal_bridge = terminal_bridge or TerminalBridge(event_bus=event_bus)
        self._terminal_bridge.start()

        self._sessions: Dict[str, TerminalSession] = {}
        self.settings_manager = settings_manager

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
        working_dir: Optional[Path] = None,
    ) -> TerminalSession:
        """
        Prepare the workspace and instruct the embedded terminal to execute Gemini CLI.

        Args:
            spec: Agent specification with task details
            command_override: Optional command tokens to use instead of template
            env: Optional environment variables
            working_dir: Working directory for command execution
        """
        if not spec.task_id:
            raise ValueError("Agent specification must include a task_id")

        project_root = self._resolve_project_root(spec)
        spec_path = self._persist_specification(spec)
        prompt_document = format_specification_for_gemini(spec)
        gemini_md_path = self._write_gemini_md(project_root, prompt_document, spec)
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

        effective_working_dir = working_dir or project_root

        try:
            self._terminal_bridge.start_session(spec.task_id, log_path, working_dir=effective_working_dir)
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
                        "gemini_md_path": str(gemini_md_path),
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

    def _write_gemini_md(self, project_root: Path, document: str, spec: AgentSpecification) -> Path:
        """
        Write GEMINI.md (Gemini CLI's native context file) to project root.

        Gemini CLI automatically reads GEMINI.md for project context.
        This is the standard format per official documentation.
        """
        gemini_md_path = project_root / "GEMINI.md"
        try:
            gemini_md_path.write_text(document, encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to write GEMINI.md for task %s: %s", spec.task_id, exc, exc_info=True)
            raise RuntimeError(f"Failed to write GEMINI.md for task {spec.task_id}") from exc

        logger.info("Wrote GEMINI.md for task %s to %s", spec.task_id, gemini_md_path)
        return gemini_md_path

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
        """
        Build command tokens for Gemini CLI headless execution.

        Per Gemini CLI docs:
        - Use -p flag for headless/non-interactive mode
        - Use --output-format json for structured output
        - Use --yolo to skip all confirmations
        - Gemini auto-reads GEMINI.md from current working directory
        """
        if command_override:
            tokens = list(command_override)
            logger.info("Using command override for task %s: %s", spec.task_id, tokens)
            return tokens

        template_parts = self.agent_command_template.split()
        base_cmd = template_parts[0]

        resolved_model = DEFAULT_GEMINI_MODEL
        if self.settings_manager:
            try:
                resolved_model = self.settings_manager.get_gemini_model()
            except Exception as exc:
                logger.warning(
                    "Failed to resolve Gemini model for task %s: %s",
                    spec.task_id,
                    exc,
                )

        tokens = [base_cmd]
        base_cmd_name = Path(base_cmd).stem.lower()
        include_model_flag = base_cmd_name == "gemini"
        if include_model_flag:
            tokens.extend(["--model", resolved_model])

        tokens = [
            *tokens,
            "-p",
            f"Implement all tasks described in GEMINI.md. When complete, write .aura/{spec.task_id}.done and .aura/{spec.task_id}.summary.json files.",
            "--yolo",
        ]

        if include_model_flag:
            logger.info(
                "Built command for task %s with model %s: %s",
                spec.task_id,
                resolved_model,
                " ".join(tokens),
            )
        else:
            logger.info("Built command for task %s: %s", spec.task_id, " ".join(tokens))
        logger.info("Working directory will be: %s", self._resolve_project_root(spec))

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

    def _compose_terminal_command(
        self,
        command_tokens: Sequence[str],
        *,
        project_root: Path,
        prompt_path: Optional[Path] = None,
        environment: Optional[Dict[str, str]] = None,
    ) -> str:
        env_map = environment or {}
        if sys.platform.startswith("win"):
            env_statements = [
                f"$env:{key} = {self._powershell_quote(value)}"
                for key, value in env_map.items()
            ]
            project_str = self._powershell_quote(str(project_root))
            cd_command = f"Set-Location -Path {project_str}"

            def _needs_quotes(token: str) -> bool:
                special_chars = {" ", "&", "|", "<", ">", "^"}
                return not token or any(char in token for char in special_chars)

            quoted_tokens: List[str] = []
            for raw_token in command_tokens:
                token = str(raw_token)
                if _needs_quotes(token):
                    quoted_tokens.append(self._powershell_quote(token))
                else:
                    quoted_tokens.append(token)
            agent_command = " ".join(quoted_tokens)

            parts = [
                cd_command,
                *env_statements,
                "$ErrorActionPreference = 'Stop'",
            ]
            if agent_command:
                parts.append(f"& {agent_command}")
            return "; ".join(filter(None, parts))

        env_lines = [f"export {key}={shlex.quote(value)}" for key, value in env_map.items()]
        command_str = " ".join(shlex.quote(token) for token in command_tokens)

        segments = [f"cd {shlex.quote(str(project_root))}"]
        segments.extend(env_lines)
        if command_str:
            segments.append(command_str)
        return " && ".join(segments)

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
        # Encourage downstream CLI processes to stream output without buffering so the UI updates promptly.
        env_map.setdefault("PYTHONUNBUFFERED", "1")
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
