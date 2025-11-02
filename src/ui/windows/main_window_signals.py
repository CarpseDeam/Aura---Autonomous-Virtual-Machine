from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class MainWindowSignaller(QObject):
    """
    Cross-thread signal bridge for streaming model responses.
    """

    chunk_received = Signal(str)
    stream_ended = Signal()
    error_received = Signal(str)
