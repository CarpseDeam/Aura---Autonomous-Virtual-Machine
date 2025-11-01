from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from src.aura.executor import AuraExecutor
from src.aura.models.action import Action, ActionType
from src.aura.models.project_context import ProjectContext


def _build_executor() -> AuraExecutor:
    event_bus = MagicMock()
    llm = MagicMock()
    prompts = MagicMock()
    prompts.render.return_value = "prompt"
    ast = MagicMock()
    context = MagicMock()
    context.get_context_for_task.return_value = []
    context._read_file_content.return_value = ""  # type: ignore[attr-defined]
    workspace = MagicMock()
    workspace.active_project_path = Path(".")
    workspace.list_workspace_projects.return_value = []
    workspace.get_project_files.return_value = []
    workspace.file_exists.return_value = False
    workspace.get_file_content.return_value = ""
    workspace.save_code_to_project.return_value = None
    return AuraExecutor(event_bus, llm, prompts, ast, context, workspace)


def test_public_api_execute_and_blueprint_delegates() -> None:
    executor = _build_executor()
    ctx = ProjectContext()

    discuss_action = Action(type=ActionType.DISCUSS, params={})
    discuss_result = object()
    discuss_mock = MagicMock(return_value=discuss_result)
    executor._tools[ActionType.DISCUSS] = discuss_mock

    assert executor.execute(discuss_action, ctx) is discuss_result
    discuss_mock.assert_called_once_with(discuss_action, ctx)

    blueprint_data: Dict[str, Any] = {"files": [{"file_path": "foo.py"}]}
    executor.blueprint_handler.execute_design_blueprint = MagicMock(return_value=blueprint_data)  # type: ignore[assignment]
    executor.blueprint_handler.files_from_blueprint = MagicMock(return_value=[{"file_path": "foo.py"}])  # type: ignore[assignment]
    executor.code_generator.execute_generate_code_for_spec = MagicMock()  # type: ignore[assignment]

    result = executor.execute_blueprint("generate foo", ctx)
    assert result["planned_files"] == ["foo.py"]
    executor.code_generator.execute_generate_code_for_spec.assert_called_once_with({"file_path": "foo.py"}, "generate foo")


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
        ActionType.WRITE_FILE,
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
        Path("src/aura/executor/code_generator.py"),
        Path("src/aura/executor/file_operations.py"),
        Path("src/aura/executor/conversation_handler.py"),
        Path("src/aura/executor/blueprint_handler.py"),
        Path("src/aura/executor/project_resolver.py"),
        Path("src/aura/executor/prompt_builder.py"),
        Path("src/aura/executor/code_sanitizer.py"),
        Path("src/aura/executor/conversation_utils.py"),
        Path("src/aura/executor/project_match_utils.py"),
        Path("src/aura/executor/code_generation_stream.py"),
    ]

    for path in module_paths:
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        assert line_count <= 200, f"{path} exceeds 200 lines (found {line_count})"


def test_executor_imports_still_work() -> None:
    module = importlib.import_module("src.aura.executor")
    assert hasattr(module, "AuraExecutor")
