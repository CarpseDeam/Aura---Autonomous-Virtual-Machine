"""File system oriented action handlers."""

from __future__ import annotations

import logging
from typing import List

from src.aura.models.action import Action
from src.aura.models.project_context import ProjectContext
from src.aura.services.workspace_service import WorkspaceService


logger = logging.getLogger(__name__)


class FileOperations:
    """Handle read/list actions against the active project."""

    def __init__(self, workspace: WorkspaceService) -> None:
        self.workspace = workspace

    def execute_list_files(self, action: Action, ctx: ProjectContext) -> List[str]:
        """Return the list of files relative to the active project root."""
        project_path = getattr(self.workspace, "active_project_path", None)
        if not project_path:
            raise RuntimeError("No active project set")

        files: List[str] = []
        try:
            for path in project_path.rglob("*"):
                if path.is_file():
                    files.append(str(path.relative_to(project_path)))
        except Exception as exc:
            logger.error("Failed to list files for project %s: %s", project_path, exc, exc_info=True)
            raise RuntimeError("Failed to list files for active project") from exc
        return files

    def execute_read_file(self, action: Action, ctx: ProjectContext) -> str:
        """Read a file inside the active project."""
        file_path = action.get_param("file_path")
        if not file_path:
            raise ValueError("Missing 'file_path' parameter for read_file action")

        project_path = getattr(self.workspace, "active_project_path", None)
        if not project_path:
            raise RuntimeError("No active project set")

        project_root = project_path.resolve()
        target = (project_path / file_path).resolve()
        try:
            target.relative_to(project_root)
        except ValueError:
            raise RuntimeError("Attempted to read outside the active project") from None
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")

        try:
            return target.read_text(encoding="utf-8")
        except Exception as exc:
            logger.error("Failed to read file %s: %s", target, exc, exc_info=True)
            raise RuntimeError(f"Failed to read file: {file_path}") from exc
