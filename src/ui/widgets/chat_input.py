from PySide6.QtWidgets import QTextEdit
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent

class ChatInputTextEdit(QTextEdit):
    """
    A custom QTextEdit that emits a sendMessage signal on Enter pressed
    and handles Shift+Enter for newlines.
    """
    sendMessage = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def keyPressEvent(self, event: QKeyEvent):
        """
        Overrides the key press event to handle Enter and Shift+Enter.
        """
        # Check if Enter key is pressed and the Shift modifier is NOT held down
        if (event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter) and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            # Emit the custom signal and accept the event to prevent a newline
            self.sendMessage.emit()
            event.accept()
        else:
            # For all other key presses, use the default behavior
            super().keyPressEvent(event)