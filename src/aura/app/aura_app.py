import sys
import logging
from PySide6.QtWidgets import QApplication
from src.aura.app.event_bus import EventBus
from src.aura.services.llm_service import LLMService
from src.aura.services.logging_service import LoggingService
from src.ui.windows.main_window import MainWindow


class AuraApp:
    """
    The main application class for AURA.

    This class initializes the QApplication, creates the main window,
    and starts the application's event loop.
    """

    def __init__(self):
        """Initializes the AuraApp."""
        # --- CRITICAL: Setup logging first ---
        LoggingService.setup_logging()
        # ------------------------------------

        logging.info("Initializing AuraApp...")
        self.app = QApplication(sys.argv)
        self.event_bus = EventBus()

        # Initialize services
        self.llm_service = LLMService(self.event_bus)

        # Initialize UI
        self.main_window = MainWindow(self.event_bus)

        self._register_event_handlers()
        logging.info("AuraApp initialized successfully.")

    def _register_event_handlers(self):
        """Register all event handlers for the application."""
        self.event_bus.subscribe("APP_START", self.on_app_start)
        # We can add a handler here to test the full loop
        self.event_bus.subscribe("MODEL_RESPONSE_RECEIVED", self.on_model_response)

    def on_app_start(self, event):
        """Example event handler for application start."""
        logging.info(f"AuraApp caught event: {event.event_type}")

    def on_model_response(self, event):
        """Handler to log model responses for now."""
        response_text = event.payload.get("text", "")
        logging.info(f"AURA RESPONSE (from event bus): {response_text[:100]}...")

    def run(self):
        """Shows the main window and starts the application."""
        logging.info("Starting Aura application...")
        self.main_window.show()
        sys.exit(self.app.exec())