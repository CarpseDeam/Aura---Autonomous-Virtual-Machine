from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
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
from src.aura.services.agents_md_formatter import format_specification_for_gemini
from src.aura.services.llm_service import LLMService
from src.aura.services.terminal_agent_service import TerminalAgentService
from src.aura.services.workspace_service import WorkspaceService


logger = logging.getLogger(__name__)


@dataclass
class TaskPlanningResult:
    detailed_plan: str
    task_spec: str


class AgentSupervisor:
    _LLM_AGENT_NAME = "architect_agent"
    _POLL_INTERVAL_SECONDS = 2.0
    _SESSION_TIMEOUT_SECONDS = 600.0
    _SUMMARY_WAIT_SECONDS = 5.0

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
        plan = self._generate_task_plan(message)
        spec = self._build_specification(task_id, project, message, plan.task_spec)

        # Dispatch event to show the user the plan
        self._dispatch_event(
            TASK_PLAN_GENERATED,
            {
                "task_id": task_id,
                "task_description": plan.detailed_plan,
                "task_spec": plan.task_spec,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        gemini_document = format_specification_for_gemini(spec)
        self._create_gemini_md(project_path, gemini_document, spec.task_id)

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

    def _generate_task_plan(self, user_message: str) -> TaskPlanningResult:
        """
        Generate both a detailed plan for the user and a concise task specification for Gemini.
        """
        prompt = f"""User request: {user_message}

You are Aura's architect agent. Produce TWO coordinated outputs for this request.

<detailed_plan>
[Comprehensive plan for the user. Include rationale, file-by-file work, and implementation steps.
Mirror the current detailed format with sections for Task Summary, Files, Implementation Steps, Testing, and Risks.
Keep content thorough so a human developer could follow it end-to-end.]
</detailed_plan>

<task_spec>
[# Task: <concise project title>

## Requirements
- 5-10 bullet points covering functional and non-functional requirements.

## Files to Create
- path/to/file.py — Short purpose

## Technical Constraints
- Key guardrails, dependencies, or coding standards to honor.

## Success Criteria
- Observable behaviours or commands to validate completion.

When complete, write .aura/{{task_id}}.done and .aura/{{task_id}}.summary.json files.]
</task_spec>

Rules:
- Do NOT include code snippets inside <task_spec>.
- Every file mentioned must include an explicit relative path.
- Keep <task_spec> between 50 and 150 lines.
- The user's request may include additional context; respect it precisely.
"""

        try:
            response = self.llm.run_for_agent(self._LLM_AGENT_NAME, prompt).strip()
        except Exception as exc:
            logger.error("LLM task planning failed: %s", exc, exc_info=True)
            fallback = user_message or "(no task description provided)"
            return TaskPlanningResult(detailed_plan=fallback, task_spec=fallback)

        plan = self._parse_plan_sections(response)
        if plan:
            return plan

        logger.warning("LLM response missing plan sections; using raw output.")
        fallback = response or user_message or "(no task description provided)"
        return TaskPlanningResult(detailed_plan=fallback, task_spec=fallback)

    def _parse_plan_sections(self, response: str) -> Optional[TaskPlanningResult]:
        if not response:
            return None

        detailed = self._extract_section(response, "detailed_plan")
        task_spec = self._extract_section(response, "task_spec")

        if not detailed and not task_spec:
            return None

        detailed_content = (detailed or task_spec or "").strip()
        task_spec_content = (task_spec or detailed or "").strip()
        if not detailed_content and not task_spec_content:
            return None

        return TaskPlanningResult(
            detailed_plan=detailed_content or "(no plan generated)",
            task_spec=task_spec_content or "(no task specification generated)",
        )

    @staticmethod
    def _extract_section(text: str, tag: str) -> Optional[str]:
        pattern = re.compile(rf"<{tag}>(.*?)</{tag}>", re.DOTALL | re.IGNORECASE)
        match = pattern.search(text)
        if not match:
            return None
        return match.group(1).strip()

    def _build_specification(
        self,
        task_id: str,
        project_name: str,
        user_message: str,
        task_spec: str,
    ) -> AgentSpecification:
        condensed_spec = (task_spec or "").strip()
        prompt_sections = []
        if condensed_spec:
            prompt_sections.append(condensed_spec)
        original_request = (user_message or "").strip()
        if original_request:
            prompt_sections.append("## Original Request")
            prompt_sections.append(original_request)
        prompt = "\n\n".join(prompt_sections) or original_request or "(no task specification provided)"
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

    def _create_gemini_md(self, project_path: Path, document: str, task_id: str) -> None:
        """
        Persist the task specification to GEMINI.md ahead of session launch.
        """
        content = (document or "").strip()
        if not content:
            content = (
                "# Task Description\n"
                "(no task description provided)\n\n"
                "## Completion Requirements\n"
                f"- Write `.aura/{task_id}.done` when work completes.\n"
                f"- Write `.aura/{task_id}.summary.json` with task metadata.\n"
            )
        gemini_md_path = project_path / "GEMINI.md"
        gemini_md_path.write_text(content + "\n", encoding="utf-8")
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
        aura_dir = project_path / ".aura"
        log_path = aura_dir / f"{task_id}.output.log"
        done_path = aura_dir / f"{task_id}.done"
        summary_path = aura_dir / f"{task_id}.summary.json"
        start_time = time.monotonic()
        completion_reason = "unknown"
        timed_out = False

        logger.debug(
            "Monitoring terminal session %s (log=%s)",
            task_id,
            log_path,
        )

        try:
            while True:
                if done_path.exists():
                    completion_reason = "done-file-detected"
                    break
                if summary_path.exists():
                    completion_reason = "summary-file-detected"
                    break

                exit_code = session.poll()
                if exit_code is not None:
                    completion_reason = "process-exited"
                    break

                elapsed = time.monotonic() - start_time
                if elapsed >= self._SESSION_TIMEOUT_SECONDS:
                    timed_out = True
                    completion_reason = "timeout"
                    break

                time.sleep(self._POLL_INTERVAL_SECONDS)

            duration_seconds = time.monotonic() - start_time
            logger.info(
                "Terminal session %s completion detected via %s after %.2fs",
                task_id,
                completion_reason,
                duration_seconds,
            )
            self._finalize_session(
                session,
                project_path,
                completion_reason=completion_reason,
                duration_seconds=duration_seconds,
                timed_out=timed_out,
            )
        except Exception as exc:
            logger.error("Output monitor error for task %s: %s", task_id, exc, exc_info=True)
            self._dispatch_event(
                TERMINAL_SESSION_FAILED,
                {"task_id": task_id, "failure_reason": "monitor_error", "error_message": str(exc)},
            )

    def _finalize_session(
        self,
        session: TerminalSession,
        project_path: Path,
        *,
        completion_reason: str,
        duration_seconds: float,
        timed_out: bool,
    ) -> None:
        exit_code = session.poll()
        if exit_code is None and not timed_out:
            try:
                exit_code = session.wait(timeout=5.0)
            except Exception as exc:
                logger.debug("Wait for session %s exit failed: %s", session.task_id, exc)

        summary_wait = self._SUMMARY_WAIT_SECONDS if not timed_out else 0.0
        summary_data = self._load_task_summary(
            session.task_id,
            project_path,
            wait_seconds=summary_wait,
        )

        if summary_data.get("execution_time_seconds") is None:
            summary_data["execution_time_seconds"] = round(duration_seconds, 3)

        log_path = project_path / ".aura" / f"{session.task_id}.output.log"
        cli_stats = self._parse_cli_stats(log_path)

        payload: Dict[str, Any] = {
            "task_id": session.task_id,
            "completion_reason": completion_reason,
            "summary_data": summary_data,
            "duration_seconds": round(duration_seconds, 3),
        }

        if exit_code is not None:
            payload["exit_code"] = exit_code
        if timed_out:
            payload["timed_out"] = True
        if cli_stats:
            payload["cli_stats"] = cli_stats
            files_created_count = cli_stats.get("files_created_count")
            if isinstance(files_created_count, int):
                payload["files_created_count"] = files_created_count

        status_value = str(summary_data.get("status") or "").lower()

        event_type = TERMINAL_SESSION_COMPLETED
        failure_reason: Optional[str] = None
        if timed_out:
            event_type = TERMINAL_SESSION_FAILED
            failure_reason = "timeout"
        elif exit_code not in (0, None):
            event_type = TERMINAL_SESSION_FAILED
            failure_reason = "non-zero-exit"
        elif status_value == "failed":
            event_type = TERMINAL_SESSION_FAILED
            failure_reason = "summary-status-failed"

        if failure_reason:
            payload["failure_reason"] = failure_reason

        self._dispatch_event(event_type, payload)
        self._sessions.pop(session.task_id, None)

    def _load_task_summary(
        self,
        task_id: str,
        project_path: Path,
        *,
        wait_seconds: float = 0.0,
    ) -> Dict[str, Any]:
        summary_file = project_path / ".aura" / f"{task_id}.summary.json"
        try:
            if wait_seconds > 0:
                deadline = time.monotonic() + wait_seconds
                while not summary_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.25)

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

    def _parse_cli_stats(self, log_path: Path) -> Optional[Dict[str, Any]]:
        if not log_path.exists():
            logger.debug("Output log not found for stats parsing: %s", log_path)
            return None

        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.debug("Failed reading output log %s: %s", log_path, exc)
            return None

        latest_block = self._extract_latest_json_block(log_text)
        if latest_block is None:
            return None

        stats = latest_block.get("stats")
        if not isinstance(stats, dict):
            return None

        tools = stats.get("tools") if isinstance(stats.get("tools"), dict) else None
        by_name = tools.get("byName") if isinstance(tools, dict) else None
        write_file = by_name.get("write_file") if isinstance(by_name, dict) else None
        files_info = stats.get("files") if isinstance(stats.get("files"), dict) else None

        files_created_count = self._coerce_int(write_file.get("count")) if isinstance(write_file, dict) else None
        lines_added = self._coerce_int(files_info.get("totalLinesAdded")) if isinstance(files_info, dict) else None
        lines_removed = self._coerce_int(files_info.get("totalLinesRemoved")) if isinstance(files_info, dict) else None

        result: Dict[str, Any] = {
            "files_created_count": files_created_count,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "stats": stats,
        }

        if isinstance(tools, dict):
            tool_calls = self._coerce_int(tools.get("totalCalls"))
            if tool_calls is not None:
                result["tool_calls"] = tool_calls

        response = latest_block.get("response")
        if isinstance(response, str) and response.strip():
            result["response"] = response.strip()

        return result

    def _extract_latest_json_block(self, log_text: str) -> Optional[Dict[str, Any]]:
        candidates: list[int] = []
        lowered = log_text.lower()
        idx = lowered.rfind("json{")
        while idx != -1:
            brace_idx = log_text.find("{", idx)
            if brace_idx != -1:
                candidates.append(brace_idx)
            idx = lowered.rfind("json{", 0, idx)

        if not candidates:
            brace_idx = log_text.rfind("{")
            if brace_idx != -1:
                candidates.append(brace_idx)

        for start in candidates:
            block = self._slice_balanced_block(log_text, start)
            if not block:
                continue
            try:
                return json.loads(block)
            except json.JSONDecodeError as exc:
                logger.debug("Failed to parse JSON block at %d: %s", start, exc)
                continue
        return None

    @staticmethod
    def _slice_balanced_block(text: str, start: int) -> Optional[str]:
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            char = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : idx + 1]
        return None

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

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
