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


# -- Metadata Persistence Tests --------------------------------------------------------


def test_project_metadata_storage_persists(project_paths):
    """Test that project metadata is saved and loaded correctly."""
    storage_dir, workspace_dir = project_paths
    manager = ProjectManager(storage_dir=str(storage_dir))
    project = manager.create_project("metadata_test", _workspace_root(workspace_dir, "metadata_test"))

    # Add various metadata
    project.metadata["recent_topics"] = ["authentication", "database design"]
    project.metadata["current_language"] = "python"
    project.metadata["tech_stack"] = ["FastAPI", "PostgreSQL", "Redis"]
    project.metadata["custom_flag"] = True

    manager.save_project(project)

    # Reload and verify
    reloaded_manager = ProjectManager(storage_dir=str(storage_dir))
    reloaded = reloaded_manager.load_project("metadata_test")

    assert reloaded.metadata["recent_topics"] == ["authentication", "database design"]
    assert reloaded.metadata["current_language"] == "python"
    assert reloaded.metadata["tech_stack"] == ["FastAPI", "PostgreSQL", "Redis"]
    assert reloaded.metadata["custom_flag"] is True


def test_project_memory_data_persists_in_metadata(project_paths):
    """Test that project memory data stored in metadata persists correctly."""
    storage_dir, workspace_dir = project_paths
    manager = ProjectManager(storage_dir=str(storage_dir))
    project = manager.create_project("memory_persist", _workspace_root(workspace_dir, "memory_persist"))

    # Simulate storing memory data
    project.metadata["project_memory"] = {
        "project_name": "memory_persist",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "architecture_decisions": [
            {
                "category": "Backend",
                "decision": "Use FastAPI",
                "rationale": "Modern async framework",
                "decided_at": "2024-01-01T00:00:00Z",
            }
        ],
        "code_patterns": [
            {
                "category": "Error Handling",
                "description": "Use custom exception classes",
                "example": "class ValidationError(Exception): pass",
            }
        ],
        "timeline": [],
        "known_issues": [],
        "current_state": {
            "status": "in_progress",
            "current_phase": "development",
        },
    }

    manager.save_project(project)

    # Reload and verify memory structure
    reloaded_manager = ProjectManager(storage_dir=str(storage_dir))
    reloaded = reloaded_manager.load_project("memory_persist")

    memory_data = reloaded.metadata.get("project_memory")
    assert memory_data is not None
    assert memory_data["project_name"] == "memory_persist"
    assert len(memory_data["architecture_decisions"]) == 1
    assert memory_data["architecture_decisions"][0]["decision"] == "Use FastAPI"
    assert memory_data["current_state"]["status"] == "in_progress"


def test_active_files_tracking_persists(project_paths):
    """Test that active_files tracking is saved and restored."""
    storage_dir, workspace_dir = project_paths
    manager = ProjectManager(storage_dir=str(storage_dir))
    project = manager.create_project("active_files_test", _workspace_root(workspace_dir, "active_files_test"))

    # Simulate tracking active files
    project.metadata["active_files"] = [
        "src/main.py",
        "src/api/routes.py",
        "tests/test_api.py",
    ]

    manager.save_project(project)

    reloaded_manager = ProjectManager(storage_dir=str(storage_dir))
    reloaded = reloaded_manager.load_project("active_files_test")

    assert reloaded.metadata.get("active_files") == [
        "src/main.py",
        "src/api/routes.py",
        "tests/test_api.py",
    ]


def test_metadata_updates_preserve_other_fields(project_paths):
    """Test that updating metadata preserves other existing metadata fields."""
    storage_dir, workspace_dir = project_paths
    manager = ProjectManager(storage_dir=str(storage_dir))
    project = manager.create_project("update_test", _workspace_root(workspace_dir, "update_test"))

    # Set initial metadata
    project.metadata["field_a"] = "value_a"
    project.metadata["field_b"] = "value_b"
    manager.save_project(project)

    # Load, update one field, and save
    manager2 = ProjectManager(storage_dir=str(storage_dir))
    project2 = manager2.load_project("update_test")
    project2.metadata["field_c"] = "value_c"  # Add new field
    manager2.save_project(project2)

    # Verify all fields are preserved
    manager3 = ProjectManager(storage_dir=str(storage_dir))
    project3 = manager3.load_project("update_test")

    assert project3.metadata["field_a"] == "value_a"
    assert project3.metadata["field_b"] == "value_b"
    assert project3.metadata["field_c"] == "value_c"


