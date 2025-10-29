import base64

from PySide6.QtWidgets import QLabel, QMessageBox, QTextEdit
from PySide6.QtCore import Qt, Signal, QBuffer, QIODevice
from PySide6.QtGui import QKeyEvent, QImage, QPixmap

class ChatInputTextEdit(QTextEdit):
    """
    A custom QTextEdit that emits a sendMessage signal on Enter pressed
    and handles Shift+Enter for newlines.
    """
    sendMessage = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._attached_image = None
        self._max_image_bytes = 5 * 1024 * 1024  # 5 MB limit
        self._attachment_label = QLabel("[Image attached]", self)
        self._attachment_label.hide()
        self._attachment_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._attachment_label.setStyleSheet(
            "QLabel {"
            "   background-color: rgba(52, 83, 109, 0.9);"
            "   color: #f5f8ff;"
            "   padding: 2px 6px;"
            "   border-radius: 4px;"
            "   font-size: 11px;"
            "}"
        )
        self.textChanged.connect(self._update_attachment_indicator_position)

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

    def insertFromMimeData(self, source):
        """
        Overrides paste handling to capture images from the clipboard.
        """
        if source.hasImage():
            image = source.imageData()
            if isinstance(image, QPixmap):
                image = image.toImage()

            if not isinstance(image, QImage):
                super().insertFromMimeData(source)
                return

            encoded = self._encode_image(image)
            if encoded is None:
                return

            self._attached_image = encoded
            self._attachment_label.show()
            self._update_attachment_indicator_position()
            return

        super().insertFromMimeData(source)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_attachment_indicator_position()

    def take_attached_image(self):
        """
        Returns the current attached image (if any) and clears it.
        """
        image = self._attached_image
        if image:
            self._attached_image = None
            self._attachment_label.hide()
        return image

    def clear(self):
        super().clear()
        if self._attached_image:
            self._attached_image = None
            self._attachment_label.hide()

    def _encode_image(self, image: QImage):
        """
        Convert the provided QImage to base64 with bounds checking.
        """
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        success = image.save(buffer, "PNG")
        buffer.close()

        if not success:
            QMessageBox.warning(self, "Image Paste Failed", "Could not read image data from the clipboard.")
            return None

        image_bytes = buffer.data()
        if image_bytes.size() > self._max_image_bytes:
            QMessageBox.warning(
                self,
                "Image Too Large",
                "Images larger than 5 MB cannot be attached. Please resize the image and try again.",
            )
            return None

        encoded_data = base64.b64encode(bytes(image_bytes)).decode("ascii")
        return {
            "mime_type": "image/png",
            "data": encoded_data,
            "size": image_bytes.size(),
        }

    def _update_attachment_indicator_position(self):
        if not self._attachment_label.isVisible():
            return
        self._attachment_label.adjustSize()
        margin = 8
        x = margin
        y = self.viewport().height() - self._attachment_label.height() - margin
        self._attachment_label.move(x, y)
