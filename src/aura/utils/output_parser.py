"""Helpers to interpret streaming output from terminal agents."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path


logger = logging.getLogger(__name__)


@dataclass
class OutputParserResult:
    """Represents the interpreted state of recent agent output."""

    is_complete: bool
    completion_reason: str | None = None


class OutputParser:
    """Lightweight parser for terminal agent output streams."""

    _COMPLETION_MARKERS = (
        "task complete",
        "task completed",
        "all tasks complete",
        "finished task",
    )

    def __init__(self, project_root: Path, task_id: str) -> None:
        self.project_root = Path(project_root)
        self.task_id = task_id
        self._done_file = self.project_root / ".aura" / f"{task_id}.done"
        self._summary_file = self.project_root / ".aura" / f"{task_id}.summary.json"

    def analyze(self, new_text: str, process_running: bool) -> OutputParserResult:
        """
        Inspect recent output and process state to detect completion signals.
        """
        if self._done_file.exists():
            return OutputParserResult(True, "done-file-detected")

        if self._summary_file.exists():
            return OutputParserResult(True, "summary-file-detected")

        text = (new_text or "").strip()
        if text:
            lower_text = text.lower()
            if any(marker in lower_text for marker in self._COMPLETION_MARKERS):
                return OutputParserResult(True, "completion-marker-detected")

        if not process_running and text:
            return OutputParserResult(True, "process-exited-after-output")

        if not process_running:
            return OutputParserResult(True, "process-exited")

        return OutputParserResult(False, None)


def read_new_text(log_path: Path, position: int) -> tuple[str, int]:
    """
    Read newly appended text from a log file.
    """
    if not log_path.exists():
        return "", position
    with log_path.open("r", encoding="utf-8", errors="replace") as stream:
        stream.seek(position)
        text = stream.read()
        new_position = stream.tell()
    return text, new_position
