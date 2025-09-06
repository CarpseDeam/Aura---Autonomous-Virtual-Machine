import logging
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QLabel, QSplitter,
                               QTreeWidget, QTreeWidgetItem, QTabWidget, QTextEdit, QApplication)
from PySide6.QtCore import Qt, Signal, QObject, Slot
from src.aura.app.event_bus import EventBus
from src.ui.widgets.syntax_highlighter import PythonSyntaxHighlighter

logger = logging.getLogger(__name__)


class CodeViewerSignaller(QObject):
    """A signaller to safely update the UI from other threads."""
    code_generated = Signal(dict)


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

    def __init__(self, event_bus: EventBus, parent=None):
        """Initializes the CodeViewerWindow."""
        super().__init__(parent)
        self.event_bus = event_bus
        self.signaller = CodeViewerSignaller()
        self.open_tabs = {}  # Maps file_path to its editor widget

        self.setWindowTitle("Code Viewer")
        self.setWindowFlags(Qt.WindowType.Tool)
        self.setGeometry(100, 100, 500, 700)
        self.setStyleSheet(self.CODE_VIEWER_STYLESHEET)

        self._init_ui()
        self._register_event_handlers()

    def _init_ui(self):
        """Initializes the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        title_label = QLabel("GENERATED CODE")
        title_label.setObjectName("title_label")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderHidden(True)
        self.file_tree.itemClicked.connect(self._on_file_tree_item_clicked)

        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self._close_tab)

        splitter.addWidget(self.file_tree)
        splitter.addWidget(self.tab_widget)
        splitter.setSizes([150, 350])  # Initial size distribution

        layout.addWidget(title_label)
        layout.addWidget(splitter)

    def _register_event_handlers(self):
        """Subscribes to relevant events from the event bus."""
        self.signaller.code_generated.connect(self._on_code_generated)
        self.event_bus.subscribe(
            "CODE_GENERATED",
            lambda event: self.signaller.code_generated.emit(event.payload)
        )

    @Slot(dict)
    def _on_code_generated(self, payload: dict):
        """Handles the code_generated signal to update the UI."""
        file_path = payload.get("file_path")
        code = payload.get("code")

        if not file_path or code is None:
            logger.warning("CODE_GENERATED event received with missing payload.")
            return

        # Add file to tree if it doesn't exist
        if not self.file_tree.findItems(file_path, Qt.MatchFlag.MatchExactly):
            tree_item = QTreeWidgetItem([file_path])
            self.file_tree.addTopLevelItem(tree_item)

        # Create or update tab
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
            tab_index = self.tab_widget.addTab(editor, file_path.split('/')[-1])
            self.tab_widget.setCurrentIndex(tab_index)
            self.tab_widget.setTabToolTip(tab_index, file_path)

    @Slot(QTreeWidgetItem)
    def _on_file_tree_item_clicked(self, item: QTreeWidgetItem, column: int):
        """Handles clicks on the file tree to open/focus tabs."""
        file_path = item.text(0)
        if file_path in self.open_tabs:
            editor = self.open_tabs[file_path]
            tab_index = self.tab_widget.indexOf(editor)
            self.tab_widget.setCurrentIndex(tab_index)
        else:
            # This case shouldn't normally be hit if code is always added via event
            logger.warning(f"File '{file_path}' clicked in tree but not found in open tabs.")

    @Slot(int)
    def _close_tab(self, index: int):
        """Closes a tab and removes it from our tracking dictionary."""
        widget = self.tab_widget.widget(index)
        if widget:
            # Find the file_path associated with this widget
            file_path_to_remove = None
            for path, editor in self.open_tabs.items():
                if editor == widget:
                    file_path_to_remove = path
                    break

            if file_path_to_remove:
                del self.open_tabs[file_path_to_remove]
                self.tab_widget.removeTab(index)
                widget.deleteLater()