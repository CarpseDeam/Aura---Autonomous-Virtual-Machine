from __future__ import annotations

from pathlib import Path

import pytest

from src.aura.project.project_manager import ProjectManager


def test_create_project_raises_when_duplicate(tmp_path: Path) -> None:
    storage_dir = tmp_path / "projects"
    workspace_root = tmp_path / "workspace" / "demo"
    workspace_root.mkdir(parents=True, exist_ok=True)

    manager = ProjectManager(storage_dir=str(storage_dir))
    manager.create_project("demo", str(workspace_root))

    with pytest.raises(ValueError) as exc:
        manager.create_project("demo", str(workspace_root))

    assert "already exists" in str(exc.value)
