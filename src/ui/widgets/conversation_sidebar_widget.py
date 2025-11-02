"""
ConversationSidebarWidget - Retro terminal-themed conversation thread manager.

Provides a collapsible sidebar for managing conversation threads organized by:
- CHATS: Standalone conversations
- PROJECTS: Workspace-based conversation threads
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon, QAction, QFont
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QMenu,
    QStyle,
    QSizePolicy,
)

logger = logging.getLogger(__name__)


class ConversationSidebarWidget(QWidget):
    """
    Sidebar widget for managing conversation threads with retro terminal aesthetic.

    Features:
    - Tree structure with CHATS and PROJECTS sections
    - Double-click to switch threads
    - Context menu for thread management (Archive/Delete/Rename)
    - New Chat and New Thread buttons
    - Compact 200-250px width design
    """

    # Signals
    thread_selected = Signal(str)  # conversation_id
    new_chat_requested = Signal()
    new_thread_requested = Signal()
    upgrade_to_project_requested = Signal(str)  # conversation_id
    thread_renamed = Signal(str, str)  # conversation_id, new_title
    thread_archived = Signal(str)  # conversation_id
    thread_deleted = Signal(str)  # conversation_id

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._is_collapsed = False
        self._current_thread_id: Optional[str] = None
        self._project_active = False
        self._empty_chats_item: Optional[QTreeWidgetItem] = None
        self._empty_projects_item: Optional[QTreeWidgetItem] = None
        self._setup_ui()
        self._apply_retro_style()
        self._update_empty_states()

    def _setup_ui(self) -> None:
        """Initialize the sidebar UI components."""
        # Main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Header with collapse button
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(8, 8, 8, 8)
        header_layout.setSpacing(4)

        self._collapse_btn = self._create_icon_button(
            tooltip="Collapse Sidebar",
            icon_type=QStyle.StandardPixmap.SP_ArrowLeft,
            size=(24, 24),
        )
        self._collapse_btn.clicked.connect(self._toggle_collapse)

        header_layout.addWidget(self._collapse_btn)
        header_layout.addStretch()

        layout.addLayout(header_layout)

        # Action buttons
        button_layout = QVBoxLayout()
        button_layout.setSpacing(4)
        button_layout.setContentsMargins(8, 0, 8, 8)

        self._new_chat_btn = self._create_action_button("💬 New Chat")
        self._new_chat_btn.clicked.connect(self.new_chat_requested.emit)

        self._new_thread_btn = self._create_action_button("📝 New Thread")
        self._new_thread_btn.clicked.connect(self.new_thread_requested.emit)
        self._new_thread_btn.setVisible(False)  # Only visible when project active

        button_layout.addWidget(self._new_chat_btn)
        button_layout.addWidget(self._new_thread_btn)

        layout.addLayout(button_layout)

        # Tree widget for conversations
        self._tree = QTreeWidget(self)
        self._tree.setObjectName("conversation_tree")
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setIndentation(16)
        self._tree.setAnimated(True)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)

        # Create top-level sections
        self._chats_section = QTreeWidgetItem(self._tree, ["💬 CHATS"])
        self._chats_section.setExpanded(True)
        self._chats_section.setFlags(Qt.ItemFlag.ItemIsEnabled)  # Not selectable

        self._projects_section = QTreeWidgetItem(self._tree, ["📁 PROJECTS"])
        self._projects_section.setExpanded(True)
        self._projects_section.setFlags(Qt.ItemFlag.ItemIsEnabled)

        layout.addWidget(self._tree)

        # Set fixed width for sidebar
        self.setFixedWidth(250)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def _ensure_placeholder(self, section: QTreeWidgetItem, text: str):
        """Ensure a dim, non-selectable placeholder exists under the given section."""
        from PySide6.QtGui import QBrush, QColor
        if section.childCount() > 0:
            return None
        item = QTreeWidgetItem(section, [text])
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.setForeground(0, QBrush(QColor("#777777")))
        font = QFont("JetBrains Mono", 9)
        item.setFont(0, font)
        return item

    def _remove_placeholder_if_present(self, placeholder: Optional[QTreeWidgetItem]) -> None:
        if placeholder and placeholder.parent():
            try:
                placeholder.parent().removeChild(placeholder)
            except Exception:
                pass

    def _update_empty_states(self) -> None:
        """Update empty-state placeholder items for CHATS and PROJECTS."""
        if hasattr(self, "_chats_section") and hasattr(self, "_projects_section"):
            # Clear placeholders if real children exist
            if self._chats_section.childCount() > 0 and self._empty_chats_item:
                self._remove_placeholder_if_present(self._empty_chats_item)
                self._empty_chats_item = None
            if self._projects_section.childCount() > 0 and self._empty_projects_item:
                self._remove_placeholder_if_present(self._empty_projects_item)
                self._empty_projects_item = None

            # Create placeholders if sections are empty
            if self._chats_section.childCount() == 0:
                self._empty_chats_item = self._ensure_placeholder(
                    self._chats_section,
                    "No conversations yet - click New Chat to start",
                )
            if self._projects_section.childCount() == 0:
                self._empty_projects_item = self._ensure_placeholder(
                    self._projects_section,
                    "No project threads - activate a project to begin",
                )

    def _create_action_button(self, text: str) -> QPushButton:
        """Create a styled action button matching Aura theme."""
        button = QPushButton(text, self)
        button.setObjectName("action_button")
        button.setMinimumHeight(32)
        font = QFont("JetBrains Mono", 10)
        button.setFont(font)
        return button

    def _create_icon_button(
        self, *, tooltip: str, icon_type: QStyle.StandardPixmap, size: tuple[int, int]
    ) -> QPushButton:
        """Create a compact icon button."""
        button = QPushButton("", self)
        button.setObjectName("icon_button")
        button.setToolTip(tooltip)
        icon = self.style().standardIcon(icon_type)
        button.setIcon(icon)
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(QSize(*size))
        return button

    def _apply_retro_style(self) -> None:
        """Apply retro terminal styling to the sidebar."""
        self.setStyleSheet("""
            ConversationSidebarWidget {
                background-color: #0a0a0a;
                border-right: 2px solid #FFB74D;
            }

            QPushButton#action_button {
                background-color: #2c2c2c;
                border: 1px solid #4a4a4a;
                color: #FFB74D;
                font-family: 'JetBrains Mono', monospace;
                font-size: 11px;
                font-weight: bold;
                border-radius: 4px;
                padding: 6px 12px;
                text-align: left;
            }

            QPushButton#action_button:hover {
                background-color: #3a3a3a;
                border-color: #FFB74D;
                color: #FFD27F;
            }

            QPushButton#action_button:pressed {
                background-color: #1e1e1e;
            }

            QPushButton#icon_button {
                background-color: transparent;
                border: 1px solid #3a3a3a;
                border-radius: 3px;
            }

            QPushButton#icon_button:hover {
                background-color: #2c2c2c;
                border-color: #FFB74D;
            }

            QTreeWidget#conversation_tree {
                background-color: #0a0a0a;
                border: none;
                color: #dcdcdc;
                font-family: 'JetBrains Mono', monospace;
                font-size: 11px;
                outline: none;
            }

            QTreeWidget#conversation_tree::item {
                padding: 6px 4px;
                border-radius: 3px;
                margin: 1px 4px;
            }

            QTreeWidget#conversation_tree::item:selected {
                background-color: #FFB74D;
                color: #000000;
                font-weight: bold;
            }

            QTreeWidget#conversation_tree::item:hover {
                background-color: #2c2c2c;
                color: #FFD27F;
            }

            QTreeWidget#conversation_tree::branch {
                background-color: transparent;
            }

            QTreeWidget#conversation_tree::branch:has-children:closed {
                image: url(none);
            }

            QTreeWidget#conversation_tree::branch:has-children:open {
                image: url(none);
            }
        """)

    def _toggle_collapse(self) -> None:
        """Toggle sidebar collapsed state."""
        if self._is_collapsed:
            self.setFixedWidth(250)
            self._collapse_btn.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowLeft)
            )
            self._tree.setVisible(True)
            self._new_chat_btn.setVisible(True)
            self._new_thread_btn.setVisible(self._project_active)
        else:
            self.setFixedWidth(40)
            self._collapse_btn.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowRight)
            )
            self._tree.setVisible(False)
            self._new_chat_btn.setVisible(False)
            self._new_thread_btn.setVisible(False)

        self._is_collapsed = not self._is_collapsed

    def _show_context_menu(self, position) -> None:
        """Show context menu for thread management."""
        item = self._tree.itemAt(position)
        if not item or not self._is_thread_item(item):
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1e1e1e;
                border: 1px solid #FFB74D;
                color: #dcdcdc;
                font-family: 'JetBrains Mono', monospace;
                font-size: 11px;
                padding: 4px;
            }
            QMenu::item:selected {
                background-color: #FFB74D;
                color: #000000;
            }
        """)

        rename_action = QAction("✏️  Rename", self)
        archive_action = QAction("📦 Archive", self)
        delete_action = QAction("🗑️  Delete", self)

        thread_id = item.data(0, Qt.ItemDataRole.UserRole)
        # Optional upgrade action for standalone chats
        upgrade_action = QAction("Upgrade to Project...", self)
        upgrade_action.triggered.connect(lambda: self.upgrade_to_project_requested.emit(thread_id))

        rename_action.triggered.connect(lambda: self._handle_rename_thread(thread_id))
        archive_action.triggered.connect(lambda: self.thread_archived.emit(thread_id))
        delete_action.triggered.connect(lambda: self.thread_deleted.emit(thread_id))

        menu.addAction(rename_action)
        # Only allow upgrade for standalone chats under CHATS section
        if item.parent() is self._chats_section:
            menu.addAction(upgrade_action)
        menu.addAction(archive_action)
        menu.addSeparator()
        menu.addAction(delete_action)

        menu.exec(self._tree.viewport().mapToGlobal(position))

    def _is_thread_item(self, item: QTreeWidgetItem) -> bool:
        """Check if item represents a thread (not a section or project)."""
        # Thread items have UserRole data (conversation_id)
        return item.data(0, Qt.ItemDataRole.UserRole) is not None

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Handle double-click on thread item."""
        if not self._is_thread_item(item):
            return

        thread_id = item.data(0, Qt.ItemDataRole.UserRole)
        if thread_id:
            self.thread_selected.emit(thread_id)
            self._set_active_thread(thread_id)

    def _handle_rename_thread(self, thread_id: str) -> None:
        """Handle thread rename request."""
        from PySide6.QtWidgets import QInputDialog

        item = self._find_thread_item(thread_id)
        if not item:
            return

        current_title = item.text(0).split("\n")[0]  # Get first line (title)
        new_title, ok = QInputDialog.getText(
            self,
            "Rename Thread",
            "Enter new title:",
            text=current_title,
        )

        if ok and new_title.strip():
            self.thread_renamed.emit(thread_id, new_title.strip())

    def _find_thread_item(self, thread_id: str) -> Optional[QTreeWidgetItem]:
        """Find tree item by thread ID."""
        for i in range(self._chats_section.childCount()):
            item = self._chats_section.child(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == thread_id:
                return item

        for i in range(self._projects_section.childCount()):
            project_item = self._projects_section.child(i)
            for j in range(project_item.childCount()):
                thread_item = project_item.child(j)
                if thread_item.data(0, Qt.ItemDataRole.UserRole) == thread_id:
                    return thread_item

        return None

    def _set_active_thread(self, thread_id: str) -> None:
        """Highlight the active thread."""
        self._current_thread_id = thread_id

        # Clear previous selection
        for i in range(self._chats_section.childCount()):
            self._chats_section.child(i).setSelected(False)

        for i in range(self._projects_section.childCount()):
            project_item = self._projects_section.child(i)
            for j in range(project_item.childCount()):
                project_item.child(j).setSelected(False)

        # Select new active thread
        item = self._find_thread_item(thread_id)
        if item:
            item.setSelected(True)
            self._tree.scrollToItem(item)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def add_chat_thread(
        self,
        thread_id: str,
        title: str,
        last_updated: Optional[datetime] = None,
    ) -> None:
        """Add a standalone chat thread to the CHATS section."""
        # Remove placeholder if present
        self._remove_placeholder_if_present(self._empty_chats_item)
        self._empty_chats_item = None

        item = QTreeWidgetItem(self._chats_section)
        item.setData(0, Qt.ItemDataRole.UserRole, thread_id)

        # Format: Title\nLast updated: 2h ago
        display_text = self._format_thread_display(title, last_updated)
        item.setText(0, display_text)

        font = QFont("JetBrains Mono", 10)
        item.setFont(0, font)
        self._update_empty_states()

    def add_project_thread(
        self,
        project_name: str,
        thread_id: str,
        title: str,
        last_updated: Optional[datetime] = None,
    ) -> None:
        """Add a thread under a project in the PROJECTS section."""
        # Remove placeholder if present
        self._remove_placeholder_if_present(self._empty_projects_item)
        self._empty_projects_item = None

        # Find or create project item
        project_item = self._find_or_create_project(project_name)

        # Add thread under project
        item = QTreeWidgetItem(project_item)
        item.setData(0, Qt.ItemDataRole.UserRole, thread_id)

        display_text = self._format_thread_display(title, last_updated)
        item.setText(0, display_text)

        font = QFont("JetBrains Mono", 10)
        item.setFont(0, font)
        self._update_empty_states()

    def _find_or_create_project(self, project_name: str) -> QTreeWidgetItem:
        """Find existing project item or create a new one."""
        for i in range(self._projects_section.childCount()):
            item = self._projects_section.child(i)
            if item.text(0) == f"📁 {project_name}":
                return item

        # Create new project item
        project_item = QTreeWidgetItem(self._projects_section, [f"📁 {project_name}"])
        project_item.setExpanded(True)
        font = QFont("JetBrains Mono", 11)
        font.setBold(True)
        project_item.setFont(0, font)

        return project_item

    def _format_thread_display(
        self, title: str, last_updated: Optional[datetime]
    ) -> str:
        """Format thread display text with title and timestamp."""
        if not title:
            title = "Untitled"

        # Truncate long titles
        if len(title) > 30:
            title = title[:27] + "..."

        if last_updated:
            time_str = self._format_relative_time(last_updated)
            return f"{title}\n{time_str}"

        return title

    def _format_relative_time(self, dt: datetime) -> str:
        """Format datetime into human-friendly relative time.

        Examples: '2 hours ago', 'Yesterday', '3 days ago', 'Oct 15', 'Oct 15, 2024'
        """
        from datetime import datetime as _dt
        now = _dt.now(dt.tzinfo) if dt.tzinfo else _dt.now()
        diff = now - dt

        seconds = int(diff.total_seconds())
        if seconds < 60:
            return "just now"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        days = diff.days
        if days == 1:
            return "Yesterday"
        if days < 7:
            return f"{days} days ago"
        if now.year == dt.year:
            # Use day without leading zero in a cross-platform way
            return dt.strftime("%b %d").replace(" 0", " ")
        return dt.strftime("%b %d, %Y").replace(" 0", " ")

    def clear_threads(self) -> None:
        """Clear all threads from the sidebar."""
        self._chats_section.takeChildren()
        self._projects_section.takeChildren()
        self._update_empty_states()

    def set_project_active(self, active: bool) -> None:
        """Show/hide New Thread button based on project state."""
        self._project_active = active
        if not self._is_collapsed:
            self._new_thread_btn.setVisible(active)

    def set_active_thread(self, thread_id: str) -> None:
        """Set the currently active thread (highlight it)."""
        self._set_active_thread(thread_id)

    def remove_thread(self, thread_id: str) -> None:
        """Remove a thread from the sidebar."""
        item = self._find_thread_item(thread_id)
        if item and item.parent():
            parent = item.parent()
            parent.removeChild(item)
        self._update_empty_states()
