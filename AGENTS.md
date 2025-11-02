# Aura Coding Standards

These conventions capture how the Aura engineering team writes code today. Study these patterns before you touch the repository.

## Engineering Voice
- Ship production-ready code with pragmatic scope—keep modules focused and composable (for example, `src/aura/services/memory_manager.py` balances responsibilities across helpers).
- Match the existing tone in docstrings and comments: short, actionable, and grounded in the user's workflow.
- Default to collaboration. Surface assumptions and invite follow-up when scope feels ambiguous.

## Naming & Structure
- Modules import `from __future__ import annotations` when future annotations simplify type hints (`src/aura/services/terminal_agent_service.py`, `src/aura/executor/file_operations.py`).
- Classes use PascalCase (`MemoryManager`, `TerminalAgentService`); functions and methods stay snake_case.
- Data models inherit from `pydantic.BaseModel` and use `Field` metadata for defaults and validation (see `src/aura/models/agent_task.py` and `src/aura/models/context_models.py`).
- Keep public APIs small and explicit. Inject dependencies through constructors rather than accessing globals.

## Error Handling
- Validate input data early and raise descriptive `ValueError` or `RuntimeError` exceptions (see `FileOperations.execute_read_file` in `src/aura/executor/file_operations.py`).
- Wrap filesystem and external I/O in `try/except`, log the failure with `%s` formatting and `exc_info=True`, then re-raise a domain-specific error (`src/aura/executor/file_operations.py:33-48`).
- When a custom exception fits better, use the dedicated types in `src/aura/models/exceptions.py`.

## Logging
- Declare a module-level logger with `logger = logging.getLogger(__name__)`.
- Use `logger.info` for state changes, `logger.debug` for diagnostic context, and `logger.error(..., exc_info=True)` for failures (`src/aura/services/workspace_service.py`, `src/aura/services/terminal_agent_service.py`).
- Include identifiers like task IDs or file paths so operators can trace behaviour quickly.

## Type Hints & Docstrings
- Annotate every function signature and class attribute. Prefer concrete container types (e.g., `List[str]`, `Dict[str, Any]`).
- Provide concise docstrings for modules, classes, and public methods describing purpose and important arguments (`MemoryManager.get_memory`, `TerminalAgentService.spawn_agent`).
- Use Google-style sections when parameters need explanation; keep docstrings short and direct.

## Tests & Examples
- Add pytest cases in `tests/` that assert observable behaviour (e.g., `tests/test_terminal_agent_service.py` checks filesystem writes and command wiring).
- Favour deterministic tests and avoid mocking internal details unless required for isolation.

## Implementation Workflow
1. Run `LIST_FILES` on the relevant directories to confirm the current layout.
2. `READ_FILE` the modules you'll touch plus two or three close neighbours to absorb naming, logging, and error-handling patterns:
   - Services: `src/aura/services/memory_manager.py`, `src/aura/services/workspace_service.py`
   - Executor logic: `src/aura/executor/file_operations.py`, `src/aura/executor/executor.py`
   - Data models: `src/aura/models/agent_task.py`, `src/aura/models/context_models.py`
3. Mirror the import order, logger usage, and guard clauses you observe.
4. Consider unhappy paths. Log failures with actionable context and raise clear exceptions.
5. Update or extend tests whenever logic changes or new behaviour appears.

## Definition of Done
- Code compiles and relevant tests pass.
- No placeholder implementations or TODOs remain.
- Changes integrate smoothly with project memory by emitting informative logs and docstrings.
- When work completes, write `.aura/{task_id}.done` containing a short summary of the outcome.
