from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from src.aura.models.agent_task import AgentSpecification
from src.aura.services.agent_supervisor import AgentSupervisor
from src.aura.services.agents_md_formatter import format_specification_for_gemini


def _build_supervisor() -> AgentSupervisor:
    llm = MagicMock()
    terminal_service = MagicMock()
    workspace_service = MagicMock()
    event_bus = MagicMock()
    return AgentSupervisor(llm, terminal_service, workspace_service, event_bus)


def test_generate_task_plan_splits_dual_sections() -> None:
    supervisor = _build_supervisor()
    supervisor.llm.run_for_agent.return_value = (
        "<detailed_plan>Detailed steps here.</detailed_plan>\n"
        "<task_spec># Task: Build tool</task_spec>"
    )

    plan = supervisor._generate_task_plan("Build something great")

    assert plan.detailed_plan == "Detailed steps here."
    assert plan.task_spec == "# Task: Build tool"
    supervisor.llm.run_for_agent.assert_called_once()


def test_generate_task_plan_falls_back_when_sections_missing() -> None:
    supervisor = _build_supervisor()
    supervisor.llm.run_for_agent.return_value = "Plain response without markers"

    plan = supervisor._generate_task_plan("Document behaviour")

    assert plan.detailed_plan == "Plain response without markers"
    assert plan.task_spec == "Plain response without markers"


def test_generate_task_plan_handles_llm_failure() -> None:
    supervisor = _build_supervisor()
    supervisor.llm.run_for_agent.side_effect = RuntimeError("provider unavailable")

    plan = supervisor._generate_task_plan("Handle failures gracefully")

    assert plan.detailed_plan == "Handle failures gracefully"
    assert plan.task_spec == "Handle failures gracefully"


def test_parse_cli_stats_extracts_recent_json(tmp_path: Path) -> None:
    supervisor = _build_supervisor()
    log_path = tmp_path / "task.output.log"
    log_path.write_text(
        "preface line\n"
        "json{\"stats\": {\"tools\": {}}}\n"
        "json{\n"
        '  "response": "All tasks complete.",\n'
        '  "stats": {\n'
        '    "tools": {\n'
        '      "totalCalls": 7,\n'
        '      "byName": {\n'
        '        "write_file": {"count": 5}\n'
        "      }\n"
        "    },\n"
        '    "files": {\n'
        '      "totalLinesAdded": 120,\n'
        '      "totalLinesRemoved": 3\n'
        "    }\n"
        "  }\n"
        "}\n"
        "trailing text\n",
        encoding="utf-8",
    )

    stats = supervisor._parse_cli_stats(log_path)

    assert stats is not None
    assert stats["files_created_count"] == 5
    assert stats["lines_added"] == 120
    assert stats["lines_removed"] == 3
    assert stats["tool_calls"] == 7
    assert stats["response"] == "All tasks complete."
    assert isinstance(stats["stats"], dict)


def test_format_specification_for_gemini_includes_condensed_spec() -> None:
    spec = AgentSpecification(
        task_id="abc123",
        request="Create a demo",
        project_name="sample_project",
        prompt="# Task: Demo build\n\n## Requirements\n- Item A",
        blueprint={"files": [{"file_path": "src/demo.py"}]},
        files_to_watch=["src/demo.py"],
    )

    document = format_specification_for_gemini(spec)

    assert "# Aura Coding Standards" in document
    assert "# Task: Demo build" in document
    assert "## Task Context" in document
    assert "## File Checklist" in document
    assert "- src/demo.py" in document
    assert "## Files to Monitor" in document
    assert "## Completion Requirements" in document
