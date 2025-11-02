from __future__ import annotations

import logging
from typing import List

from PySide6.QtWidgets import QFileDialog, QInputDialog, QWidget

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.project.project_manager import ProjectManager
from src.ui.widgets.chat_display_widget import ChatDisplayWidget
from src.ui.widgets.project_switch_dialog import ProjectSwitchDialog

logger = logging.getLogger(__name__)


class ProjectActions:
    """
    Handles project-related UI flows triggered from the main window toolbar.
    """

    def __init__(self, event_bus: EventBus, chat_display: ChatDisplayWidget, parent: QWidget):
        self._event_bus = event_bus
        self._chat_display = chat_display
        self._parent = parent

    def import_project(self) -> None:
        """
        Launch a directory picker and dispatch an import request if selected.
        """
        dialog = QFileDialog(self._parent)
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setWindowTitle("Import Project - Select Directory")
        if not dialog.exec():
            return

        selected_dirs = dialog.selectedFiles()
        if not selected_dirs:
            return

        project_path = selected_dirs[0]
        logger.info("User selected project for import: %s", project_path)
        self._event_bus.dispatch(
            Event(event_type="IMPORT_PROJECT_REQUESTED", payload={"path": project_path})
        )
        self._chat_display.display_system_message("WORKSPACE", f"Importing project from: {project_path}")

    def create_new_project(self) -> None:
        """
        Prompt for a project name and dispatch the create/switch command.
        """
        project_name, ok = QInputDialog.getText(self._parent, "New Project", "Enter project name:", text="")
        if not ok or not project_name:
            return

        normalized = project_name.strip()
        if not normalized:
            self._chat_display.display_system_message("ERROR", "Project name cannot be empty.")
            return

        logger.info("User requested new project: %s", normalized)
        self._event_bus.dispatch(
            Event(event_type="SEND_USER_MESSAGE", payload={"text": f"/project create {normalized}"})
        )

    def open_project_switcher(self) -> None:
        """
        Display the project switch dialog and dispatch a switch command if chosen.
        """
        try:
            manager = ProjectManager()
            project_summaries = manager.list_projects()
            project_names: List[str] = [summary.name for summary in project_summaries]
        except Exception as exc:  # noqa: BLE001 - user-visible error surface
            logger.error("Failed to load project list: %s", exc)
            self._chat_display.display_system_message("ERROR", "Unable to load project list.")
            return

        if not project_names:
            self._chat_display.display_system_message("SYSTEM", "No projects available to switch.")
            return

        dialog = ProjectSwitchDialog(self._parent, project_names)
        if dialog.exec() != dialog.Accepted:
            return

        selected = (dialog.selected_project or "").strip()
        if not selected:
            return

        logger.info("User requested switch to project: %s", selected)
        self._chat_display.display_system_message("WORKSPACE", f"Switching to project: {selected}")
        self._event_bus.dispatch(
            Event(event_type="SEND_USER_MESSAGE", payload={"text": f"/project switch {selected}"})
        )
