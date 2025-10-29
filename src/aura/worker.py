import logging

from typing import Any, Dict, Optional

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

    def __init__(self, interface, user_text: str, image_attachment: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.interface = interface
        self.user_text = user_text
        self.image_attachment = image_attachment
        self.signals = WorkerSignals()

    def run(self):
        try:
            try:
                images = [self.image_attachment] if self.image_attachment else None
                self.interface.conversations.add_message("user", self.user_text, images=images)
            except Exception:
                logger.debug("Failed to append to conversation history; continuing.")

            try:
                ctx = self.interface._build_context()
                if self.image_attachment:
                    ctx_extras = dict(ctx.extras or {})
                    ctx_extras["latest_user_images"] = [self.image_attachment]
                    ctx.extras = ctx_extras
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
