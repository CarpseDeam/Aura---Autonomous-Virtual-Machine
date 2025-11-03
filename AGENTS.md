# Aura Coding Standards

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
---
# Task
Testing terminal agent input/output functionality

## Files
- No specific files identified.

## Context
- Project: (unspecified)
- Task ID: test-io-001
- Request: Test terminal I/O

## Completion & Summary
- Write `.aura/test-io-001.done` when the task is finished.
- Also write `.aura/test-io-001.summary.json` capturing:
  - status: one of `completed`, `failed`, or `partial`
  - files_created: list of file paths created
  - files_modified: list of file paths modified
  - files_deleted: list of file paths deleted
  - errors: list of error strings (if any)
  - warnings: list of warning strings (if any)
  - execution_time_seconds: total number of seconds taken
  - suggestions: list of concise next-step suggestions
