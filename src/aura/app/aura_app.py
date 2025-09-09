import sys
import os
import logging
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
from src.aura.app.event_bus import EventBus
from src.aura.services.logging_service import LoggingService
from src.aura.services.llm_service import LLMService
from src.aura.services.build_service import BuildService
from src.aura.services.task_management_service import TaskManagementService
from src.aura.services.conversation_management_service import ConversationManagementService
from src.aura.services.ast_service import ASTService
from src.aura.services.context_retrieval_service import ContextRetrievalService
from src.aura.services.workspace_service import WorkspaceService
from src.aura.services.validation_service import ValidationService
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.services.orchestration_service import OrchestrationService
from src.aura.config import ASSETS_DIR, ROOT_DIR, WORKSPACE_DIR
from src.aura.models.events import Event
from src.ui.windows.main_window import MainWindow
from src.ui.windows.code_viewer_window import CodeViewerWindow


class AuraApp:
    """
    The main application class for AURA.
    """

    def __init__(self):
        """Initializes the AuraApp."""
        LoggingService.setup_logging()

        logging.info("Initializing AuraApp...")
        self.app = QApplication(sys.argv)
        self.app.setOrganizationName("Aura")
        self.app.setApplicationName("Autonomous Virtual Machine")

        self._load_fonts()

        self.event_bus = EventBus()
        self.prompt_manager = PromptManager()
        # CRITICAL: Instantiation order matters for dependencies
        # EventBus -> ASTService -> WorkspaceService -> Other services
        self.task_management_service = TaskManagementService(self.event_bus)
        self.conversation_management_service = ConversationManagementService(self.event_bus)
        self.ast_service = ASTService(self.event_bus)
        self.workspace_service = WorkspaceService(self.event_bus, WORKSPACE_DIR, self.ast_service)
        self.context_retrieval_service = ContextRetrievalService(self.ast_service)
        # Phoenix Initiative: Initialize ValidationService for Quality Gate
        self.validation_service = ValidationService(self.event_bus)
        # Low-level LLM dispatcher
        self.llm_service = LLMService(self.event_bus)
        # High-level services
        # New OrchestrationService becomes the primary entry point for user requests
        self.orchestration_service = OrchestrationService(
            event_bus=self.event_bus,
            llm_service=self.llm_service,
            ast_service=self.ast_service,
            prompt_manager=self.prompt_manager,
            task_management_service=self.task_management_service,
        )
        self.build_service = BuildService(
            self.event_bus,
            self.prompt_manager,
            self.llm_service,
            self.context_retrieval_service,
            self.task_management_service,
        )
        self.main_window = MainWindow(self.event_bus)
        self.code_viewer_window = CodeViewerWindow(self.event_bus, self.ast_service)

        # Give the main window reference to the code viewer for positioning
        self.main_window.code_viewer_window = self.code_viewer_window

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
        """Initialize the workspace by setting up the default project."""
        try:
            logging.info("Initializing workspace with default project...")
            # PRIME DIRECTIVE: Set active project triggers automatic AST indexing
            self.workspace_service.set_active_project("default_project")
            logging.info("Workspace initialization complete: default_project activated and indexed")
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
