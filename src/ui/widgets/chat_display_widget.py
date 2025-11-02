from __future__ import annotations

import logging
import re
from html import escape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

import markdown
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QTextCursor, QTextOption
from PySide6.QtWidgets import QTextBrowser, QWidget

from src.aura.services.image_storage_service import ImageStorageService

logger = logging.getLogger(__name__)


AURA_RESPONSE_CSS = """
<style>
    .aura-response-content {
        font-family: 'JetBrains Mono', monospace;
        font-size: 13px;
        color: #FFB74D;
        background: transparent;
        line-height: 1.4;
        word-wrap: break-word;
    }
    .aura-response-content h1,
    .aura-response-content h2,
    .aura-response-content h3,
    .aura-response-content h4,
    .aura-response-content h5,
    .aura-response-content h6 {
        color: #FFD27F;
        font-weight: bold;
        margin: 8px 0 4px 0;
    }
    .aura-response-content h1 { font-size: 16px; }
    .aura-response-content h2 { font-size: 15px; }
    .aura-response-content h3 { font-size: 14px; }
    .aura-response-content p {
        margin: 4px 0;
        color: #FFB74D;
    }
    .aura-response-content ul,
    .aura-response-content ol {
        margin: 4px 0;
        padding-left: 20px;
    }
    .aura-response-content li {
        margin: 2px 0;
        color: #FFB74D;
    }
    .aura-response-content code {
        background-color: #2a2a2a;
        color: #64B5F6;
        padding: 1px 4px;
        border-radius: 2px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 12px;
    }
    .aura-response-content pre {
        background-color: #1e1e1e;
        color: #FFE0A3;
        padding: 8px;
        border-radius: 4px;
        border-left: 3px solid #FFB74D;
        margin: 8px 0;
        white-space: pre-wrap;
        font-family: 'JetBrains Mono', monospace;
        font-size: 12px;
        overflow-x: auto;
    }
    .aura-response-content pre code {
        background: transparent;
        padding: 0;
    }
    .aura-response-content blockquote {
        border-left: 3px solid #64B5F6;
        margin: 8px 0;
        padding-left: 12px;
        color: #E0C48A;
    }
    .aura-response-content strong,
    .aura-response-content b {
        color: #FFD27F;
        font-weight: bold;
    }
    .aura-response-content em,
    .aura-response-content i {
        color: #64B5F6;
        font-style: italic;
    }
    .aura-response-content a {
        color: #64B5F6;
        text-decoration: underline;
    }
    .diff-message {
        border: 1px solid #4a4a4a;
        border-radius: 6px;
        padding: 10px;
        margin: 12px 0;
        background: rgba(20, 20, 20, 0.85);
    }
    .diff-header {
        font-weight: bold;
        color: #FFD27F;
        margin-bottom: 6px;
    }
    .diff-file-list {
        list-style: none;
        margin: 0 0 8px 0;
        padding: 0;
        color: #FFB74D;
        font-size: 12px;
    }
    .diff-file-list li {
        margin: 2px 0;
    }
    .diff-block {
        background-color: #0e0e0e;
        border-radius: 4px;
        padding: 8px;
        border: 1px solid #333;
        font-size: 12px;
        overflow-x: auto;
    }
    .diff-line {
        display: block;
        white-space: pre;
        font-family: 'JetBrains Mono', monospace;
    }
    .diff-line.added { color: #66BB6A; }
    .diff-line.removed { color: #EF5350; }
    .diff-line.meta { color: #64B5F6; }
    .diff-line.neutral { color: #dcdcdc; }
    .diff-actions {
        margin-top: 10px;
        text-align: right;
    }
    .diff-actions a {
        display: inline-block;
        margin-left: 8px;
        padding: 4px 10px;
        border-radius: 4px;
        text-decoration: none;
        font-weight: bold;
    }
    .diff-actions a.accept {
        background-color: #2E7D32;
        color: #ffffff;
    }
    .diff-actions a.reject {
        background-color: #C62828;
        color: #ffffff;
    }
    .diff-status {
        margin-top: 8px;
        font-size: 12px;
        color: #64B5F6;
        text-align: right;
    }
</style>
"""

ImageReference = Optional[Union[str, Dict[str, Any]]]


