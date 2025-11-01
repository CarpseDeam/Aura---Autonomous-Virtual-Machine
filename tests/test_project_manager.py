import json
import time
from pathlib import Path

import pytest

from src.aura.project.project_manager import ProjectManager


@pytest.fixture
def project_paths(tmp_path):
    storage_dir = tmp_path / "projects"
    workspace_dir = tmp_path / "workspace"
    storage_dir.mkdir()
    workspace_dir.mkdir()
    return storage_dir, workspace_dir


def _workspace_root(workspace_dir: Path, name: str) -> str:
    return str((workspace_dir / name).resolve())


def test_create_project_creates_project_json(project_paths):
    storage_dir, workspace_dir = project_paths
    manager = ProjectManager(storage_dir=str(storage_dir))

    project = manager.create_project("alpha", _workspace_root(workspace_dir, "alpha"))

    project_dir = storage_dir / "alpha"
    project_file = project_dir / "project.json"
    assert project_dir.is_dir()
    assert project_file.is_file()

    data = json.loads(project_file.read_text(encoding="utf-8"))
    assert data["name"] == project.name
    assert data["root_path"] == project.root_path
    assert data["conversation_history"] == []


def test_save_and_load_project_round_trip(project_paths):
    storage_dir, workspace_dir = project_paths
    manager = ProjectManager(storage_dir=str(storage_dir))
    project = manager.create_project("beta", _workspace_root(workspace_dir, "beta"))

    entry = {"role": "user", "content": "Hello there"}
    project.conversation_history.append(entry)
    manager.save_project(project)

    reloaded_manager = ProjectManager(storage_dir=str(storage_dir))
    loaded_project = reloaded_manager.load_project("beta")

    assert loaded_project.conversation_history[-1]["content"] == entry["content"]
    assert loaded_project.name == "beta"


def test_save_project_updates_last_active(project_paths):
    storage_dir, workspace_dir = project_paths
    manager = ProjectManager(storage_dir=str(storage_dir))
    project = manager.create_project("gamma", _workspace_root(workspace_dir, "gamma"))

    initial_last_active = project.last_active
    time.sleep(0.01)
    manager.save_project(project)

    assert project.last_active > initial_last_active


def test_switch_project_persists_current_state(project_paths):
    storage_dir, workspace_dir = project_paths
    manager = ProjectManager(storage_dir=str(storage_dir))
    project_a = manager.create_project("delta", _workspace_root(workspace_dir, "delta"))
    manager.create_project("epsilon", _workspace_root(workspace_dir, "epsilon"))

    manager.current_project = project_a
    project_a.conversation_history.append({"role": "user", "content": "Work on delta"})
    project_a.metadata["recent_topics"] = ["delta topic"]

    switched = manager.switch_project("epsilon")
    assert switched.name == "epsilon"

    validation_manager = ProjectManager(storage_dir=str(storage_dir))
    delta = validation_manager.load_project("delta")
    assert delta.conversation_history[-1]["content"] == "Work on delta"
    assert delta.metadata.get("recent_topics") == ["delta topic"]


def test_conversation_history_persists_between_sessions(project_paths):
    storage_dir, workspace_dir = project_paths
    manager = ProjectManager(storage_dir=str(storage_dir))
    project = manager.create_project("zeta", _workspace_root(workspace_dir, "zeta"))

    turns = [
        {"role": "user", "content": "Start project"},
        {"role": "assistant", "content": "Acknowledged"},
    ]
    project.conversation_history.extend(turns)
    manager.save_project(project)

    reloaded_manager = ProjectManager(storage_dir=str(storage_dir))
    reloaded = reloaded_manager.load_project("zeta")

    assert reloaded.conversation_history == turns
