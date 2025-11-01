from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event


logger = logging.getLogger(__name__)


class WorkspaceService:
    """
    Minimal workspace manager responsible for project activation and filesystem access.
    """

    def __init__(self, event_bus: EventBus, workspace_root: Path) -> None:
        self.event_bus = event_bus
        self.workspace_root = Path(workspace_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)

        self.active_project: Optional[str] = None
        self.active_project_path: Optional[Path] = None

        logger.info("WorkspaceService initialized at %s", self.workspace_root)

    # ------------------------------------------------------------------ Project management

    def set_active_project(self, project_name: str) -> None:
        if not project_name:
            raise ValueError("project_name must be provided")

        project_path = self.workspace_root / project_name
        project_path.mkdir(parents=True, exist_ok=True)

        self.active_project = project_name
        self.active_project_path = project_path

        logger.info("Activated project '%s' at %s", project_name, project_path)
        self._dispatch_event(
            "PROJECT_ACTIVATED",
            {"project_name": project_name, "project_path": str(project_path)},
        )

    def list_workspace_projects(self) -> List[Dict[str, str]]:
        projects: List[Dict[str, str]] = []
        for path in self.workspace_root.iterdir():
            if path.is_dir():
                projects.append({"name": path.name, "path": str(path)})
        return projects

    def get_project_files(self) -> List[str]:
        if not self.active_project_path:
            return []
        files: List[str] = []
        for path in self.active_project_path.rglob("*"):
            if path.is_file():
                files.append(str(path.relative_to(self.active_project_path)))
        return files

    # ------------------------------------------------------------------ Event helpers

    def _dispatch_event(self, event_type: str, payload: Dict[str, str]) -> None:
        try:
            self.event_bus.dispatch(Event(event_type=event_type, payload=payload))
        except Exception:
            logger.debug("Failed to dispatch %s event", event_type, exc_info=True)