class ChatDisplayWidget(QTextBrowser):
    """
    Retro terminal chat display with Aura-specific rendering helpers.
    """

    anchor_requested = Signal(QUrl)

    def __init__(self, image_storage: Optional[ImageStorageService], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._image_storage = image_storage
        self._styles_injected = False

        self.setObjectName("chat_display")
        self.setFocusPolicy(Qt.NoFocus)
        self.setOpenLinks(False)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setReadOnly(True)
        self.anchorClicked.connect(self.anchor_requested.emit)

    def display_boot_sequence(self, boot_sequence: Sequence[Dict[str, Any]]) -> None:
        """
        Render the startup boot sequence messages.
        """
        self.clear()
        self._styles_injected = False
        self._ensure_styles()
        for item in boot_sequence:
            text = (item or {}).get("text", "")
            if not text:
                continue
            boot_html = (
                "<div style=\"color: #FFB74D; font-family: JetBrains Mono, monospace; "
                "font-size: 13px; margin: 2px 0;\">"
                f"{escape(text)}"
                "</div><br>"
            )
            self.insertHtml(boot_html)
        self.ensureCursorVisible()

    def display_system_message(self, category: str, message: str) -> None:
        """
        Render a system-level message highlighted by category.
        """
        self._ensure_styles()
        color_map = {
            "KERNEL": "#64B5F6",
            "SYSTEM": "#66BB6A",
            "NEURAL": "#FFB74D",
            "SUCCESS": "#39FF14",
            "WARNING": "#FFEE58",
            "ERROR": "#FF4444",
            "WORKSPACE": "#64B5F6",
            "USER": "#64B5F6",
            "DEFAULT": "#dcdcdc",
        }
        color = color_map.get(category.upper(), color_map["DEFAULT"])
        safe_message = escape(message).replace("\n", "<br>")
        html = (
            '<div style="margin: 12px 0; text-align: center;">'
            f'<span style="color: {color}; font-size: 12px;">[{category.upper()}] {safe_message}</span>'
            "</div><br>"
        )
        self.moveCursor(QTextCursor.End)
        self.insertHtml(html)
        self.ensureCursorVisible()

    def display_user_message(self, user_text: str, image: ImageReference) -> None:
        """
        Render a user-authored chat bubble and optional image attachment.
        """
        self._ensure_styles()
        safe_text = escape(user_text).replace("\n", "<br>") if user_text else ""

        content_parts: List[str] = []
        if safe_text:
            content_parts.append(safe_text)

        display_image = self._load_image(image)
        has_image_reference = image is not None

        if display_image and display_image.get("base64_data"):
            mime_type = display_image.get("mime_type") or "image/png"
            image_html = (
                '<div style="margin-top: 10px; text-align: center;">'
                f'<img src="data:{mime_type};base64,{display_image["base64_data"]}" '
                'style="max-width: 260px; max-height: 180px; border-radius: 6px; '
                'border: 1px solid rgba(255, 255, 255, 0.2); display: block; margin: 0 auto;" '
                'alt="User attached image" />'
                "</div>"
            )
            content_parts.append(image_html)
        elif has_image_reference:
            content_parts.append('<span style="color: #7CC4FF;">[Image attached]</span>')

        if not content_parts:
            content_parts.append('<span style="color: #7CC4FF;">[Image attached]</span>')

        user_html = (
            '<div style="margin: 15px 0; text-align: right;">'
            '<div style="display: inline-block; max-width: 65%; background-color: #34536d; '
            'color: #f5f8ff; padding: 14px; border-radius: 8px; text-align: left; '
            "font-family: 'JetBrains Mono', monospace; font-size: 14px; line-height: 1.55;\">"
            '<strong style="color: #7CC4FF; font-size: 11px;">YOU</strong><br>'
            f"{''.join(content_parts)}"
            "</div>"
            "</div><br>"
        )
        self.moveCursor(QTextCursor.End)
        self.insertHtml(user_html)
        self.ensureCursorVisible()

    def display_aura_response(self, response_text: str) -> None:
        """
        Render Aura's markdown response on the left-hand side of the chat.
        """
        self._ensure_styles()
        normalized_text = response_text.replace("\r\n", "\n").replace("\r", "\n")
        html_content = markdown.markdown(
            normalized_text,
            extensions=[
                "markdown.extensions.fenced_code",
                "markdown.extensions.nl2br",
                "markdown.extensions.sane_lists",
            ],
            output_format="html5",
        )
        heading_styles = {
            "h1": "color: #FFF3D2; font-size: 18px; margin: 10px 0 6px 0;",
            "h2": "color: #FFEFC5; font-size: 17px; margin: 10px 0 6px 0;",
            "h3": "color: #FFEBC0; font-size: 16px; margin: 8px 0 4px 0;",
            "h4": "color: #FFE7B8; font-size: 15px; margin: 8px 0 4px 0;",
            "h5": "color: #FFE3B0; font-size: 14px; margin: 6px 0 4px 0;",
            "h6": "color: #FFDFA6; font-size: 13px; margin: 6px 0 4px 0;",
        }
        for tag, style in heading_styles.items():
            html_content = html_content.replace(f"<{tag}>", f"<{tag} style=\"{style}\">")
        html_content = html_content.replace(
            "<pre>",
            "<pre style=\"background-color: #211207; color: #FFEBD2; padding: 10px; "
            "border-radius: 6px; border-left: 3px solid #FFCF8C; margin: 8px 0; "
            "white-space: pre-wrap; font-family: 'JetBrains Mono', monospace; font-size: 13px;\">",
        )
        html_content = html_content.replace(
            "<code>",
            "<code style=\"background-color: #3a2310; color: #FFEBD2; padding: 2px 6px; "
            "border-radius: 4px; font-family: 'JetBrains Mono', monospace; font-size: 13px;\">",
        )
        html_content = html_content.replace("<p>", "<p style=\"margin: 6px 0;\">")
        aura_html = (
            '<div style="margin: 15px 0; text-align: left;">'
            '<div style="display: inline-block; max-width: 70%; background-color: #2a170a; '
            'color: #FFEBD2; padding: 14px; border-radius: 8px; '
            "font-family: 'JetBrains Mono', monospace; font-size: 14px; line-height: 1.55;\">"
            '<strong style="color: #FFEFC5; font-size: 11px;">AURA</strong><br>'
            f"{html_content}"
            "</div>"
            "</div><br>"
        )
        self.moveCursor(QTextCursor.End)
        self.insertHtml(aura_html)
        self.ensureCursorVisible()

    def display_error(self, message: str) -> None:
        """
        Display an error response in-line within the chat display.
        """
        self._ensure_styles()
        processed = escape(message).replace("\n", "<br>").replace(" ", "&nbsp;")
        error_html = (
            "<div style=\"color: #FF4444; font-family: JetBrains Mono, monospace; "
            "font-size: 13px; margin: 2px 0;\">"
            f"[ERROR] {processed}"
            "</div><br>"
        )
        self.moveCursor(QTextCursor.End)
        self.insertHtml(error_html)
        self.ensureCursorVisible()

    def display_diff_message(self, payload: Dict[str, Any], *, pending: bool, auto_applied: bool) -> None:
        """
        Display a diff summary with per-file breakdown and action links.
        """
        self._ensure_styles()
        diff_html = self._build_diff_html(payload, pending=pending, auto_applied=auto_applied)
        self.moveCursor(QTextCursor.End)
        self.insertHtml(diff_html)
        self.insertHtml("<br>")
        self.ensureCursorVisible()

    def _ensure_styles(self) -> None:
        if self._styles_injected:
            return
        self.insertHtml(AURA_RESPONSE_CSS)
        self._styles_injected = True

    def _load_image(self, image: ImageReference) -> Optional[Dict[str, Any]]:
        if image is None:
            return None

        if isinstance(image, dict) and image.get("base64_data"):
            return image

        image_reference: Optional[str] = None
        if isinstance(image, dict):
            if isinstance(image.get("path"), str):
                image_reference = image["path"]
            elif isinstance(image.get("relative_path"), str):
                image_reference = image["relative_path"]
        elif isinstance(image, Path):
            image_reference = image.as_posix()
        elif isinstance(image, str):
            image_reference = image

        if image_reference and self._image_storage:
            loaded = self._image_storage.load_image(image_reference)
            if loaded:
                return loaded
            logger.warning("Failed to load image for display from %s", image_reference)
        return None

    def _build_diff_html(self, payload: Dict[str, Any], *, pending: bool, auto_applied: bool) -> str:
        change_id = payload.get("change_id")
        summary = payload.get("summary") or {}
        files = payload.get("files") or []

        total_files = summary.get("total_files") or len(files)
        additions = summary.get("total_additions", 0)
        deletions = summary.get("total_deletions", 0)

        header_parts = [
            f"Change {self._short_change_id(change_id)}",
            f"{total_files} file{'s' if total_files != 1 else ''}",
            f"+{additions} / -{deletions}",
        ]
        header_html = " | ".join(header_parts)

        file_items: Iterable[str] = []
        file_item_list: List[str] = []
        for file_info in files:
            path = file_info.get("display_path") or file_info.get("relative_path") or "unknown"
            add = file_info.get("additions", 0)
            remove = file_info.get("deletions", 0)
            badge = " (new)" if file_info.get("is_new_file") else ""
            file_item_list.append(f"<li><strong>{escape(path)}</strong> (+{add} / -{remove}){badge}</li>")
        file_items = file_item_list or ["<li><strong>Unknown file</strong></li>"]

        diff_sections: List[str] = []
        for file_info in files:
            path = file_info.get("display_path") or file_info.get("relative_path") or "unknown"
            diff_text = file_info.get("diff", "")
            diff_lines_html = self._format_diff_lines(diff_text)
            diff_sections.append(
                "<div>"
                f"<div style='font-size:12px; color:#64B5F6; margin-bottom:4px;'>{escape(path)}</div>"
                f"<pre class='diff-block'>{diff_lines_html}</pre>"
                "</div>"
            )

        actions_html = ""
        status_html = ""
        if pending:
            actions_html = (
                "<div class='diff-actions'>"
                f"<a href='aura://reject?change_id={change_id}' class='reject'>Reject</a>"
                f"<a href='aura://accept?change_id={change_id}' class='accept'>Accept</a>"
                "</div>"
            )
        else:
            status_text = "Changes applied automatically." if auto_applied else "Changes applied."
            status_html = f"<div class='diff-status'>{escape(status_text)}</div>"

        return (
            "<div class='diff-message'>"
            f"<div class='diff-header'>{escape(header_html)}</div>"
            f"<ul class='diff-file-list'>{''.join(file_items)}</ul>"
            f"{''.join(diff_sections)}"
            f"{actions_html}{status_html}"
            "</div>"
        )

    def _format_diff_lines(self, diff_text: str) -> str:
        lines_html: List[str] = []
        for raw_line in (diff_text or "").splitlines():
            line = raw_line or ""
            if line.startswith("@@"):
                match = re.match(r"@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@", line)
                if match:
                    old_start = int(match.group(1))
                    old_count = int(match.group(2)) if match.group(2) else 1
                    new_start = int(match.group(3))
                    new_count = int(match.group(4)) if match.group(4) else 1
                    old_end = old_start + old_count - 1
                    new_end = new_start + new_count - 1
                    old_range = f"Line {old_start}" if old_count == 1 else f"Lines {old_start}-{old_end}"
                    new_range = f"Line {new_start}" if new_count == 1 else f"Lines {new_start}-{new_end}"
                    line_info = f"@@ {old_range} -> {new_range} @@"
                    lines_html.append(f"<span class='diff-line meta'>{escape(line_info)}</span>")
                else:
                    lines_html.append(f"<span class='diff-line meta'>{escape(line)}</span>")
            elif line.startswith("+") and not line.startswith("+++"):
                lines_html.append(f"<span class='diff-line added'>{escape(line)}</span>")
            elif line.startswith("-") and not line.startswith("---"):
                lines_html.append(f"<span class='diff-line removed'>{escape(line)}</span>")
            elif line.startswith("diff ") or line.startswith("---") or line.startswith("+++"):
                lines_html.append(f"<span class='diff-line meta'>{escape(line)}</span>")
            else:
                lines_html.append(f"<span class='diff-line neutral'>{escape(line)}</span>")

        if not lines_html:
            lines_html.append("<span class='diff-line neutral'>(no changes)</span>")
        return "\n".join(lines_html)

    @staticmethod
    def _short_change_id(change_id: Optional[str]) -> str:
        if not change_id:
            return "N/A"
        return change_id[:8].upper()

    def clear_chat(self) -> None:
        """Clear all content from the chat display."""
        self.setText("")
        self._styles_injected = False

    def load_conversation_history(self, messages: List[Dict[str, Any]], limit: int = 100) -> None:
        """
        Load and display a conversation's message history.

        Args:
            messages: List of message dicts with 'role', 'content', and optional 'metadata'
            limit: Maximum number of recent messages to display (default 100)
        """
        # Clear existing content
        self.setText("")
        self._styles_injected = False

        # Limit to most recent messages if needed
        total_messages = len(messages)
        if total_messages > limit:
            display_messages = messages[-limit:]
            # Show a notice about hidden messages
            self.display_system_message(
                "INFO",
                f"Showing last {limit} messages (older {total_messages - limit} messages hidden)"
            )
        else:
            display_messages = messages

        # Render each message without auto-scrolling
        for msg in display_messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            metadata = msg.get("metadata", {})

            # Handle images from metadata
            image_ref = None
            if isinstance(metadata, dict):
                image_ref = metadata.get("image")

            # Render based on role
            if role == "user":
                self.display_user_message(content, image_ref)
            elif role == "assistant":
                self.display_aura_response(content)
            elif role == "system":
                # System messages are typically internal, skip or show as info
                if content:
                    self.display_system_message("SYSTEM", content)

        # Scroll to bottom after all messages loaded
        self.moveCursor(QTextCursor.End)
        self.ensureCursorVisible()
