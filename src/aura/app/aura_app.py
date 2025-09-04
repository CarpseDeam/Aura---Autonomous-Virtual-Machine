import sys
from PySide6.QtWidgets import QApplication

from src.aura.app.event_bus import EventBus
from src.ui.windows.main_window import MainWindow


class AuraApp:
    """
    The main application class for AURA.

    This class initializes the QApplication, creates the main window,
    and starts the application's event loop.
    """

    def __init__(self):
        """Initializes the AuraApp."""
        self.app = QApplication(sys.argv)
        self.event_bus = EventBus()
        self.main_window = MainWindow(self.event_bus)

        self._register_event_handlers()

    def _register_event_handlers(self):
        """Register all event handlers for the application."""
        # This is where we will connect services to the event bus in the future.
        # For now, we can add a simple test handler.
        self.event_bus.subscribe("APP_START", self.on_app_start)

    def on_app_start(self, event):
        """Example event handler for application start."""
        print(f"AuraApp caught event: {event.event_type}")

    def run(self):
        """Shows the main window and starts the application."""
        self.main_window.show()
        sys.exit(self.app.exec())