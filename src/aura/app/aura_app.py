import argparse
import logging
import sys
from typing import List, Optional

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from src.aura.app.event_bus import EventBus
from src.aura.config import ASSETS_DIR, ROOT_DIR, WORKSPACE_DIR
from src.aura.project.project_manager import ProjectManager
from src.aura.services.conversation_management_service import ConversationManagementService
from src.aura.services.conversation_persistence_service import ConversationPersistenceService
from src.aura.services.image_storage_service import ImageStorageService
from src.aura.services.llm_service import LLMService
from src.aura.services.terminal_agent_service import TerminalAgentService
from src.aura.services.user_settings_manager import (
    get_terminal_agent_command_template,
    load_user_settings,
)
from src.aura.services.workspace_service import WorkspaceService
from src.aura.models.events import Event
from src.ui.controllers.conversation_sidebar_controller import ConversationSidebarController
from src.ui.windows.main_window import MainWindow


def get_project_from_args_or_prompt(argv: Optional[List[str]] = None) -> str:
    """
    Resolve the target project name from CLI args or interactive prompt.

    Args:
        argv: Optional list of CLI arguments to inspect.

    Returns:
        The selected project name, defaulting to 'default_project'.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--project")
    args, _ = parser.parse_known_args(argv)

    if getattr(args, "project", None):
        return args.project

    stdin = getattr(sys, "stdin", None)
    if stdin is not None and hasattr(stdin, "isatty") and stdin.isatty():
        try:
            user_input = input("Enter project name (default_project): ").strip()
            if user_input:
                return user_input
        except EOFError:
            pass

    return "default_project"


def get_project_root_path(project_name: str) -> str:
    """
    Determine the filesystem path for the given project within the workspace.

    Args:
        project_name: Name of the project.

    Returns:
        Absolute path to the project's root directory.
    """
    return str((WORKSPACE_DIR / project_name).expanduser().resolve())


class AuraApp:
    """
    The main application class for AURA.
    """

    def __init__(self):
        """Initializes the AuraApp."""
        # LoggingService removed during dead code cleanup; rely on default logging configuration

        logging.info("Initializing AuraApp...")
        project_name = get_project_from_args_or_prompt(sys.argv[1:])
        self.project_manager = ProjectManager(storage_dir="~/.aura/projects")
        try:
            if self.project_manager.project_exists(project_name):
                project = self.project_manager.load_project(project_name)
                logging.info(f"Loaded existing project: {project_name}")
            else:
                project_root = get_project_root_path(project_name)
                project = self.project_manager.create_project(project_name, project_root)
                logging.info(f"Created new project: {project_name}")
        except Exception as exc:
            logging.error(f"Failed to initialize project '{project_name}': {exc}")
            raise

        try:
            self.project_manager.save_project(project)
        except Exception as exc:
            logging.warning(f"Unable to refresh project metadata for '{project_name}': {exc}")
        self._active_project_name = project.name
        self._active_project_root = project.root_path

        self.app = QApplication(sys.argv)
        self.app.setOrganizationName("Aura")
        self.app.setApplicationName("Autonomous Virtual Machine")

        self._load_fonts()

        self.event_bus = EventBus()
        image_cache_dir = ROOT_DIR / "image_cache"
        try:
            self.image_storage_service = ImageStorageService(image_cache_dir, retention_limit=200)
        except Exception:
            logging.warning("Image cache unavailable; continuing without persistent image storage.")
            self.image_storage_service = None

        self.conversation_persistence_service = ConversationPersistenceService()
        self.conversation_management_service = ConversationManagementService(
            self.event_bus,
            self.conversation_persistence_service,
        )
        self.workspace_service = WorkspaceService(self.event_bus, WORKSPACE_DIR)

        # Low-level LLM dispatcher
        self.llm_service = LLMService(self.event_bus, self.image_storage_service)

        # Load terminal agent configuration from user settings
        user_settings = load_user_settings()
        agent_command_template = get_terminal_agent_command_template(user_settings)

        self.terminal_agent_service = TerminalAgentService(
            workspace_root=WORKSPACE_DIR,
            llm_service=self.llm_service,
            default_command=None,  # Will use template-based command building
            agent_command_template=agent_command_template,
        )
        # Ensure the requested project is active
        try:
            self.workspace_service.set_active_project(project_name)
        except Exception as exc:
            logging.warning("Failed to activate workspace project '%s': %s", project_name, exc)

        self.main_window = MainWindow(
            self.event_bus,
            self.image_storage_service,
            llm_service=self.llm_service,
            terminal_service=self.terminal_agent_service,
            workspace_service=self.workspace_service,
            conversations=self.conversation_management_service,
        )

        # Initialize conversation sidebar controller to manage thread navigation
        self.conversation_sidebar_controller = ConversationSidebarController(
            sidebar=self.main_window.conversation_sidebar,
            conversations=self.conversation_management_service,
            event_bus=self.event_bus,
        )

        self._register_event_handlers()
        self._initialize_workspace()

        logging.info("AuraApp initialized successfully.")

    def _load_fonts(self):
        """Loads custom fonts from the assets directory."""
        font_path = ASSETS_DIR / "JetBrainsMono-Regular.ttf"
        if font_path.exists():
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            if font_id != -1:
                family = QFontDatabase.applicationFontFamilies(font_id)[0]
                logging.info(f"Successfully loaded font: '{family}'")
            else:
                logging.error(f"Failed to load font from {font_path}.")
        else:
            logging.warning(f"Font file not found at {font_path}. Using default.")

    def _initialize_workspace(self):
        """Initialize the workspace by setting up the active project."""
        project_name = getattr(self, "_active_project_name", "default_project")
        try:
            logging.info(f"Initializing workspace with project '{project_name}'...")
            project_files = self.workspace_service.get_project_files()
            logging.info("Workspace file index refreshed (%d files).", len(project_files))
            if hasattr(self.project_manager, "current_project") and self.project_manager.current_project:
                current = self.project_manager.current_project
                current.root_path = getattr(self, "_active_project_root", current.root_path)
                current.active_files = project_files
                self.project_manager.save_project(current)
        except Exception as e:
            logging.error(f"Failed to initialize workspace: {e}")

    def _register_event_handlers(self):
        """Register all event handlers for the application."""
        self.event_bus.subscribe("APP_START", self.on_app_start)
        self.event_bus.subscribe("SEND_USER_MESSAGE", self._handle_user_command)

    def _handle_user_command(self, event: Event) -> None:
        """Handle project-related slash commands dispatched via the event bus."""
        payload = event.payload or {}
        text = (payload.get("text") or "").strip()
        if not text.startswith("/project"):
            return

        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            logging.warning("Ignoring malformed project command: %s", text)
            return

        action = parts[1].lower()
        project_name = parts[2].strip()
        if not project_name:
            logging.warning("Project command missing name: %s", text)
            return

        if action == "create":
            self._handle_project_create(project_name)
        elif action == "switch":
            self._handle_project_switch(project_name)
        else:
            logging.debug("Unhandled project subcommand '%s' in '%s'", action, text)

    def _handle_project_create(self, project_name: str) -> None:
        root_path = get_project_root_path(project_name)
        if self.project_manager.project_exists(project_name):
            logging.info("Project '%s' already exists; activating workspace.", project_name)
        else:
            try:
                self.project_manager.create_project(project_name, root_path)
            except Exception as exc:
                logging.error("Failed to create project '%s': %s", project_name, exc)
                return
        try:
            self.workspace_service.set_active_project(project_name)
        except Exception as exc:
            logging.error("Failed to activate project '%s': %s", project_name, exc)

    def _handle_project_switch(self, project_name: str) -> None:
        if not self.project_manager.project_exists(project_name):
            logging.warning("Cannot switch: project '%s' does not exist", project_name)
            return
        try:
            self.project_manager.switch_project(project_name)
            self.workspace_service.set_active_project(project_name)
        except Exception as exc:
            logging.error("Failed to switch project '%s': %s", project_name, exc)

    def on_app_start(self, event):
        """Example event handler for application start."""
        logging.info(f"AuraApp caught event: {event.event_type}")

    def run(self):
        """Shows the main window and starts the application."""
        logging.info("Starting Aura application...")
        self.main_window.show()
        sys.exit(self.app.exec())
