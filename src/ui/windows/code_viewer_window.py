import logging
import os
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QLabel, QSplitter,
                               QTreeWidget, QTreeWidgetItem, QTabWidget, QTextEdit)
from PySide6.QtCore import Qt, Signal, QObject, Slot, QSettings, QByteArray
from PySide6.QtGui import QTextCursor
from src.aura.app.event_bus import EventBus
from src.ui.widgets.syntax_highlighter import PythonSyntaxHighlighter
from src.aura.services.ast_service import ASTService

logger = logging.getLogger(__name__)


class CodeViewerSignaller(QObject):
    """A signaller to safely update the UI from other threads."""
    validated_code_saved = Signal(dict)
    project_activated = Signal(dict)
    code_chunk_generated = Signal(dict)


class CodeViewerWindow(QWidget):
    """
    A window to display generated code, featuring a file tree and a tabbed editor.
    """
    CODE_VIEWER_STYLESHEET = """
        QWidget {
            background-color: #000000;
            color: #dcdcdc;
            font-family: "JetBrains Mono", "Courier New", Courier, monospace;
            border: 1px solid #FFB74D; /* Amber */
            border-radius: 5px;
        }
        QLabel#title_label {
            color: #FFB74D; /* Amber */
            font-weight: bold;
            font-size: 16px;
            padding: 5px;
            border: none;
            max-height: 25px;
        }
        QTreeWidget {
            background-color: #2c2c2c;
            border: none;
            font-size: 14px;
        }
        QTreeWidget::item:selected {
            background-color: #FFB74D;
            color: #000000;
        }
        QTabWidget::pane {
            border-top: 1px solid #4a4a4a;
        }
        QTabBar::tab {
            background: #2c2c2c;
            border: 1px solid #4a4a4a;
            border-bottom: none;
            padding: 8px 12px;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
        }
        QTabBar::tab:selected {
            background: #000000;
            border: 1px solid #FFB74D;
            border-bottom: 1px solid #000000;
        }
        QTextEdit {
            background-color: #1e1e1e;
            border: none;
            font-size: 14px;
            padding: 5px;
        }
    """

    def __init__(self, event_bus: EventBus, ast_service: ASTService, parent=None):
        """Initializes the CodeViewerWindow."""
        super().__init__(parent)
        self.event_bus = event_bus
        self.ast_service = ast_service
        self.signaller = CodeViewerSignaller()
        self.open_tabs = {}  # Maps file_path to its editor widget
        self.tree_items = {} # Maps a full path to its tree widget item for quick lookup

        self.setWindowTitle("Code Viewer")
        self.setWindowFlags(Qt.WindowType.Tool)
        self.setStyleSheet(self.CODE_VIEWER_STYLESHEET)

        self._init_ui()
        self._register_event_handlers()
        self._load_settings()

    def _init_ui(self):
        """Initializes the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        title_label = QLabel("GENERATED CODE")
        title_label.setObjectName("title_label")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderHidden(True)
        self.file_tree.itemClicked.connect(self._on_file_tree_item_clicked)

        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self._close_tab)

        self.splitter.addWidget(self.file_tree)
        self.splitter.addWidget(self.tab_widget)

        layout.addWidget(title_label)
        layout.addWidget(self.splitter)

    def _load_settings(self):
        """Loads window geometry and splitter state from settings."""
        settings = QSettings()
        geometry = settings.value("code_viewer/geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)
        else:
            # Default geometry if no settings are found
            self.setGeometry(100, 100, 500, 700)

        splitter_state = settings.value("code_viewer/splitterState")
        if isinstance(splitter_state, QByteArray):
            self.splitter.restoreState(splitter_state)
        else:
            # Default splitter sizes
            self.splitter.setSizes([150, 350])

    def closeEvent(self, event):
        """Saves window geometry and splitter state on close."""
        settings = QSettings()
        settings.setValue("code_viewer/geometry", self.saveGeometry())
        settings.setValue("code_viewer/splitterState", self.splitter.saveState())
        super().closeEvent(event)

    def _register_event_handlers(self):
        """Subscribes to relevant events from the event bus."""
        self.signaller.validated_code_saved.connect(self._on_validated_code_saved)
        self.event_bus.subscribe(
            "VALIDATED_CODE_SAVED",
            lambda event: self.signaller.validated_code_saved.emit(event.payload)
        )
        self.signaller.project_activated.connect(self._on_project_activated)
        self.event_bus.subscribe(
            "PROJECT_ACTIVATED",
            lambda event: self.signaller.project_activated.emit(event.payload)
        )
        # Real-time code chunk updates
        self.signaller.code_chunk_generated.connect(self._on_code_chunk_generated)
        self.event_bus.subscribe(
            "CODE_CHUNK_GENERATED",
            lambda event: self.signaller.code_chunk_generated.emit(event.payload)
        )

    def _add_path_to_tree(self, file_path: str):
        """Adds a file path to the tree, creating parent directories as needed."""
        path_parts = file_path.replace("\\", "/").split("/")
        current_parent_item = self.file_tree.invisibleRootItem()
        current_path_key = ""

        for part in path_parts:
            if not current_path_key:
                current_path_key = part
            else:
                current_path_key = f"{current_path_key}/{part}"

            child_item = self.tree_items.get(current_path_key)

            if not child_item:
                child_item = QTreeWidgetItem([part])
                child_item.setData(0, Qt.ItemDataRole.UserRole, current_path_key)
                current_parent_item.addChild(child_item)
                self.tree_items[current_path_key] = child_item
            
            current_parent_item = child_item

    @Slot(dict)
    def _on_validated_code_saved(self, payload: dict):
        """Handles the validated_code_saved signal to update the UI only after validation."""
        file_path = payload.get("file_path")
        code = payload.get("code")

        if not file_path or code is None:
            logger.warning("VALIDATED_CODE_SAVED event received with missing payload.")
            return

        self._add_path_to_tree(file_path)

        if file_path in self.open_tabs:
            editor = self.open_tabs[file_path]
            editor.setPlainText(code)
            tab_index = self.tab_widget.indexOf(editor)
            self.tab_widget.setCurrentIndex(tab_index)
        else:
            editor = QTextEdit()
            editor.setPlainText(code)
            editor.setReadOnly(True)
            highlighter = PythonSyntaxHighlighter(editor.document())

            self.open_tabs[file_path] = editor
            tab_index = self.tab_widget.addTab(editor, os.path.basename(file_path))
            self.tab_widget.setCurrentIndex(tab_index)
            self.tab_widget.setTabToolTip(tab_index, file_path)

    @Slot(dict)
    def _on_code_chunk_generated(self, payload: dict):
        """Handles streaming code chunks to create/append content in real-time."""
        file_path = payload.get("file_path")
        chunk = payload.get("chunk", "")

        if not file_path:
            logger.warning("CODE_CHUNK_GENERATED received without file_path.")
            return

        # Ensure path appears in the tree on first chunk
        if file_path not in self.tree_items:
            self._add_path_to_tree(file_path)

        # Create a new editor/tab for first chunk of a file
        if file_path not in self.open_tabs:
            editor = QTextEdit()
            editor.setReadOnly(True)
            highlighter = PythonSyntaxHighlighter(editor.document())
            self.open_tabs[file_path] = editor
            tab_index = self.tab_widget.addTab(editor, os.path.basename(file_path))
            self.tab_widget.setCurrentIndex(tab_index)
            self.tab_widget.setTabToolTip(tab_index, file_path)
        else:
            editor = self.open_tabs[file_path]

        # Append the incoming chunk
        if chunk:
            editor.moveCursor(QTextCursor.End)
            editor.insertPlainText(chunk)
            editor.moveCursor(QTextCursor.End)

    @Slot(dict)
    def _on_project_activated(self, payload: dict):
        """
        Handles the PROJECT_ACTIVATED signal to populate the file tree with existing files.
        """
        logger.info(f"Project activated: {payload.get('project_name')}. Populating file tree.")
        # Clear existing tree items before repopulating
        self.file_tree.clear()
        self.tree_items.clear()

        indexed_files = self.ast_service.get_indexed_file_paths()
        for file_path in indexed_files:
            self._add_path_to_tree(file_path)

    @Slot(QTreeWidgetItem, int)
    def _on_file_tree_item_clicked(self, item: QTreeWidgetItem, column: int):
        """Handles clicks on the file tree to open/focus tabs."""
        file_path = item.data(0, Qt.ItemDataRole.UserRole)
        if file_path and file_path in self.open_tabs:
            editor = self.open_tabs[file_path]
            tab_index = self.tab_widget.indexOf(editor)
            self.tab_widget.setCurrentIndex(tab_index)

    @Slot(int)
    def _close_tab(self, index: int):
        """
        Closes a tab and removes it from our tracking dictionary.
        """
        widget = self.tab_widget.widget(index)
        if widget:
            file_path_to_remove = None
            for path, editor in self.open_tabs.items():
                if editor == widget:
                    file_path_to_remove = path
                    break

            if file_path_to_remove:
                del self.open_tabs[file_path_to_remove]
                self.tab_widget.removeTab(index)
                widget.deleteLater()
