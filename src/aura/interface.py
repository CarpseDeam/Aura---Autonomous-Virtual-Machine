import logging
from typing import Optional

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.models.project_context import ProjectContext
from src.aura.brain import AuraBrain
from src.aura.executor import AuraExecutor
from src.aura.services.ast_service import ASTService
from src.aura.services.conversation_management_service import ConversationManagementService
from src.aura.services.workspace_service import WorkspaceService


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
    ) -> None:
        self.event_bus = event_bus
        self.brain = brain
        self.executor = executor
        self.ast = ast
        self.conversations = conversations
        self.workspace = workspace

        self._register_event_handlers()

    def _register_event_handlers(self) -> None:
        self.event_bus.subscribe("SEND_USER_MESSAGE", self._handle_user_message)

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
        user_text = (event.payload or {}).get("text", "").strip()
        if not user_text:
            self.event_bus.dispatch(Event(event_type="MODEL_ERROR", payload={"message": "Empty user request received."}))
            return

        # Record conversation
        try:
            self.conversations.add_message("user", user_text)
        except Exception:
            logger.debug("Failed to append to conversation history; continuing.")

        # Build decision context and route
        ctx = self._build_context()
        action = self.brain.decide(user_text, ctx)

        # Execute and let events flow to UI; final result is not mandatory for UI
        result = self.executor.execute(action, ctx)
        if not result.ok and result.error:
            self.event_bus.dispatch(Event(event_type="MODEL_ERROR", payload={"message": result.error}))

