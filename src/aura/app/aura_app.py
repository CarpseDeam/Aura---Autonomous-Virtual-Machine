import sys
import os
import logging
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
from src.aura.app.event_bus import EventBus
from src.aura.services.logging_service import LoggingService
from src.aura.services.llm_service import LLMService
from src.ui.windows.main_window import MainWindow


class AuraApp:
    """
    The main application class for AURA.
    """

    def __init__(self):
        """Initializes the AuraApp."""
        LoggingService.setup_logging()

        logging.info("Initializing AuraApp...")
        self.app = QApplication(sys.argv)

        self._load_fonts()

        self.event_bus = EventBus()
        self.llm_service = LLMService(self.event_bus)
        self.main_window = MainWindow(self.event_bus)

        self._register_event_handlers()
        logging.info("AuraApp initialized successfully.")

    def _load_fonts(self):
        """Loads custom fonts from the assets directory."""
        font_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets", "JetBrainsMono-Regular.ttf")
        if os.path.exists(font_path):
            font_id = QFontDatabase.addApplicationFont(font_path)
            if font_id != -1:
                family = QFontDatabase.applicationFontFamilies(font_id)[0]
                logging.info(f"Successfully loaded font: '{family}' from {font_path}")
            else:
                logging.error(f"Failed to load font from {font_path}.")
        else:
            logging.warning(f"Font file not found at {font_path}. Using default.")

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