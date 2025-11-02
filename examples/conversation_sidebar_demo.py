"""
Demo script for ConversationSidebarWidget.

Shows how to use the ConversationSidebarWidget with the ConversationSidebarController
to create a functional conversation thread management sidebar.

Run this script to see the sidebar in action with sample data.
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from PySide6.QtWidgets import QApplication, QMainWindow, QHBoxLayout, QWidget, QTextEdit
from PySide6.QtCore import Qt

from src.aura.app.event_bus import EventBus
from src.aura.services.conversation_management_service import ConversationManagementService
from src.aura.services.conversation_persistence_service import ConversationPersistenceService
from src.ui.widgets.conversation_sidebar_widget import ConversationSidebarWidget
from src.ui.controllers.conversation_sidebar_controller import ConversationSidebarController


class DemoMainWindow(QMainWindow):
    """Demo main window showing the sidebar integration."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Aura Conversation Sidebar Demo")
        self.setGeometry(100, 100, 1200, 800)

        # Initialize services
        self.event_bus = EventBus()
        self.persistence = ConversationPersistenceService(":memory:")  # In-memory DB for demo
        self.conversations = ConversationManagementService(
            event_bus=self.event_bus,
            persistence=self.persistence,
        )

        # Create sample data
        self._create_sample_conversations()

        # Setup UI
        self._setup_ui()

    def _setup_ui(self):
        """Setup the main window UI."""
        # Central widget with horizontal layout
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Create sidebar
        self.sidebar = ConversationSidebarWidget()

        # Create controller to connect sidebar to services
        self.controller = ConversationSidebarController(
            sidebar=self.sidebar,
            conversations=self.conversations,
            event_bus=self.event_bus,
        )

        # Main content area (placeholder)
        self.content = QTextEdit()
        self.content.setReadOnly(True)
        self.content.setPlaceholderText("Select a conversation thread from the sidebar...")
        self.content.setStyleSheet("""
            QTextEdit {
                background-color: #0a0a0a;
                color: #FFB74D;
                font-family: 'JetBrains Mono', monospace;
                font-size: 13px;
                border: none;
                padding: 20px;
            }
        """)

        # Connect sidebar signals to update content
        self.sidebar.thread_selected.connect(self._on_thread_selected)

        # Add widgets to layout
        layout.addWidget(self.sidebar)
        layout.addWidget(self.content, stretch=1)

        self.setCentralWidget(central)

        # Apply dark theme to window
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0a0a0a;
            }
        """)

    def _create_sample_conversations(self):
        """Create sample conversations for demo purposes."""
        now = datetime.now(timezone.utc)

        # Standalone chats
        chats = [
            ("Quick Python question", 5),  # 5 minutes ago
            ("Debugging async code", 120),  # 2 hours ago
            ("API design review", 1440),  # 1 day ago
            ("Database optimization tips", 2880),  # 2 days ago
        ]

        for title, minutes_ago in chats:
            conv = self.persistence.create_conversation("default_project", active=False)
            self.persistence.update_conversation_title(conv["id"], title)

            # Add a sample message
            created_at = (now - timedelta(minutes=minutes_ago)).isoformat()
            self.persistence.save_message(
                conv["id"],
                "user",
                f"This is a sample message for: {title}",
                metadata={"created_at": created_at},
            )

        # Project-based conversations
        projects = {
            "aura-core": [
                ("Implement new feature", 30),
                ("Fix memory leak", 180),
                ("Refactor services", 360),
            ],
            "web-scraper": [
                ("Add proxy support", 60),
                ("Handle rate limiting", 240),
            ],
            "ml-pipeline": [
                ("Optimize training loop", 90),
                ("Add model versioning", 480),
            ],
        }

        for project_name, threads in projects.items():
            for title, minutes_ago in threads:
                conv = self.persistence.create_conversation(project_name, active=False)
                self.persistence.update_conversation_title(conv["id"], title)

                # Add a sample message
                created_at = (now - timedelta(minutes=minutes_ago)).isoformat()
                self.persistence.save_message(
                    conv["id"],
                    "user",
                    f"Project: {project_name}\nThread: {title}",
                    metadata={"created_at": created_at},
                )

    def _on_thread_selected(self, thread_id: str):
        """Handle thread selection - display conversation content."""
        try:
            # Load conversation
            conversation = self.persistence.get_conversation(thread_id)
            if not conversation:
                self.content.setText("Conversation not found.")
                return

            # Load messages
            messages = self.persistence.load_messages(thread_id)

            # Format for display
            title = conversation.get("title", "Untitled")
            project = conversation.get("project_name", "default_project")
            created = conversation.get("created_at", "Unknown")

            content_lines = [
                f"{'=' * 60}",
                f"Conversation: {title}",
                f"Project: {project}",
                f"Created: {created}",
                f"Messages: {len(messages)}",
                f"{'=' * 60}",
                "",
            ]

            for msg in messages:
                role = msg.get("role", "unknown").upper()
                content = msg.get("content", "")
                timestamp = msg.get("created_at", "")

                content_lines.append(f"[{role}] {timestamp}")
                content_lines.append(content)
                content_lines.append("")

            self.content.setText("\n".join(content_lines))

        except Exception as exc:
            self.content.setText(f"Error loading conversation: {exc}")


def main():
    """Run the demo application."""
    app = QApplication(sys.argv)

    # Set application-wide font
    from PySide6.QtGui import QFont
    app.setFont(QFont("JetBrains Mono", 11))

    window = DemoMainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