def test_large_conversation_history_persists(project_paths):
    """Test that large conversation histories are saved and loaded correctly."""
    storage_dir, workspace_dir = project_paths
    manager = ProjectManager(storage_dir=str(storage_dir))
    project = manager.create_project("large_history", _workspace_root(workspace_dir, "large_history"))

    # Create a large conversation history (100 turns)
    large_history = []
    for i in range(100):
        large_history.append({"role": "user", "content": f"Message {i}"})
        large_history.append({"role": "assistant", "content": f"Response {i}"})

    project.conversation_history = large_history
    manager.save_project(project)

    # Reload and verify
    reloaded_manager = ProjectManager(storage_dir=str(storage_dir))
    reloaded = reloaded_manager.load_project("large_history")

    assert len(reloaded.conversation_history) == 200
    assert reloaded.conversation_history[0]["content"] == "Message 0"
    assert reloaded.conversation_history[-1]["content"] == "Response 99"


def test_conversation_history_with_max_limit(project_paths):
    """Test that conversation history can be limited (e.g., 500 message limit)."""
    storage_dir, workspace_dir = project_paths
    manager = ProjectManager(storage_dir=str(storage_dir))
    project = manager.create_project("limit_test", _workspace_root(workspace_dir, "limit_test"))

    # Create a very large history (600 messages)
    huge_history = []
    for i in range(300):
        huge_history.append({"role": "user", "content": f"Message {i}"})
        huge_history.append({"role": "assistant", "content": f"Response {i}"})

    project.conversation_history = huge_history

    # If there's a 500 message limit, truncate before saving
    MAX_HISTORY_SIZE = 500
    if len(project.conversation_history) > MAX_HISTORY_SIZE:
        # Keep only the most recent messages
        project.conversation_history = project.conversation_history[-MAX_HISTORY_SIZE:]

    manager.save_project(project)

    # Reload and verify truncation
    reloaded_manager = ProjectManager(storage_dir=str(storage_dir))
    reloaded = reloaded_manager.load_project("limit_test")

    assert len(reloaded.conversation_history) == MAX_HISTORY_SIZE
    # Should have the most recent messages
    assert reloaded.conversation_history[0]["content"] == "Message 50"  # 600 - 500 = 100 messages dropped, so starts at 50


def test_empty_metadata_persists_as_empty_dict(project_paths):
    """Test that projects with no metadata have empty dict metadata."""
    storage_dir, workspace_dir = project_paths
    manager = ProjectManager(storage_dir=str(storage_dir))
    project = manager.create_project("empty_meta", _workspace_root(workspace_dir, "empty_meta"))

    # Don't add any metadata
    manager.save_project(project)

    reloaded_manager = ProjectManager(storage_dir=str(storage_dir))
    reloaded = reloaded_manager.load_project("empty_meta")

    assert isinstance(reloaded.metadata, dict)
    assert len(reloaded.metadata) == 0


def test_nested_metadata_structures_persist(project_paths):
    """Test that complex nested metadata structures persist correctly."""
    storage_dir, workspace_dir = project_paths
    manager = ProjectManager(storage_dir=str(storage_dir))
    project = manager.create_project("nested_meta", _workspace_root(workspace_dir, "nested_meta"))

    # Add deeply nested metadata
    project.metadata["project_config"] = {
        "build": {
            "tool": "webpack",
            "options": {
                "mode": "production",
                "optimization": {
                    "minimize": True,
                    "splitChunks": {
                        "chunks": "all",
                    },
                },
            },
        },
        "test": {
            "framework": "jest",
            "coverage": True,
        },
    }

    manager.save_project(project)

    reloaded_manager = ProjectManager(storage_dir=str(storage_dir))
    reloaded = reloaded_manager.load_project("nested_meta")

    config = reloaded.metadata["project_config"]
    assert config["build"]["tool"] == "webpack"
    assert config["build"]["options"]["mode"] == "production"
    assert config["build"]["options"]["optimization"]["minimize"] is True
    assert config["test"]["framework"] == "jest"


def test_metadata_with_various_types(project_paths):
    """Test that metadata can store various JSON-compatible types."""
    storage_dir, workspace_dir = project_paths
    manager = ProjectManager(storage_dir=str(storage_dir))
    project = manager.create_project("types_test", _workspace_root(workspace_dir, "types_test"))

    project.metadata["string_value"] = "hello"
    project.metadata["int_value"] = 42
    project.metadata["float_value"] = 3.14
    project.metadata["bool_value"] = True
    project.metadata["null_value"] = None
    project.metadata["list_value"] = [1, 2, 3]
    project.metadata["dict_value"] = {"key": "value"}

    manager.save_project(project)

    reloaded_manager = ProjectManager(storage_dir=str(storage_dir))
    reloaded = reloaded_manager.load_project("types_test")

    assert reloaded.metadata["string_value"] == "hello"
    assert reloaded.metadata["int_value"] == 42
    assert reloaded.metadata["float_value"] == 3.14
    assert reloaded.metadata["bool_value"] is True
    assert reloaded.metadata["null_value"] is None
    assert reloaded.metadata["list_value"] == [1, 2, 3]
    assert reloaded.metadata["dict_value"] == {"key": "value"}
