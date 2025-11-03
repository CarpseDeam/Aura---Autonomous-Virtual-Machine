from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from src.aura.app.event_bus import EventBus
from src.aura.models.agent_task import AgentSpecification, TerminalSession
from src.aura.models.event_types import (
    TERMINAL_SESSION_COMPLETED,
    TERMINAL_SESSION_FAILED,
    TERMINAL_SESSION_STARTED,
)
from src.aura.models.events import Event
from src.aura.services.llm_service import LLMService
from src.aura.services.terminal_agent_service import TerminalAgentService
from src.aura.services.workspace_service import WorkspaceService
from src.aura.utils.output_parser import OutputParser, OutputParserResult, read_new_text


logger = logging.getLogger(__name__)


class AgentSupervisor:
    _LLM_AGENT_NAME = "architect_agent"
    _POLL_INTERVAL_SECONDS = 2.0

    def __init__(
        self,
        llm_service: LLMService,
        terminal_service: TerminalAgentService,
        workspace_service: WorkspaceService,
        event_bus: EventBus,
    ) -> None:
        self.llm = llm_service
        self.terminal_service = terminal_service
        self.workspace = workspace_service
        self.event_bus = event_bus
        self._lock = threading.Lock()
        self._sessions: dict[str, TerminalSession] = {}

    def process_message(self, user_message: str, project_name: str) -> None:
        message = user_message.strip()
        if not message:
            raise ValueError("user_message must not be empty")
        project = project_name.strip()
        if not project:
            raise ValueError("project_name must not be empty")
        task_id = uuid4().hex[:12]
        project_path = self._ensure_project_directory(project)
        description = self._generate_task_description(message)
        spec = self._build_specification(task_id, project, message, description)
        self._create_agents_md(project_path, description, task_id)
        try:
            session = self.terminal_service.spawn_agent(spec)
        except Exception as exc:
            logger.error("Failed to spawn agent for task %s: %s", task_id, exc, exc_info=True)
            self._dispatch_event(
                TERMINAL_SESSION_FAILED,
                {"task_id": task_id, "failure_reason": "spawn_failed", "error_message": str(exc)},
            )
            raise
        self._sessions[session.task_id] = session
        payload = {
            "task_id": session.task_id,
            "command": session.command,
            "spec_path": session.spec_path,
            "started_at": session.started_at.isoformat(),
        }
        if session.process_id is not None:
            payload["process_id"] = session.process_id
        self._dispatch_event(TERMINAL_SESSION_STARTED, payload)
        self._start_monitor_thread(session, project_path)

    def _generate_task_description(self, user_message: str) -> str:
        prompt = (
            "Convert the following user request into a concise, actionable task description "
            "for a coding agent.\n\n"
            f"User request:\n{user_message.strip()}"
        )
        try:
            response = self.llm.run_for_agent(self._LLM_AGENT_NAME, prompt).strip()
            return response or user_message
        except Exception as exc:
            logger.error("LLM task description generation failed: %s", exc, exc_info=True)
            return user_message

    def _build_specification(
        self,
        task_id: str,
        project_name: str,
        user_message: str,
        task_description: str,
    ) -> AgentSpecification:
        header = task_description.strip() or user_message
        prompt = f"{header}\n\nOriginal request:\n{user_message}"
        return AgentSpecification(
            task_id=task_id,
            request=user_message,
            project_name=project_name,
            prompt=prompt,
            metadata={"generated_by": "AgentSupervisor"},
        )

    def _ensure_project_directory(self, project_name: str) -> Path:
        project_path = self.workspace.workspace_root / project_name
        project_path.mkdir(parents=True, exist_ok=True)
        (project_path / ".aura").mkdir(parents=True, exist_ok=True)
        return project_path

    def _create_agents_md(self, project_path: Path, task_description: str, task_id: str) -> None:
        description = task_description.strip() or "(no task description provided)"
        content = (
            "# Task Description\n"
            f"{description}\n\n"
            "## Instructions\n"
            "- Follow the Aura Coding Standards in this repository.\n"
            f"- Log progress to `.aura/{task_id}.output.log`.\n"
            f"- Mark completion by writing `.aura/{task_id}.done`.\n"
        )
        (project_path / "AGENTS.md").write_text(content, encoding="utf-8")

    def _start_monitor_thread(self, session: TerminalSession, project_path: Path) -> None:
        threading.Thread(
            target=self._monitor_output_loop,
            args=(session, project_path),
            daemon=True,
            name=f"aura-monitor-{session.task_id}",
        ).start()

    def _monitor_output_loop(self, session: TerminalSession, project_path: Path) -> None:
        task_id = session.task_id
        log_path = project_path / ".aura" / f"{task_id}.output.log"
        parser = OutputParser(project_path, task_id)
        position = 0
        try:
            while True:
                text, position = read_new_text(log_path, position)
                running = session.is_alive()
                result = parser.analyze(text, running)
                if result.is_complete or not running:
                    self._finalize_session(session, result)
                    break
                time.sleep(self._POLL_INTERVAL_SECONDS)
        except Exception as exc:
            logger.error("Output monitor error for task %s: %s", task_id, exc, exc_info=True)
            self._dispatch_event(
                TERMINAL_SESSION_FAILED,
                {"task_id": task_id, "failure_reason": "monitor_error", "error_message": str(exc)},
            )

    def _finalize_session(self, session: TerminalSession, result: OutputParserResult) -> None:
        exit_code = session.poll()
        if exit_code is None and result.is_complete:
            exit_code = session.wait(timeout=1.0)
        payload = {
            "task_id": session.task_id,
            "completion_reason": result.completion_reason or "process-exit",
        }
        if exit_code is not None:
            payload["exit_code"] = exit_code
        event_type = TERMINAL_SESSION_COMPLETED if exit_code in (0, None) else TERMINAL_SESSION_FAILED
        self._dispatch_event(event_type, payload)
        self._sessions.pop(session.task_id, None)

    def _dispatch_event(self, event_type: str, payload: dict[str, object]) -> None:
        try:
            self.event_bus.dispatch(Event(event_type=event_type, payload=payload))
        except Exception:
            logger.error("Failed to dispatch %s for task %s", event_type, payload.get("task_id"), exc_info=True)
