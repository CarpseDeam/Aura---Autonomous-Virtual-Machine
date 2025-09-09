import logging
from typing import Optional

from PySide6.QtCore import QObject, Signal, QRunnable


logger = logging.getLogger(__name__)


class WorkerSignals(QObject):
    """Signals used by BrainExecutorWorker to communicate status back to UI/main thread."""

    error = Signal(str)
    finished = Signal()


class BrainExecutorWorker(QRunnable):
    """
    QRunnable that runs the Companion logic off the main UI thread.

    It invokes AuraInterface._handle_user_message_logic(user_text) in a background thread
    and emits signals on error and completion.
    """

    def __init__(self, interface, user_text: str):
        super().__init__()
        self.interface = interface
        self.user_text = user_text
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.interface._handle_user_message_logic(self.user_text)
        except Exception as e:
            logger.error(f"Background worker error: {e}", exc_info=True)
            try:
                self.signals.error.emit(str(e))
            except Exception:
                pass
        finally:
            try:
                self.signals.finished.emit()
            except Exception:
                pass

