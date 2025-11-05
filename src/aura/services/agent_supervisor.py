from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pydantic

from src.aura.app.event_bus import EventBus
from src.aura.models.agent_task import AgentSpecification, TerminalSession, TaskSummary
from src.aura.models.event_types import (
    TASK_PLAN_GENERATED,
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
        task_description = self._generate_task_description(message)
        spec = self._build_specification(task_id, project, message, task_description)

        # Dispatch event to show the user the plan
        self._dispatch_event(
            TASK_PLAN_GENERATED,
            {
                "task_id": task_id,
                "task_description": task_description,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        self._create_gemini_md(project_path, spec.prompt, spec.task_id)

        try:
            session = self.terminal_service.spawn_agent(spec, working_dir=project_path)
        except Exception as exc:
            logger.error("Failed to spawn agent for task %s: %s", spec.task_id, exc, exc_info=True)
            self._dispatch_event(
                TERMINAL_SESSION_FAILED,
                {"task_id": spec.task_id, "failure_reason": "spawn_failed", "error_message": str(exc)},
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
        """
        Generate detailed task description from user message via LLM.
        
        Args:
            user_message: Raw user input
            
        Returns:
            Detailed task specification with concrete file names and paths
        """
        prompt = f"""User request: {user_message}

Generate a detailed technical specification for a coding agent to implement this request.

CRITICAL REQUIREMENTS:
1. Include SPECIFIC file names, not vague descriptions
2. Include EXACT file paths relative to project root
3. Specify directory structure if new directories are needed
4. Provide file-by-file breakdown of what to create/modify

Format your response as:

## Task Summary
[One sentence describing what will be built]

## Files to Create/Modify
- path/to/specific_file.py: [One line describing purpose]
- path/to/another_file.py: [One line describing purpose]

## Implementation Steps
1. Create [specific_file.py] containing [concrete description]
2. Add [specific function/class] to [specific_file.py]
3. Test by running [specific command]

EXAMPLES:

BAD (too vague):
"Write a Python script that prints a greeting"

GOOD (concrete):
"Create hello_world.py in the project root containing a print() statement that outputs 'Hello, World!'"

BAD (no file name):
"Build a Flask API with routes"

GOOD (specific files):
"Create app.py in project root with Flask initialization
Create routes.py containing /hello endpoint that returns JSON
Create models.py with User dataclass"

Always provide explicit file names. Never say "a file" or "a script" without naming it.
"""
        
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

    def _create_gemini_md(self, project_path: Path, task_description: str, task_id: str) -> None:
        """
        Write GEMINI.md to project root with task description.

        GEMINI.md is Gemini CLI's standard context file.
        """
        description = task_description.strip() or "(no task description provided)"
        content = (
            "# Task Description\n"
            f"{description}\n\n"
            "## Instructions\n"
            "- Follow the Aura Coding Standards in this repository.\n"
            f"- Log progress to `.aura/{task_id}.output.log`.\n"
            f"- Mark completion by writing `.aura/{task_id}.done`.\n"
            f"- Write summary to `.aura/{task_id}.summary.json`.\n"
        )
        gemini_md_path = project_path / "GEMINI.md"
        gemini_md_path.write_text(content, encoding="utf-8")
        logger.info("Wrote GEMINI.md to %s", gemini_md_path)

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
                    self._finalize_session(session, result, project_path)
                    break
                time.sleep(self._POLL_INTERVAL_SECONDS)
        except Exception as exc:
            logger.error("Output monitor error for task %s: %s", task_id, exc, exc_info=True)
            self._dispatch_event(
                TERMINAL_SESSION_FAILED,
                {"task_id": task_id, "failure_reason": "monitor_error", "error_message": str(exc)},
            )

    def _finalize_session(
        self, session: TerminalSession, result: OutputParserResult, project_path: Path
    ) -> None:
        exit_code = session.poll()
        if exit_code is None and result.is_complete:
            exit_code = session.wait(timeout=1.0)

        summary_data = self._load_task_summary(session.task_id, project_path)

        payload = {
            "task_id": session.task_id,
            "completion_reason": result.completion_reason or "process-exit",
            "summary_data": summary_data,
        }
        if exit_code is not None:
            payload["exit_code"] = exit_code

        event_type = (
            TERMINAL_SESSION_COMPLETED if exit_code in (0, None) else TERMINAL_SESSION_FAILED
        )
        self._dispatch_event(event_type, payload)
        self._sessions.pop(session.task_id, None)

    def _load_task_summary(self, task_id: str, project_path: Path) -> dict:
        summary_file = project_path / ".aura" / f"{task_id}.summary.json"
        try:
            if not summary_file.exists():
                raise FileNotFoundError("Summary file not found.")
            summary_content = summary_file.read_text(encoding="utf-8")
            summary = TaskSummary.model_validate_json(summary_content)
            return summary.model_dump(mode="json")
        except (FileNotFoundError, json.JSONDecodeError, pydantic.ValidationError) as e:
            logger.warning(
                "Could not load or parse summary file for task %s: %s", task_id, e
            )
            return {
                "status": "unknown",
                "files_created": [],
                "files_modified": [],
                "files_deleted": [],
                "suggestions": [],
                "note": "Summary file could not be retrieved or was invalid.",
            }

    def _dispatch_event(self, event_type: str, payload: dict[str, object]) -> None:
        try:
            self.event_bus.dispatch(Event(event_type=event_type, payload=payload))
        except Exception:
            logger.error(
                "Failed to dispatch %s for task %s",
                event_type,
                payload.get("task_id"),
                exc_info=True,
            )
