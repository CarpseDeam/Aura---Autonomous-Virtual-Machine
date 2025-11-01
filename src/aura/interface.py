import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QThreadPool

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.models.action import Action, ActionType
from src.aura.models.project_context import ProjectContext
from src.aura.models.project import Project, ProjectSummary
from src.aura.agent import AuraAgent
from src.aura.brain import AuraBrain
from src.aura.executor import AuraExecutor
from src.aura.services.conversation_management_service import ConversationManagementService
from src.aura.services.workspace_service import WorkspaceService
from src.aura.project.project_manager import ProjectManager
from src.aura.worker import BrainExecutorWorker


logger = logging.getLogger(__name__)

ADVICE_TRIGGER_PHRASES = [
    "what do you think",
    "what would you",
    "what should",
    "should i",
    "not sure",
    "not totally sure",
    "not certain",
    "uncertain",
    "recommend",
    "recommendation",
    "your opinion",
    "your thoughts",
    "advice on",
    "thoughts on",
    "which is better",
    " vs ",
    " versus ",
    "evaluate",
    "evaluating",
    "considering",
    "or should",
]


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
        conversations: ConversationManagementService,
        workspace: WorkspaceService,
        thread_pool: QThreadPool,
        project_manager: Optional[ProjectManager] = None,
        context_manager: Optional["ContextManager"] = None,
        iteration_controller: Optional["IterationController"] = None,
    ) -> None:
        self.event_bus = event_bus
        self.brain = brain
        self.executor = executor
        self.conversations = conversations
        self.workspace = workspace
        self.thread_pool = thread_pool
        self.project_manager = project_manager
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
        logger.info(f"AuraInterface initialized with ProjectManager: {'enabled' if project_manager else 'disabled'}")

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

    def _collect_active_files(self) -> List[str]:
        """Collect currently indexed project files from the workspace service."""
        try:
            return self.workspace.get_project_files()
        except Exception as exc:
            logger.debug("Failed to collect active files from workspace: %s", exc)
            return []

    def _build_context(self) -> ProjectContext:
        if self.project_manager and self.project_manager.current_project:
            project = self.project_manager.current_project
            extras: Dict[str, Any] = dict(project.metadata or {})
            extras["workspace_root"] = project.root_path
            extras["current_language"] = self.current_language
            return ProjectContext(
                active_project=project.name,
                active_files=list(project.active_files) if project.active_files else self._collect_active_files(),
                conversation_history=list(project.conversation_history),
                extras=extras,
            )

        conversation_history = self.conversations.get_history()
        extras = {"current_language": self.current_language}
        return ProjectContext(
            active_project=self.workspace.active_project,
            active_files=self._collect_active_files(),
            conversation_history=conversation_history,
            extras=extras,
        )

    def _handle_user_message(self, event: Event) -> None:
        payload = event.payload or {}
        user_text_raw = payload.get("text") or ""
        image_attachment = payload.get("image")
        user_text = user_text_raw.strip()

        if not user_text and not image_attachment:
            self.event_bus.dispatch(Event(event_type="MODEL_ERROR", payload={"message": "Empty user request received."}))
            return

        if user_text:
            user_text_lower = user_text.lower()
            if any(phrase in user_text_lower for phrase in ADVICE_TRIGGER_PHRASES):
                self._handle_advice_request(user_text, image_attachment)
                return

        if user_text.startswith("/project"):
            logger.info("Processing project command: %s", user_text)
            response_text = self._handle_project_command(user_text)
            try:
                self.conversations.add_message("user", user_text)
            except Exception:
                logger.debug("Failed to record project command user message.", exc_info=True)
            try:
                self.conversations.add_message(
                    "assistant",
                    response_text,
                    metadata={"action_type": "project_management"}
                )
            except Exception:
                logger.debug("Failed to record project command response.", exc_info=True)
            self._persist_project_conversation_turn(
                user_text,
                [{"role": "assistant", "content": response_text, "metadata": {"action_type": "project_management"}}],
            )
            self.event_bus.dispatch(Event(event_type="MODEL_CHUNK_RECEIVED", payload={"chunk": response_text}))
            self.event_bus.dispatch(Event(event_type="MODEL_STREAM_ENDED", payload={}))
            return

        # Check for natural language project creation commands
        natural_project_response = self._handle_natural_project_command(user_text)
        if natural_project_response:
            logger.info("Processing natural language project command: %s", user_text)
            try:
                self.conversations.add_message("user", user_text)
            except Exception:
                logger.debug("Failed to record natural project command user message.", exc_info=True)
            try:
                self.conversations.add_message(
                    "assistant",
                    natural_project_response,
                    metadata={"action_type": "project_management"}
                )
            except Exception:
                logger.debug("Failed to record natural project command response.", exc_info=True)
            self._persist_project_conversation_turn(
                user_text,
                [{"role": "assistant", "content": natural_project_response, "metadata": {"action_type": "project_management"}}],
            )
            self.event_bus.dispatch(Event(event_type="MODEL_CHUNK_RECEIVED", payload={"chunk": natural_project_response}))
            self.event_bus.dispatch(Event(event_type="MODEL_STREAM_ENDED", payload={}))
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

    def _handle_advice_request(self, user_text: str, image_attachment: Optional[Any]) -> None:
        logger.info("Advice-seeking phrase detected; routing directly to companion agent.")

        image_payload = None
        try:
            image_payload = BrainExecutorWorker._normalize_image_attachment(image_attachment)
            images = [image_payload] if image_payload else None
            self.conversations.add_message("user", user_text, images=images)
        except Exception:
            logger.debug("Failed to record advice-seeking user message.", exc_info=True)

        try:
            context = self._build_context()
        except Exception as exc:
            logger.error("Failed to build context for advice reply: %s", exc, exc_info=True)
            self.event_bus.dispatch(Event(
                event_type="MODEL_ERROR",
                payload={"message": "I'm having trouble responding right now. Please try again."},
            ))
            return

        if image_payload:
            extras = dict(context.extras or {})
            extras["latest_user_images"] = [image_payload]
            context.extras = extras

        history = list(context.conversation_history or [])
        user_entry: Dict[str, Any] = {"role": "user", "content": user_text}
        if image_payload:
            user_entry["images"] = [image_payload]

        if history:
            last_entry_raw = history[-1] or {}
            last_entry = dict(last_entry_raw) if isinstance(last_entry_raw, dict) else {}
            last_role = last_entry.get("role")
            last_content = (last_entry.get("content") or "").strip()
            if last_role == "user" and last_content == user_text:
                if image_payload:
                    last_entry["images"] = [image_payload]
                    history[-1] = last_entry
            else:
                history.append(user_entry)
        else:
            history.append(user_entry)
        context.conversation_history = history

        action = Action(
            type=ActionType.SIMPLE_REPLY,
            params={"request": user_text},
        )

        try:
            reply_text = self.executor.execute_simple_reply(action, context)
        except Exception as exc:
            logger.error("Companion agent failed to provide advice reply: %s", exc, exc_info=True)
            self.event_bus.dispatch(Event(
                event_type="MODEL_ERROR",
                payload={"message": "I'm having trouble sharing advice right now. Please try again."},
            ))
            return

        assistant_message = {
            "role": "assistant",
            "content": reply_text,
            "metadata": {"action_type": ActionType.SIMPLE_REPLY.value},
        }

        try:
            self.conversations.add_message(
                "assistant",
                reply_text,
                metadata={"action_type": ActionType.SIMPLE_REPLY.value},
            )
        except Exception:
            logger.debug("Failed to record advice companion reply.", exc_info=True)

        self._persist_project_conversation_turn(user_text, [assistant_message])

        try:
            self.event_bus.dispatch(Event(event_type="MODEL_CHUNK_RECEIVED", payload={"chunk": reply_text}))
            self.event_bus.dispatch(Event(event_type="MODEL_STREAM_ENDED", payload={}))
        except Exception:
            logger.debug("Failed to dispatch advice reply events.", exc_info=True)

    def _handle_project_command(self, command: str) -> str:
        """Handle /project commands."""
        if not self.project_manager:
            logger.warning("Project command received but ProjectManager is not configured.")
            return "Project management is currently unavailable."

        parts = command.split()
        if len(parts) < 2:
            return "Usage: /project [list|switch <name>|create <name>|new <name>|info]"

        action = parts[1].lower()
        if action == "list":
            projects = self.project_manager.list_projects()
            return self._format_project_list(projects)

        if action in ("create", "new"):
            if len(parts) < 3:
                return f"Usage: /project {action} <name>"
            project_name = parts[2]
            try:
                project = self.project_manager.create_and_switch_project(project_name)
            except ValueError as exc:
                return f"Failed to create project: {exc}"
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to create project '%s': %s", project_name, exc, exc_info=True)
                return f"Failed to create project '{project_name}': {exc}"

            try:
                self.workspace.set_active_project(project.name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Workspace activation failed for project '%s': %s", project.name, exc, exc_info=True)

            return f"Created new project '{project.name}'!\n\nYou're all set up and ready to go. What would you like to build?"

        if action == "switch":
            if len(parts) < 3:
                return "Usage: /project switch <name>"
            target = parts[2]
            try:
                project = self.project_manager.switch_project(target)
            except FileNotFoundError:
                return f"Project '{target}' not found."
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to switch to project '%s': %s", target, exc, exc_info=True)
                return f"Failed to switch to project '{target}': {exc}"

            try:
                self.workspace.set_active_project(project.name)
            except Exception as exc:  # noqa: BLE001
                logger.error("Workspace activation failed for project '%s': %s", project.name, exc, exc_info=True)
                return f"Switched to project '{project.name}', but activating the workspace failed: {exc}"

            return f"Switched to project: {project.name}"

        if action == "info":
            project = self.project_manager.current_project
            if not project:
                return "No project is currently active."
            return self._format_project_info(project)

        return "Unsupported project command. Use '/project list', '/project switch <name>', '/project create <name>', or '/project info'."

    def _handle_natural_project_command(self, user_text: str) -> Optional[str]:
        """
        Handle natural language project creation commands.

        Detects patterns like:
        - "create a new project called fastapi-backend"
        - "start a fresh project for my saas app"
        - "make me a new project named website-redesign"

        Args:
            user_text: The raw user message

        Returns:
            Response message if a project command was detected and handled, None otherwise
        """
        if not self.project_manager:
            return None

        # Pattern to detect natural language project creation
        patterns = [
            r"(?:create|make|start)\s+(?:a\s+)?(?:new\s+)?project\s+(?:called|named)\s+(.+)",
            r"(?:create|make|start)\s+(?:a\s+)?(?:new\s+)?project\s+for\s+(?:my\s+)?(.+)",
            r"(?:new|fresh)\s+project\s+(?:called|named|for)\s+(.+)",
        ]

        project_name = None
        for pattern in patterns:
            match = re.search(pattern, user_text, re.IGNORECASE)
            if match:
                project_name = match.group(1).strip()
                break

        if not project_name:
            return None

        # Sanitize the project name to be filesystem-safe
        sanitized_name = self._sanitize_project_name(project_name)

        if not sanitized_name:
            return "I couldn't extract a valid project name. Please provide a name with alphanumeric characters."

        try:
            project = self.project_manager.create_and_switch_project(sanitized_name)

            # Activate workspace
            try:
                self.workspace.set_active_project(project.name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Workspace activation failed for project '%s': %s", project.name, exc, exc_info=True)

            return f"Created new project '{project.name}'!\n\nYou're all set up and ready to go. What would you like to build?"

        except ValueError as exc:
            # Project might already exist
            if "already exists" in str(exc).lower():
                return f"Project '{sanitized_name}' already exists. Would you like to switch to it? Try: /project switch {sanitized_name}"
            return f"Failed to create project: {exc}"
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to create project '%s': %s", sanitized_name, exc, exc_info=True)
            return f"An error occurred while creating the project: {exc}"

    def _sanitize_project_name(self, name: str) -> str:
        """
        Sanitize a project name to be filesystem-safe.

        Args:
            name: Raw project name from user input

        Returns:
            Sanitized project name with only safe characters
        """
        # Remove invalid filesystem characters
        sanitized = re.sub(r'[<>:"/\\|?*]', '', name)

        # Replace spaces and underscores with hyphens
        sanitized = re.sub(r'[\s_]+', '-', sanitized)

        # Remove leading/trailing hyphens
        sanitized = sanitized.strip('-')

        # Convert to lowercase for consistency
        sanitized = sanitized.lower()

        # Keep only alphanumeric and hyphens
        sanitized = re.sub(r'[^a-z0-9-]', '', sanitized)

        return sanitized

    def _format_project_list(self, projects: List[ProjectSummary]) -> str:
        """Format project summaries for display."""
        if not projects:
            return "No projects found."

        lines = ["Projects:"]
        for summary in projects:
            last_active = summary.last_active.isoformat()
            topics_part = ""
            if summary.recent_topics:
                topics_preview = ", ".join(summary.recent_topics[:3])
                if len(summary.recent_topics) > 3:
                    topics_preview += ", ..."
                topics_part = f" | topics: {topics_preview}"
            lines.append(
                f"- {summary.name} (last active {last_active}, messages {summary.message_count}){topics_part}"
            )
        return "\n".join(lines)

    def _format_project_info(self, project: Project) -> str:
        """Format current project details."""
        lines = [
            f"Project: {project.name}",
            f"Root: {project.root_path}",
            f"Created: {project.created_at.isoformat()}",
            f"Last Active: {project.last_active.isoformat()}",
            f"Messages: {len(project.conversation_history)}",
        ]
        if project.active_files:
            preview = ", ".join(project.active_files[:5])
            if len(project.active_files) > 5:
                preview += ", ..."
            lines.append(f"Active Files: {preview}")

        metadata = project.metadata or {}
        topics = metadata.get("recent_topics")
        if isinstance(topics, list) and topics:
            preview = ", ".join(str(topic) for topic in topics[:5])
            if len(topics) > 5:
                preview += ", ..."
            lines.append(f"Recent Topics: {preview}")

        language = metadata.get("current_language")
        if isinstance(language, str) and language:
            lines.append(f"Current Language: {language}")

        return "\n".join(lines)

    def _persist_project_conversation_turn(
        self,
        user_message: str,
        agent_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Update persistent project state after a conversation turn."""
        if not self.project_manager or not self.project_manager.current_project:
            return

        project = self.project_manager.current_project
        try:
            new_entries: List[Dict[str, Any]] = []
            timestamp = datetime.now(timezone.utc).isoformat()
            new_entries.append({"role": "user", "content": user_message, "timestamp": timestamp})

            collected_topics: List[str] = []
            for message in agent_messages or []:
                prepared = self._prepare_project_message(message)
                new_entries.append(prepared)
                metadata = message.get("metadata")
                if isinstance(metadata, dict):
                    topics = metadata.get("topics") or metadata.get("recent_topics")
                    if isinstance(topics, list):
                        collected_topics.extend(str(topic) for topic in topics if topic)

            project.conversation_history.extend(new_entries)
            max_history = 500
            if len(project.conversation_history) > max_history:
                project.conversation_history = project.conversation_history[-max_history:]
            project.active_files = self._collect_active_files()
            project.last_active = datetime.now(timezone.utc)

            metadata = dict(project.metadata or {})
            metadata["current_language"] = self.current_language
            existing_topics = metadata.get("recent_topics")
            topic_list = [str(topic) for topic in existing_topics] if isinstance(existing_topics, list) else []
            if collected_topics:
                merged = collected_topics + [topic for topic in topic_list if topic not in collected_topics]
                metadata["recent_topics"] = merged[:10]
            else:
                metadata["recent_topics"] = topic_list[:10]
            project.metadata = metadata

            self.project_manager.save_project(project)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to persist project conversation turn: %s", exc, exc_info=True)

    def _prepare_project_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize agent message payloads for project storage."""
        entry: Dict[str, Any] = {
            "role": message.get("role", "assistant"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        content = message.get("content")
        if isinstance(content, (str, list, dict)) or content is None:
            entry["content"] = content
        else:
            entry["content"] = str(content)

        metadata = message.get("metadata")
        if isinstance(metadata, dict):
            entry["metadata"] = metadata

        for key in ("action_type", "tool", "images"):
            value = message.get(key)
            if value is not None:
                entry[key] = value

        return entry
