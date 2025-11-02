from __future__ import annotations

import importlib
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pytest

from src.aura.executor import AuraExecutor
from src.aura.models.action import Action, ActionType
from src.aura.models.agent_task import AgentSpecification, TerminalSession
from src.aura.models.project_context import ProjectContext


def _build_executor() -> AuraExecutor:
    event_bus = MagicMock()
    llm = MagicMock()
    prompts = MagicMock()
    prompts.render.return_value = "prompt"
    workspace = MagicMock()
    workspace.active_project_path = Path(".")
    workspace.list_workspace_projects.return_value = []
    workspace.get_project_files.return_value = []
    workspace.file_exists.return_value = False
    workspace.get_file_content.return_value = ""
    workspace.save_code_to_project.return_value = None
    file_registry = MagicMock()
    file_registry.list_files.return_value = []
    file_registry.refresh.return_value = None
    terminal_service = MagicMock()
    workspace_monitor = MagicMock()
    terminal_session_manager = MagicMock()
    return AuraExecutor(
        event_bus,
        llm,
        prompts,
        workspace,
        file_registry,
        terminal_service,
        workspace_monitor,
        terminal_session_manager,
    )


@pytest.mark.parametrize(
    "action_type",
    [
        ActionType.DESIGN_BLUEPRINT,
        ActionType.REFINE_CODE,
        ActionType.DISCUSS,
        ActionType.SIMPLE_REPLY,
        ActionType.RESEARCH,
        ActionType.LIST_FILES,
        ActionType.READ_FILE,
    ],
)
def test_execute_routes_actions_to_registered_handler(action_type: ActionType) -> None:
    executor = _build_executor()
    ctx = ProjectContext()
    action = Action(type=action_type, params={})
    sentinel = object()
    handler = MagicMock(return_value=sentinel)
    executor._tools[action_type] = handler

    result = executor.execute(action, ctx)
    assert result is sentinel
    handler.assert_called_once_with(action, ctx)


def test_executor_modules_under_line_limit() -> None:
    module_paths: List[Path] = [
        Path("src/aura/executor/executor.py"),
        Path("src/aura/executor/conversation_handler.py"),
        Path("src/aura/executor/blueprint_handler.py"),
        Path("src/aura/executor/project_resolver.py"),
        Path("src/aura/executor/prompt_builder.py"),
        Path("src/aura/executor/code_sanitizer.py"),
        Path("src/aura/executor/conversation_utils.py"),
        Path("src/aura/executor/project_match_utils.py"),
        Path("src/aura/executor/file_operations.py"),
    ]

    # Allow slightly more for main files
    limits = {
        "executor.py": 250,
        "blueprint_handler.py": 350,  # Handles complex blueprint parsing
    }

    for path in module_paths:
        if not path.exists():
            continue  # Skip non-existent files
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        limit = limits.get(path.name, 200)
        assert line_count <= limit, f"{path} exceeds {limit} lines (found {line_count})"


def test_executor_imports_still_work() -> None:
    module = importlib.import_module("src.aura.executor")
    assert hasattr(module, "AuraExecutor")


# -- Design blueprint workflow tests ---------------------------------------------------


def test_design_blueprint_stores_spec_in_context() -> None:
    """Test that _handle_design_blueprint stores specification in context.extras."""
    executor = _build_executor()
    ctx = ProjectContext()

    executor.file_registry.list_files.return_value = ["src/main.py"]  # type: ignore[assignment]

    # Create a mock specification
    mock_spec = AgentSpecification(
        task_id="test-123",
        request="build web server",
        project_name="test-project",
        blueprint={"files": []},
        prompt="test prompt",
        files_to_watch=[],
    )

    # Mock the blueprint handler
    executor.blueprint_handler.execute_design_blueprint = MagicMock(return_value=mock_spec)

    # Create action with auto_spawn=False to skip terminal spawning
    action = Action(type=ActionType.DESIGN_BLUEPRINT, params={"request": "build web server", "auto_spawn": False})

    result = executor.execute(action, ctx)

    # Verify spec is stored in context
    assert "latest_specification" in ctx.extras
    assert ctx.extras["latest_specification"]["task_id"] == "test-123"
    assert ctx.active_files == ["src/main.py"]
    assert isinstance(result, AgentSpecification)


