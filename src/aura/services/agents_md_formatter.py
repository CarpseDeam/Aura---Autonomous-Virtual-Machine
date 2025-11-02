"""Utilities for serializing AgentSpecification data into Codex-friendly AGENTS.md files."""

from __future__ import annotations
from typing import List, Set

from src.aura.models.agent_task import AgentSpecification


def format_specification_for_codex(spec: AgentSpecification) -> str:
    """Convert an AgentSpecification into the AGENTS.md structure expected by Codex."""
    file_entries = _collect_file_paths(spec)

    lines = [
        "# Task",
        spec.prompt.rstrip(),
        "",
        "## Files",
    ]

    if file_entries:
        lines.extend(f"- {path}" for path in file_entries)
    else:
        lines.append("- No specific files identified.")

    context_lines = [
        "",
        "## Context",
        f"- Project: {spec.project_name or '(unspecified)'}",
        f"- Task ID: {spec.task_id}",
        f"- Request: {spec.request.strip() or '(no request provided)'}",
    ]

    lines.extend(context_lines)

    return "\n".join(lines).strip() + "\n"


def _collect_file_paths(spec: AgentSpecification) -> List[str]:
    seen: Set[str] = set()
    ordered_paths: List[str] = []

    def _push(path: str) -> None:
        normalized = path.strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        ordered_paths.append(normalized)

    blueprint = spec.blueprint if isinstance(spec.blueprint, dict) else {}
    files_section = blueprint.get("files")
    if isinstance(files_section, list):
        for entry in files_section:
            if isinstance(entry, dict):
                file_path = entry.get("file_path")
                if isinstance(file_path, str):
                    _push(file_path)

    blueprint_section = blueprint.get("blueprint")
    if isinstance(blueprint_section, dict):
        for file_path in blueprint_section.keys():
            if isinstance(file_path, str):
                _push(file_path)

    for file_path in spec.files_to_watch:
        if isinstance(file_path, str):
            _push(file_path)

    return ordered_paths
