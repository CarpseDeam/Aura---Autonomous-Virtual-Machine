from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget


logger = logging.getLogger(__name__)


class PermissiveWebEnginePage(QWebEnginePage):
    """QWebEnginePage that permits the embedded terminal to open WebSocket connections."""

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        settings = self.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.LocalStorageEnabled, True)


class TerminalWidget(QWidget):
    """
    Embedded web-based terminal powered by xterm.js.

    This widget loads the HTML transport layer and exposes helpers for sending
    commands or retrieving buffered output through Qt's JavaScript bridge.
    """

    output_ready = Signal(str)

    def __init__(
        self,
        *,
        html_path: Optional[Path] = None,
        ws_host: str = "127.0.0.1",
        ws_port: int = 8765,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._ws_host = ws_host
        self._ws_port = ws_port
        self._html_path = html_path or self._default_html_path()
        self._web_view = QWebEngineView(self)
        custom_page = PermissiveWebEnginePage(self._web_view)
        self._web_view.setPage(custom_page)
        self._page_ready = False
        self._page_loaded = False
        self._pending_scripts: List[Tuple[str, Optional[Callable[[object], None]]]] = []
        self._web_view.loadFinished.connect(self._on_load_finished)
        self._init_layout()

    def _init_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._web_view)

    def _default_html_path(self) -> Path:
        default_path = Path(__file__).resolve().parent.parent / "terminal" / "terminal.html"
        if not default_path.exists():
            logger.error("terminal.html not found at %s", default_path)
            raise FileNotFoundError(f"terminal.html not found at {default_path}")
        return default_path

    def _load_terminal_page(self) -> None:
        if self._page_loaded:
            logger.debug("Terminal page already loaded, skipping")
            return
        self._page_ready = False
        self._pending_scripts.clear()
        url = QUrl.fromLocalFile(str(self._html_path))
        query_items = [f"host={self._ws_host}", f"port={self._ws_port}"]
        url.setQuery("&".join(query_items))
        logger.debug("Loading terminal HTML: %s", url.toString())
        self._web_view.load(url)
        self._page_loaded = True

    def set_connection_target(self, *, host: str, port: int) -> None:
        """
        Update the websocket target and reload the terminal page.

        Args:
            host: Hostname or IP for the websocket server.
            port: Listening port for the websocket server.
        """
        reload_needed = host != self._ws_host or port != self._ws_port
        self._ws_host = host
        self._ws_port = port
        if reload_needed:
            self._page_loaded = False
            self._load_terminal_page()

    def focus_terminal(self) -> None:
        """Give focus to the embedded terminal."""
        self._run_js("window.AuraTerminal && window.AuraTerminal.focus();")

    def clear_terminal(self) -> None:
        """Clear the terminal display buffer."""
        self._run_js("window.AuraTerminal && window.AuraTerminal.clearTerminal();")

    def send_command(self, command: str) -> None:
        """
        Send a command to the terminal session.

        Args:
            command: Command string to send. A trailing carriage return is appended if missing.
        """
        payload = json.dumps(command)
        script = f"if (window.AuraTerminal) {{ window.AuraTerminal.sendCommand({payload}); }}"
        self._run_js(script)

    def send_input(self, data: str) -> None:
        """
        Send raw input to the terminal without altering the payload.

        Args:
            data: Raw characters to send to the PTY.
        """
        payload = json.dumps(data)
        script = f"if (window.AuraTerminal) {{ window.AuraTerminal.sendInput({payload}); }}"
        self._run_js(script)

    def request_captured_output(self) -> None:
        """
        Retrieve buffered output from the terminal page and emit output_ready.
        """
        script = "window.AuraTerminal ? window.AuraTerminal.getCapturedOutput() : '';"
        self._run_js(script, self._emit_output)

    def clear_captured_output(self) -> None:
        """Reset the output buffer in the embedded terminal."""
        self._run_js("window.AuraTerminal && window.AuraTerminal.clearCapturedOutput();")

    def _run_js(self, script: str, callback: Optional[Callable[[object], None]] = None) -> None:
        page = self._web_view.page()
        if not page:
            logger.debug("Terminal web page not ready for JavaScript execution.")
            return
        if not self._page_ready:
            self._pending_scripts.append((script, callback))
            return
        if callback is not None:
            page.runJavaScript(script, callback)
        else:
            page.runJavaScript(script)

    def _emit_output(self, result: object) -> None:
        if isinstance(result, str):
            self.output_ready.emit(result)

    def _on_load_finished(self, ok: bool) -> None:
        self._page_ready = ok
        if not ok:
            logger.error("Terminal widget failed to load xterm HTML at %s", self._html_path)
            self._pending_scripts.clear()
            return
        if not self._pending_scripts:
            return
        pending = list(self._pending_scripts)
        self._pending_scripts.clear()
        for script, callback in pending:
            self._run_js(script, callback)