def test_design_blueprint_skips_auto_spawn_when_disabled() -> None:
    """Test that auto-spawn can be disabled via action params."""
    executor = _build_executor()
    ctx = ProjectContext()

    mock_spec = AgentSpecification(
        task_id="test-456",
        request="test",
        project_name="test-project",
        blueprint={},
        prompt="prompt",
        files_to_watch=[],
    )

    executor.blueprint_handler.execute_design_blueprint = MagicMock(return_value=mock_spec)

    # Disable auto-spawn
    action = Action(type=ActionType.DESIGN_BLUEPRINT, params={"auto_spawn": False})
    executor.execute(action, ctx)

    # Terminal service should not be called
    executor.terminal_service.spawn_agent.assert_not_called()
    executor.terminal_session_manager.register_session.assert_not_called()
    assert "last_terminal_session" not in ctx.extras
    assert ctx.extras["latest_specification"]["task_id"] == "test-456"


def test_design_blueprint_auto_spawn_registers_session() -> None:
    """Test that DESIGN_BLUEPRINT auto-spawns terminal session by default."""
    executor = _build_executor()
    ctx = ProjectContext()

    executor.file_registry.list_files.return_value = ["src/main.py"]  # type: ignore[assignment]
    spec = AgentSpecification(
        task_id="auto-001",
        request="build service",
        project_name="proj",
        blueprint={"files": [{"file_path": "src/main.py"}]},
        prompt="prompt",
        files_to_watch=[],
    )
    executor.blueprint_handler.execute_design_blueprint = MagicMock(return_value=spec)
    session = TerminalSession(task_id="auto-001", command=["bash"], spec_path="spec.json")
    # Supervised spawn returns (session, process)
    process = MagicMock()
    executor.terminal_service.spawn_agent_for_supervision.return_value = (session, process)  # type: ignore[assignment]

    action = Action(type=ActionType.DESIGN_BLUEPRINT, params={"request": "build service"})
    result = executor.execute(action, ctx)

    assert result is spec
    executor.terminal_service.spawn_agent_for_supervision.assert_called_once_with(spec)
    # Should register with process handle for I/O capture
    executor.terminal_session_manager.register_session.assert_called_once()
    assert ctx.extras["latest_specification"]["task_id"] == "auto-001"
    assert ctx.extras["last_terminal_session"] == session.model_dump()
    assert ctx.active_files == ["src/main.py"]


def test_design_blueprint_raises_for_invalid_result_type() -> None:
    """Handler must return an AgentSpecification."""
    executor = _build_executor()
    ctx = ProjectContext()

    executor.blueprint_handler.execute_design_blueprint = MagicMock(return_value={"spec": "invalid"})

    action = Action(type=ActionType.DESIGN_BLUEPRINT, params={})
    with pytest.raises(TypeError) as exc:
        executor.execute(action, ctx)

    assert "AgentSpecification" in str(exc.value)


def test_refine_code_builds_manual_specification() -> None:
    """Test that REFINE_CODE action builds specification from params."""
    executor = _build_executor()
    ctx = ProjectContext()

    mock_spec = AgentSpecification(
        task_id="refine-123",
        request="refine code",
        project_name="test",
        blueprint={"manual": True},
        prompt="refine prompt",
        files_to_watch=["src/main.py"],
    )

    executor.blueprint_handler.build_manual_specification = MagicMock(return_value=mock_spec)

    action = Action(
        type=ActionType.REFINE_CODE,
        params={
            "request": "improve error handling",
            "files": ["src/main.py"],
            "notes": ["add try/catch"],
        },
    )

    result = executor.execute(action, ctx)

    # Verify build_manual_specification was called with correct params
    executor.blueprint_handler.build_manual_specification.assert_called_once()
    call_kwargs = executor.blueprint_handler.build_manual_specification.call_args[1]
    assert call_kwargs["request"] == "improve error handling"
    assert call_kwargs["target_files"] == ["src/main.py"]
    assert call_kwargs["notes"] == ["add try/catch"]

    assert isinstance(result, AgentSpecification)
