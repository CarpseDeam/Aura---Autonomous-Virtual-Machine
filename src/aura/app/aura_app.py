import argparse
import sys
import logging
from typing import List, Optional
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtGui import QFontDatabase
from src.aura.app.event_bus import EventBus
from src.aura.services.logging_service import LoggingService
from src.aura.services.llm_service import LLMService
from src.aura.services.conversation_management_service import ConversationManagementService
from src.aura.services.conversation_persistence_service import ConversationPersistenceService
from src.aura.services.workspace_service import WorkspaceService
from src.aura.services.image_storage_service import ImageStorageService
from src.aura.services.file_registry import FileRegistry
from src.aura.services.terminal_agent_service import TerminalAgentService
from src.aura.services.workspace_monitor import WorkspaceChangeMonitor
from src.aura.services.terminal_session_manager import TerminalSessionManager
from src.aura.services.memory_manager import MemoryManager
from src.aura.services.token_tracker import TokenTracker
from src.aura.services.user_settings_manager import (
    get_terminal_agent_command_template,
    load_user_settings,
)
from src.aura.models.events import Event
from src.aura.models.event_types import TRIGGER_AUTO_INTEGRATE
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.brain import AuraBrain
from src.aura.executor import AuraExecutor
from src.aura.interface import AuraInterface
from src.aura.project.project_manager import ProjectManager
from src.aura.context.context_manager import ContextManager
from src.aura.models.context_models import ContextConfig
from src.aura.agent.iteration_controller import IterationController
from src.aura.models.iteration_models import IterationConfig
from src.aura.config import ASSETS_DIR, WORKSPACE_DIR
from src.ui.windows.main_window import MainWindow
from src.ui.controllers.conversation_sidebar_controller import ConversationSidebarController


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
        LoggingService.setup_logging()

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
        self.token_tracker = TokenTracker(self.event_bus, token_limit=200_000)
        self.prompt_manager = PromptManager()
        self.image_storage_service = ImageStorageService()
        # Instantiate core services
        self.conversation_persistence_service = ConversationPersistenceService()
        self.conversation_management_service = ConversationManagementService(
            self.event_bus,
            self.conversation_persistence_service,
        )
        self.workspace_service = WorkspaceService(self.event_bus, WORKSPACE_DIR)
        self.file_registry = FileRegistry(WORKSPACE_DIR)

        # Load terminal agent configuration from user settings
        user_settings = load_user_settings()
        agent_command_template = get_terminal_agent_command_template(user_settings)

        self.terminal_agent_service = TerminalAgentService(
            workspace_root=WORKSPACE_DIR,
            default_command=None,  # Will use template-based command building
            agent_command_template=agent_command_template,
        )
        self.workspace_monitor = WorkspaceChangeMonitor(WORKSPACE_DIR)
        self.terminal_session_manager = TerminalSessionManager(
            workspace_root=WORKSPACE_DIR,
            workspace_monitor=self.workspace_monitor,
            event_bus=self.event_bus,
            stabilization_seconds=90,
            timeout_seconds=600,
        )

        # Ensure the requested project is active
        try:
            self.workspace_service.set_active_project(project_name)
        except Exception as exc:
            logging.warning("Failed to activate workspace project '%s': %s", project_name, exc)

        # Low-level LLM dispatcher
        self.llm_service = LLMService(self.event_bus, self.image_storage_service)
        # New 3-layer architecture
        self.brain = AuraBrain(self.llm_service, self.prompt_manager)
        self.executor = AuraExecutor(
            event_bus=self.event_bus,
            llm=self.llm_service,
            prompts=self.prompt_manager,
            workspace=self.workspace_service,
            file_registry=self.file_registry,
            terminal_service=self.terminal_agent_service,
            workspace_monitor=self.workspace_monitor,
            terminal_session_manager=self.terminal_session_manager,
        )

        # Initialize Memory Manager for project memory persistence (before ContextManager)
        self.memory_manager = None
        try:
            self.memory_manager = MemoryManager(
                project_manager=self.project_manager,
                event_bus=self.event_bus
            )
            logging.info("MemoryManager initialized successfully")
        except Exception as e:
            logging.warning(f"Failed to initialize MemoryManager: {e}. Continuing without memory management.")

        # Initialize Context Manager for intelligent context loading
        self.context_manager = None
        try:
            context_config = ContextConfig(
                max_tokens=8000,
                min_relevance_threshold=0.3,
                max_files=20
            )
            self.context_manager = ContextManager(
                project_root=str(WORKSPACE_DIR),
                config=context_config,
                event_bus=self.event_bus,
                memory_manager=self.memory_manager
            )
            logging.info("ContextManager initialized successfully")
        except Exception as e:
            logging.warning(f"Failed to initialize ContextManager: {e}. Continuing with fallback behavior.")

        # Initialize Iteration Controller for intelligent iteration control
        self.iteration_controller = None
        try:
            iteration_config = IterationConfig(
                bootstrap_max_iterations=15,
                iterate_max_iterations=8,
                loop_detection_threshold=3,
                use_llm_reflection=True
            )
            self.iteration_controller = IterationController(
                config=iteration_config,
                llm_service=self.llm_service,
                event_bus=self.event_bus
            )
            logging.info("IterationController initialized successfully")
        except Exception as e:
            logging.warning(f"Failed to initialize IterationController: {e}. Continuing with fallback behavior.")

        # Thread pool for background tasks
        thread_pool = QThreadPool.globalInstance()

        self.interface = AuraInterface(
            event_bus=self.event_bus,
            brain=self.brain,
            executor=self.executor,
            conversations=self.conversation_management_service,
            workspace=self.workspace_service,
            thread_pool=thread_pool,
            project_manager=self.project_manager,
            context_manager=self.context_manager,
            iteration_controller=self.iteration_controller,
        )
        self.main_window = MainWindow(
            self.event_bus,
            self.image_storage_service,
            self.terminal_session_manager,
        )

        # Initialize conversation sidebar controller to manage thread navigation
        self.conversation_sidebar_controller = ConversationSidebarController(
            sidebar=self.main_window.conversation_sidebar,
            conversations=self.conversation_management_service,
            event_bus=self.event_bus,
        )

        self._register_event_handlers()
        self._initialize_workspace()

        # Setup periodic session monitoring
        self._setup_session_monitor()

        logging.info("AuraApp initialized successfully.")

    def _determine_default_agent_command(self) -> List[str]:
        if sys.platform.startswith("win"):
            return ["pwsh.exe", "-NoExit"]
        return ["bash", "-i"]

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
            self.file_registry.refresh()
            logging.info("Workspace file index refreshed.")
            if hasattr(self.project_manager, "current_project") and self.project_manager.current_project:
                current = self.project_manager.current_project
                current.root_path = getattr(self, "_active_project_root", current.root_path)
                current.active_files = self.file_registry.list_files()
                self.project_manager.save_project(current)
        except Exception as e:
            logging.error(f"Failed to initialize workspace: {e}")

    def _register_event_handlers(self):
        """Register all event handlers for the application."""
        self.event_bus.subscribe("APP_START", self.on_app_start)

    def on_app_start(self, event):
        """Example event handler for application start."""
        logging.info(f"AuraApp caught event: {event.event_type}")

    def _setup_session_monitor(self):
        """Setup periodic monitoring of terminal sessions."""
        self.session_check_timer = QTimer()
        self.session_check_timer.timeout.connect(self._check_terminal_sessions)
        self.session_check_timer.start(5000)  # Check every 5 seconds
        logging.info("Terminal session monitoring started (checking every 5s)")

    def _check_terminal_sessions(self):
        """Periodically check terminal sessions for completion."""
        try:
            completed_sessions = self.terminal_session_manager.check_all_sessions()

            # Auto-integrate completed sessions if enabled
            for session_status in completed_sessions:
                if session_status.status == "completed":
                    logging.info(
                        "Session %s completed, dispatching auto-integrate event",
                        session_status.session.task_id
                    )
                    self.event_bus.dispatch(
                        Event(
                            event_type=TRIGGER_AUTO_INTEGRATE,
                            payload={"task_id": session_status.session.task_id}
                        )
                    )
        except Exception as exc:
            logging.error("Error checking terminal sessions: %s", exc)

    def cleanup(self):
        """Cleanup resources before application shutdown."""
        logging.info("Cleaning up Aura application...")
        # Terminate all active terminal sessions
        count = self.terminal_session_manager.cleanup_all_sessions()
        logging.info(f"Cleaned up {count} terminal sessions")

    def run(self):
        """Shows the main window and starts the application."""
        logging.info("Starting Aura application...")
        # Register cleanup to run on application exit
        self.app.aboutToQuit.connect(self.cleanup)
        self.main_window.show()
        sys.exit(self.app.exec())
