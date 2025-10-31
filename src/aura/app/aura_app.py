import argparse
import sys
import logging
from typing import List, Optional
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QFontDatabase
from src.aura.app.event_bus import EventBus
from src.aura.services.logging_service import LoggingService
from src.aura.services.llm_service import LLMService
from src.aura.services.conversation_management_service import ConversationManagementService
from src.aura.services.conversation_persistence_service import ConversationPersistenceService
from src.aura.services.ast_service import ASTService
from src.aura.services.context_retrieval_service import ContextRetrievalService
from src.aura.services.workspace_service import WorkspaceService
from src.aura.services.blueprint_validator import BlueprintValidator
from src.aura.services.image_storage_service import ImageStorageService
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
        self.prompt_manager = PromptManager()
        self.image_storage_service = ImageStorageService()
        # CRITICAL: Instantiation order matters for dependencies
        # EventBus -> ASTService -> WorkspaceService -> Other services
        self.conversation_persistence_service = ConversationPersistenceService()
        self.conversation_management_service = ConversationManagementService(
            self.event_bus,
            self.conversation_persistence_service,
        )
        self.ast_service = ASTService(self.event_bus)
        self.workspace_service = WorkspaceService(self.event_bus, WORKSPACE_DIR, self.ast_service)
        self.context_retrieval_service = ContextRetrievalService(self.ast_service)
        # Phoenix Initiative: Initialize BlueprintValidator for Quality Gate
        self.validation_service = BlueprintValidator(self.event_bus)
        # Low-level LLM dispatcher
        self.llm_service = LLMService(self.event_bus, self.image_storage_service)
        # New 3-layer architecture
        self.brain = AuraBrain(self.llm_service, self.prompt_manager)
        self.executor = AuraExecutor(
            event_bus=self.event_bus,
            llm=self.llm_service,
            prompts=self.prompt_manager,
            ast=self.ast_service,
            context=self.context_retrieval_service,
            workspace=self.workspace_service,
        )

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
                event_bus=self.event_bus
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
            ast=self.ast_service,
            conversations=self.conversation_management_service,
            workspace=self.workspace_service,
            thread_pool=thread_pool,
            project_manager=self.project_manager,
            context_manager=self.context_manager,
            iteration_controller=self.iteration_controller,
        )
        self.main_window = MainWindow(self.event_bus, self.image_storage_service)

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
            # PRIME DIRECTIVE: Set active project triggers automatic AST indexing
            self.workspace_service.set_active_project(project_name)
            logging.info(f"Workspace initialization complete: {project_name} activated and indexed")

            try:
                if hasattr(self, "project_manager") and self.project_manager.current_project:
                    current = self.project_manager.current_project
                    current.root_path = getattr(self, "_active_project_root", current.root_path)
                    project_index = getattr(self.ast_service, "project_index", {}) or {}
                    if isinstance(project_index, dict):
                        current.active_files = list(project_index.keys())
                    self.project_manager.save_project(current)
            except Exception as exc:
                logging.warning(f"Failed to persist project state after workspace setup: {exc}")
        except Exception as e:
            logging.error(f"Failed to initialize workspace: {e}")

    def _register_event_handlers(self):
        """Register all event handlers for the application."""
        self.event_bus.subscribe("APP_START", self.on_app_start)

    def on_app_start(self, event):
        """Example event handler for application start."""
        logging.info(f"AuraApp caught event: {event.event_type}")

    def run(self):
        """Shows the main window and starts the application."""
        logging.info("Starting Aura application...")
        self.main_window.show()
        sys.exit(self.app.exec())
