from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QWidget, QPushButton, QSizePolicy

from src.aura.services.image_storage_service import ImageStorageService
from src.ui.widgets.chat_input import ChatInputTextEdit

ImageAttachment = Union[str, Dict[str, Any], Path]
NormalizedAttachment = Optional[Union[str, Dict[str, Any]]]


class ChatInputWidget(QWidget):
    """
    Wrapper around the chat input text edit that exposes a clean message API.
    """

    message_requested = Signal()

    def __init__(self, image_storage: Optional[ImageStorageService], parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._image_storage = image_storage
        self._text_edit = ChatInputTextEdit(image_storage=image_storage)
        self._text_edit.setObjectName("chat_input")
        self._text_edit.setPlaceholderText("Type here. Shift+Enter for newline. Enter to send.")
        self._text_edit.sendMessage.connect(self.message_requested.emit)
        self._text_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Send button (visible action next to input)
        self._send_button = QPushButton("Send", self)
        self._send_button.setObjectName("send_button")
        self._send_button.setFixedWidth(70)
        # Match input height within the row
        self._send_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._send_button.setToolTip("Send (Enter). Shift+Enter for newline")
        self._send_button.clicked.connect(self._handle_send)

        # Style matches Aura theme (amber accent, dark background)
        self._send_button.setStyleSheet(
            "QPushButton#send_button {"
            "  background-color: #2a170a;"
            "  color: #dcdcdc;"
            "  border: 1px solid #FFB74D;"
            "  border-radius: 5px;"
            "  font-weight: bold;"
            "}"
            "QPushButton#send_button:hover {"
            "  background-color: #FFB74D;"
            "  color: #2a170a;"
            "}"
        )

        layout.addWidget(self._text_edit, 5)
        layout.addWidget(self._send_button)

    def take_message(self) -> Optional[Tuple[str, NormalizedAttachment]]:
        """
        Extract the current message payload and clear the input.
        """
        raw_text = self._text_edit.toPlainText()
        image_attachment = self._text_edit.take_attached_image()
        user_text = raw_text.strip()
        if not user_text and image_attachment is None:
            return None

        normalized_image = self._normalize_attachment(image_attachment)
        self._text_edit.clear()
        return user_text, normalized_image

    def focus_input(self) -> None:
        """
        Restore focus to the text edit.
        """
        self._text_edit.setFocus()

    def _handle_send(self) -> None:
        """Emit the message request when the send button is pressed."""
        self.message_requested.emit()

    def setEnabled(self, enabled: bool) -> None:  # noqa: D401 - QWidget signature
        super().setEnabled(enabled)
        self._text_edit.setEnabled(enabled)

    def _normalize_attachment(self, image: Optional[ImageAttachment]) -> NormalizedAttachment:
        if image is None:
            return None
        if isinstance(image, dict):
            if isinstance(image.get("path"), str):
                return image["path"]
            if isinstance(image.get("relative_path"), str):
                return image["relative_path"]
            data = image.get("data")
            if data and self._image_storage:
                saved_path = self._image_storage.save_image(data, image.get("mime_type") or "image/png")
                if saved_path:
                    return saved_path
            return image
        if isinstance(image, Path):
            return image.as_posix()
        if isinstance(image, str):
            return image
        return image
