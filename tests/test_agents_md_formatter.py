from __future__ import annotations

from typing import Callable, Iterable

import pytest

from src.aura.models.agent_task import AgentSpecification
from src.aura.services.agents_md_formatter import format_specification_for_gemini


def test_format_specification_for_gemini_includes_task_context(
    agent_spec_factory: Callable[..., AgentSpecification],
) -> None:
    spec = agent_spec_factory(
        files_to_watch=["src/main.py", "src/utils.py"],
        blueprint={"files": [{"file_path": "src/main.py"}]},
    )

    document = format_specification_for_gemini(spec)

    assert "# Aura Coding Standards" in document
    assert f"- Project: {spec.project_name}" in document
    assert f"- Task ID: {spec.task_id}" in document
    assert "## File Checklist" in document
    assert "- src/main.py" in document
    assert "## Files to Monitor" in document


def test_format_specification_for_gemini_sanitizes_blank_prompt(
    agent_spec_factory: Callable[..., AgentSpecification],
) -> None:
    spec = agent_spec_factory(prompt="   ", request="")

    document = format_specification_for_gemini(spec)

    assert "(no task specification provided)" in document
    assert "## Completion Requirements" in document


def test_format_specification_for_gemini_excludes_sensitive_metadata(
    agent_spec_factory: Callable[..., AgentSpecification],
) -> None:
    spec = agent_spec_factory(metadata={"api_key": "secret-token"})

    document = format_specification_for_gemini(spec)

    assert "secret-token" not in document


@pytest.mark.parametrize(
    ("files_to_watch", "expected_items"),
    [
        (["src/repeat.py", "src/repeat.py", "   "], ["- src/repeat.py"]),
        ([], []),
    ],
)
def test_format_specification_for_gemini_normalizes_file_sequences(
    agent_spec_factory: Callable[..., AgentSpecification],
    files_to_watch: Iterable[str],
    expected_items: Iterable[str],
) -> None:
    spec = agent_spec_factory(
        files_to_watch=list(files_to_watch),
        blueprint={"files": [{"file_path": "src/repeat.py"}]},
    )

    document = format_specification_for_gemini(spec)

    for item in expected_items:
        assert item in document


def test_format_specification_for_gemini_avoids_redundant_blank_lines(
    agent_spec_factory: Callable[..., AgentSpecification],
) -> None:
    spec = agent_spec_factory()
    document = format_specification_for_gemini(spec)

    assert "\n\n\n" not in document
