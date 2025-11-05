"""Utilities for serializing AgentSpecification data into Claude-friendly CLAUDE.md files."""

from __future__ import annotations

from typing import List, Sequence, Set

from src.aura.models.agent_task import AgentSpecification


BASE_GUIDELINES = """# Aura Coding Standards

These conventions capture how the Aura engineering team writes code today. Study these patterns before you touch the repository.

## Engineering Voice
- Ship production-ready code with pragmatic scope; keep modules focused and composable (for example, `src/aura/services/terminal_agent_service.py` balances responsibilities across helpers).
- Match the existing tone in docstrings and comments: short, actionable, and grounded in the user's workflow.
- Default to collaboration. Surface assumptions and invite follow-up when scope feels ambiguous.

## Naming & Structure
- Modules import `from __future__ import annotations` when future annotations simplify type hints (`src/aura/services/terminal_agent_service.py`, `src/aura/services/workspace_service.py`).
- Classes use PascalCase (`AgentSupervisor`, `TerminalAgentService`); functions and methods stay snake_case.
- Data models inherit from `pydantic.BaseModel` and use `Field` metadata for defaults and validation (see `src/aura/models/agent_task.py`).
- Keep public APIs small and explicit. Inject dependencies through constructors rather than accessing globals.

## Error Handling
- Validate input data early and raise descriptive `ValueError` or `RuntimeError` exceptions (see `_persist_specification` in `src/aura/services/terminal_agent_service.py`).
- Wrap filesystem and external I/O in `try/except`, log the failure with `%s` formatting and `exc_info=True`, then re-raise a domain-specific error (`AgentSupervisor.process_message` propagates terminal launch failures).
- When a custom exception fits better, use the dedicated types in `src/aura/models/exceptions.py`.

## Logging
- Declare a module-level logger with `logger = logging.getLogger(__name__)`.
- Use `logger.info` for state changes, `logger.debug` for diagnostic context, and `logger.error(..., exc_info=True)` for failures (`src/aura/services/workspace_service.py`, `src/aura/services/terminal_agent_service.py`).
- Include identifiers like task IDs or file paths so operators can trace behaviour quickly.

## Type Hints & Docstrings
- Annotate every function signature and class attribute. Prefer concrete container types (e.g., `List[str]`, `Dict[str, Any]`).
- Provide concise docstrings for modules, classes, and public methods describing purpose and important arguments (`AgentSupervisor.process_message`, `TerminalAgentService.spawn_agent`).
- Use Google-style sections when parameters need explanation; keep docstrings short and direct.

## Tests & Examples
- Add pytest cases in `tests/` that assert observable behaviour (e.g., `tests/unit/test_terminal_agent_service.py` checks filesystem writes and command wiring).
- Favour deterministic tests and avoid mocking internal details unless required for isolation.

## Implementation Workflow
1. Run `LIST_FILES` on the relevant directories to confirm the current layout.
2. `READ_FILE` the modules you'll touch plus two or three close neighbours to absorb naming, logging, and error-handling patterns:
   - Services: `src/aura/services/agent_supervisor.py`, `src/aura/services/terminal_agent_service.py`
   - Workspace & persistence: `src/aura/services/workspace_service.py`, `src/aura/services/conversation_management_service.py`
   - Data models: `src/aura/models/agent_task.py`
3. Mirror the import order, logger usage, and guard clauses you observe.
4. Consider unhappy paths. Log failures with actionable context and raise clear exceptions.
5. Update or extend tests whenever logic changes or new behaviour appears.

## Definition of Done
- Code compiles and relevant tests pass.
- No placeholder implementations or TODOs remain.
- Changes integrate smoothly with project memory by emitting informative logs and docstrings.
- When work completes, write `.aura/{task_id}.done` containing a short summary of the outcome.
"""


def format_specification_for_claude(spec: AgentSpecification) -> str:
    """
    Convert an AgentSpecification into CLAUDE.md format.

    CLAUDE.md is Claude Code's native project context file format.
    It should be placed in the project root where Claude will auto-read it.
    """
    file_entries = _collect_file_paths(spec)

    lines: List[str] = [
        BASE_GUIDELINES.strip(),
        "---",
        "# Task",
        _sanitize_block(spec.prompt),
        "",
        "## Files to Create/Modify",
    ]

    if file_entries:
        lines.extend(f"- {path}" for path in file_entries)
    else:
        lines.append("- No specific files identified.")

    lines.extend(
        [
            "",
            "## Context",
            f"- Project: {spec.project_name or '(unspecified)'}",
            f"- Task ID: {spec.task_id}",
            f"- Request: {spec.request.strip() or '(no request provided)'}",
        ]
    )

    watch_paths = _normalize_sequence(spec.files_to_watch)
    if watch_paths:
        lines.append("")
        lines.append("## Files to Monitor")
        lines.extend(f"- {path}" for path in watch_paths)

    lines.append("")
    lines.append("## Completion Requirements")
    lines.append(f"- Write `.aura/{spec.task_id}.done` when the task is finished.")
    lines.append(f"- Write `.aura/{spec.task_id}.summary.json` with the following structure:")
    lines.append("  - status: one of `completed`, `failed`, or `partial`")
    lines.append("  - files_created: list of file paths created")
    lines.append("  - files_modified: list of file paths modified")
    lines.append("  - files_deleted: list of file paths deleted")
    lines.append("  - errors: list of error strings (if any)")
    lines.append("  - warnings: list of warning strings (if any)")
    lines.append("  - execution_time_seconds: total number of seconds taken")
    lines.append("  - suggestions: list of concise next-step suggestions")

    return "\n".join(lines).strip() + "\n"


def _sanitize_block(value: str) -> str:
    result = (value or "").strip()
    return result or "(no task description provided)"


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


def _normalize_sequence(items: Sequence[str]) -> List[str]:
    normalized: List[str] = []
    for item in items or []:
        value = (item or "").strip()
        if value:
            normalized.append(value)
    return normalized
