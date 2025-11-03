from __future__ import annotations

from pathlib import Path

from src.aura.utils.output_parser import OutputParser


def test_output_parser_detects_done_file(tmp_path: Path) -> None:
    task_id = "task123"
    aura_dir = tmp_path / ".aura"
    aura_dir.mkdir(parents=True, exist_ok=True)
    (aura_dir / f"{task_id}.done").write_text("", encoding="utf-8")

    parser = OutputParser(tmp_path, task_id)
    result = parser.analyze("", process_running=True)

    assert result.is_complete
    assert result.completion_reason == "done-file-detected"


def test_output_parser_detects_completion_marker(tmp_path: Path) -> None:
    parser = OutputParser(tmp_path, "task789")

    result = parser.analyze("Task completed successfully", process_running=True)

    assert result.is_complete
    assert result.completion_reason == "completion-marker-detected"


def test_output_parser_marks_process_exit_when_idle(tmp_path: Path) -> None:
    parser = OutputParser(tmp_path, "task999")

    result = parser.analyze("", process_running=False)

    assert result.is_complete
    assert result.completion_reason == "process-exited"
