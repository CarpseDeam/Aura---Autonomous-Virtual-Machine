"""Terminal I/O supervision handlers.

Provides read/write access to active terminal sessions via TerminalSessionManager.
"""

from __future__ import annotations

import logging
from typing import Any

from src.aura.models.action import Action
from src.aura.models.project_context import ProjectContext
from src.aura.services.terminal_session_manager import TerminalSessionManager


logger = logging.getLogger(__name__)


class TerminalSupervisor:
    """Handlers for reading and writing terminal I/O."""

    def __init__(self, session_manager: TerminalSessionManager) -> None:
        self.session_manager = session_manager

    def _resolve_task_id(self, action: Action, context: ProjectContext) -> str:
        task_id = action.get_param("task_id")
        if isinstance(task_id, str) and task_id:
            return task_id
        last = (context.extras or {}).get("last_terminal_session")
        if isinstance(last, dict) and isinstance(last.get("task_id"), str):
            return last["task_id"]
        raise ValueError("Missing 'task_id' and no recent terminal session in context")

    def execute_read_terminal_output(self, action: Action, context: ProjectContext) -> str:
        task_id = self._resolve_task_id(action, context)
        max_lines = action.get_param("max_lines", 100)
        include_stderr = action.get_param("include_stderr", True)
        try:
            return self.session_manager.read_terminal_output(
                task_id=task_id,
                max_lines=int(max_lines) if isinstance(max_lines, int) else 100,
                include_stderr=bool(include_stderr),
            )
        except Exception as exc:
            logger.error("Failed to read terminal output for task %s: %s", task_id, exc, exc_info=True)
            raise RuntimeError(f"Failed to read terminal output for {task_id}") from exc

    def execute_send_to_terminal(self, action: Action, context: ProjectContext) -> str:
        task_id = self._resolve_task_id(action, context)
        message = action.get_param("message")
        if not isinstance(message, str) or not message:
            raise ValueError("SEND_TO_TERMINAL requires a non-empty 'message'")
        try:
            self.session_manager.send_to_terminal(task_id, message)
            return "ok"
        except Exception as exc:
            logger.error("Failed to send message to terminal for task %s: %s", task_id, exc, exc_info=True)
            raise

