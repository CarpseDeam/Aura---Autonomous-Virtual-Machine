import logging
from pathlib import Path
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
            image_payload = None
            try:
                image_payload = self._normalize_image_attachment(self.image_attachment)
                images = [image_payload] if image_payload else None
                self.interface.conversations.add_message("user", self.user_text, images=images)
            except Exception:
                logger.debug("Failed to append to conversation history; continuing.")

            try:
                ctx = self.interface._build_context()
                baseline_length = len(ctx.conversation_history or [])
                if image_payload:
                    ctx_extras = dict(ctx.extras or {})
                    ctx_extras["latest_user_images"] = [image_payload]
                    ctx.extras = ctx_extras
                final_state = self.interface.agent.invoke(self.user_text, ctx)
                try:
                    final_messages = list((final_state or {}).get("messages") or [])
                    new_messages = final_messages[baseline_length:]
                    if new_messages and isinstance(new_messages[0], dict) and new_messages[0].get("role") == "user":
                        new_messages = new_messages[1:]
                    if new_messages:
                        self.interface.conversations.add_messages(new_messages)
                except Exception:
                    logger.debug("Failed to append agent messages to persistent history.", exc_info=True)
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

    @staticmethod
    def _normalize_image_attachment(image: Optional[Any]) -> Optional[Any]:
        if image is None:
            return None
        if isinstance(image, str):
            return {"path": image}
        if isinstance(image, Path):
            return {"path": image.as_posix()}
        if isinstance(image, dict):
            if "path" in image or "relative_path" in image or "data" in image:
                return image
        return image
