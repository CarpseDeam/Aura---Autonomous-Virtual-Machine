import logging
from typing import Optional

from PySide6.QtCore import QThreadPool

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.models.project_context import ProjectContext
from src.aura.agent import AuraAgent
from src.aura.brain import AuraBrain
from src.aura.executor import AuraExecutor
from src.aura.services.ast_service import ASTService
from src.aura.services.conversation_management_service import ConversationManagementService
from src.aura.services.workspace_service import WorkspaceService
from src.aura.worker import BrainExecutorWorker


logger = logging.getLogger(__name__)


class AuraInterface:
    """Interface layer: the clean boundary for UI and events.

    Responsibilities:
    - Receive user input and build a ProjectContext snapshot.
    - Ask the Brain for a decision (Action).
    - Pass the Action to the Executor and let it stream/update via events.
    - Keep flow linear: Input → Brain → Executor → UI.
    """

    def __init__(
        self,
        event_bus: EventBus,
        brain: AuraBrain,
        executor: AuraExecutor,
        ast: ASTService,
        conversations: ConversationManagementService,
        workspace: WorkspaceService,
        thread_pool: QThreadPool,
        context_manager: Optional["ContextManager"] = None,
        iteration_controller: Optional["IterationController"] = None,
    ) -> None:
        self.event_bus = event_bus
        self.brain = brain
        self.executor = executor
        self.ast = ast
        self.conversations = conversations
        self.workspace = workspace
        self.thread_pool = thread_pool
        self.context_manager = context_manager
        self.iteration_controller = iteration_controller

        # Initialize AuraAgent with the new systems
        self.agent = AuraAgent(
            brain=brain,
            executor=executor,
            context_manager=context_manager,
            iteration_controller=iteration_controller
        )

        # Log whether systems are enabled
        logger.info(f"AuraInterface initialized with ContextManager: {'enabled' if context_manager else 'disabled'}")
        logger.info(f"AuraInterface initialized with IterationController: {'enabled' if iteration_controller else 'disabled'}")

        # Default language until detection signals otherwise
        self.current_language: str = "python"

        self._register_event_handlers()

    def _register_event_handlers(self) -> None:
        self.event_bus.subscribe("SEND_USER_MESSAGE", self._handle_user_message)
        # Passive listener for detected language updates
        self.event_bus.subscribe("PROJECT_LANGUAGE_DETECTED", self._handle_project_language_detected)

        # Subscribe to Context Manager events
        if self.context_manager:
            self.event_bus.subscribe("CONTEXT_LOADING_STARTED", self._handle_context_loading_started)
            self.event_bus.subscribe("CONTEXT_LOADING_COMPLETED", self._handle_context_loading_completed)

        # Subscribe to Iteration Controller events
        if self.iteration_controller:
            self.event_bus.subscribe("ITERATION_INITIALIZED", self._handle_iteration_initialized)
            self.event_bus.subscribe("ITERATION_PROGRESS", self._handle_iteration_progress)
            self.event_bus.subscribe("ITERATION_STOPPED", self._handle_iteration_stopped)

    def _handle_project_language_detected(self, event: Event) -> None:
        """Update current language from WorkspaceService detection events."""
        try:
            lang = (event.payload or {}).get("language")
            if isinstance(lang, str) and lang:
                self.current_language = lang
                logger.info(f"Interface updated language -> {self.current_language}")
        except Exception:
            logger.debug("Failed to process PROJECT_LANGUAGE_DETECTED event; ignoring.")

    def _handle_context_loading_started(self, event: Event) -> None:
        """Handle context loading started event."""
        try:
            payload = event.payload or {}
            mode = payload.get("mode", "unknown")
            request = payload.get("request", "")
            logger.info(f"Context loading started in {mode} mode for request: {request[:50]}...")
        except Exception as e:
            logger.debug(f"Failed to process CONTEXT_LOADING_STARTED event: {e}")

    def _handle_context_loading_completed(self, event: Event) -> None:
        """Handle context loading completed event."""
        try:
            payload = event.payload or {}
            loaded_files_count = payload.get("loaded_files_count", 0)
            total_tokens = payload.get("total_tokens", 0)
            truncated = payload.get("truncated", False)
            logger.info(
                f"Context loaded: {loaded_files_count} files, {total_tokens} tokens"
                f"{' (truncated)' if truncated else ''}"
            )
        except Exception as e:
            logger.debug(f"Failed to process CONTEXT_LOADING_COMPLETED event: {e}")

    def _handle_iteration_initialized(self, event: Event) -> None:
        """Handle iteration initialized event."""
        try:
            payload = event.payload or {}
            mode = payload.get("mode", "unknown")
            max_iterations = payload.get("max_iterations", 0)
            logger.info(f"Iteration initialized in {mode} mode with max {max_iterations} iterations")
        except Exception as e:
            logger.debug(f"Failed to process ITERATION_INITIALIZED event: {e}")

    def _handle_iteration_progress(self, event: Event) -> None:
        """Handle iteration progress event."""
        try:
            payload = event.payload or {}
            current = payload.get("current_iteration", 0)
            max_iter = payload.get("max_iterations", 0)
            action_type = payload.get("action_type", "unknown")
            logger.info(f"Iteration progress: {current}/{max_iter} - Action: {action_type}")
        except Exception as e:
            logger.debug(f"Failed to process ITERATION_PROGRESS event: {e}")

    def _handle_iteration_stopped(self, event: Event) -> None:
        """Handle iteration stopped event."""
        try:
            payload = event.payload or {}
            reason = payload.get("reason", "unknown")
            completed = payload.get("completed_iterations", 0)
            logger.info(f"Iteration stopped after {completed} iterations. Reason: {reason}")
        except Exception as e:
            logger.debug(f"Failed to process ITERATION_STOPPED event: {e}")

    def _build_context(self) -> ProjectContext:
        active_project = self.workspace.active_project
        active_files = list(self.ast.project_index.keys()) if getattr(self.ast, "project_index", None) else []
        conversation_history = self.conversations.get_history()
        return ProjectContext(
            active_project=active_project,
            active_files=active_files,
            conversation_history=conversation_history,
        )

    def _handle_user_message(self, event: Event) -> None:
        payload = event.payload or {}
        user_text_raw = payload.get("text") or ""
        image_attachment = payload.get("image")
        user_text = user_text_raw.strip()

        if not user_text and not image_attachment:
            self.event_bus.dispatch(Event(event_type="MODEL_ERROR", payload={"message": "Empty user request received."}))
            return

        if image_attachment and not self.executor.llm.provider_supports_vision("lead_companion_agent"):
            self.event_bus.dispatch(Event(
                event_type="MODEL_ERROR",
                payload={
                    "message": "Image attachments are only supported when a Gemini model is selected. "
                               "Please switch to a Gemini model in Settings."
                }
            ))
            return

        # Run heavy logic on a background thread
        worker = BrainExecutorWorker(self, user_text, image_attachment=image_attachment)
        worker.signals.error.connect(lambda msg: self.event_bus.dispatch(Event(event_type="MODEL_ERROR", payload={"message": msg})))
        # finished signal available for potential UI hooks; no-op here
        worker.signals.finished.connect(lambda: None)
        self.thread_pool.start(worker)

