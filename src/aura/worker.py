import logging

from PySide6.QtCore import QObject, Signal, QRunnable

from src.aura.models.events import Event


logger = logging.getLogger(__name__)


class WorkerSignals(QObject):
    """Signals used by BrainExecutorWorker to communicate status back to UI/main thread."""

    error = Signal(str)
    finished = Signal()


class BrainExecutorWorker(QRunnable):
    """
    QRunnable that runs the Companion logic off the main UI thread.

    It handles user message processing (context building, agent invocation)
    and emits signals on error and completion.
    """

    def __init__(self, interface, user_text: str):
        super().__init__()
        self.interface = interface
        self.user_text = user_text
        self.signals = WorkerSignals()

    def run(self):
        try:
            try:
                self.interface.conversations.add_message("user", self.user_text)
            except Exception:
                logger.debug("Failed to append to conversation history; continuing.")

            try:
                ctx = self.interface._build_context()
                self.interface.agent.invoke(self.user_text, ctx)
            except Exception as e:
                logger.error(f"Failed to process user message: {e}", exc_info=True)
                self.interface.event_bus.dispatch(
                    Event(event_type="MODEL_ERROR", payload={"message": "Internal error during request handling."})
                )
                return
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
