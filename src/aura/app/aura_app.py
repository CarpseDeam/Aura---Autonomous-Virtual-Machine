import sys
import logging
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
from src.aura.config import ASSETS_DIR, WORKSPACE_DIR
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
        self.validation_service = BlueprintValidator()
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
        )
        self.main_window = MainWindow(self.event_bus, self.image_storage_service)
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
