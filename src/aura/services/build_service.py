import logging
import re
import threading
from typing import Dict, Optional

from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.services.context_retrieval_service import ContextRetrievalService
from src.aura.services.task_management_service import TaskManagementService
from src.aura.models.task import TaskStatus


logger = logging.getLogger(__name__)


class BuildService:
    """
    Owns the context-aware code generation and build phase.

    Responsibilities:
    - Listen for BLUEPRINT_APPROVED to kick off sequential build.
    - Handle DISPATCH_TASK events to generate code via the Engineer agent.
    - Leverage AST-driven ContextRetrievalService to provide pruned context.
    - Route generated code through ValidationService (via events) when specs exist.
    """

    def __init__(
        self,
        event_bus: EventBus,
        prompt_manager: PromptManager,
        llm_dispatcher,  # LLMService (low-level dispatcher)
        context_retrieval_service: ContextRetrievalService,
        task_management_service: TaskManagementService,
    ):
        self.event_bus = event_bus
        self.prompt_manager = prompt_manager
        self.llm = llm_dispatcher
        self.context_retrieval_service = context_retrieval_service
        self.task_management_service = task_management_service

        self._register_event_handlers()
        logger.info("BuildService initialized and listening for build events.")

    # ------------------- Event Wiring -------------------
    def _register_event_handlers(self):
        # Primary trigger from DesignService
        self.event_bus.subscribe("BLUEPRINT_APPROVED", self._handle_blueprint_approved)
        # Per-task dispatches from TaskManagementService
        self.event_bus.subscribe("DISPATCH_TASK", self.handle_dispatch_task)

    def _handle_blueprint_approved(self, event: Event):
        """When a blueprint is approved, kick off sequential task dispatch."""
        logger.info("BuildService: BLUEPRINT_APPROVED received; starting build sequence if idle.")
        # Avoid double-start if a task is already in progress
        any_in_progress = any(t.status == TaskStatus.IN_PROGRESS for t in self.task_management_service.tasks)
        any_pending = any(t.status == TaskStatus.PENDING for t in self.task_management_service.tasks)
        if not any_in_progress:
            if any_pending:
                self.event_bus.dispatch(Event(event_type="DISPATCH_ALL_TASKS"))
            else:
                logger.info("BuildService: No pending tasks in blueprint; not starting build.")
        else:
            logger.info("BuildService: Build already in progress; ignoring auto-start.")

    # ------------------- Core Build Handlers -------------------
    def handle_dispatch_task(self, event: Event):
        """Handles dispatched tasks by routing them to the engineer agent with AST-powered context."""
        task_id = event.payload.get("task_id")
        if not task_id:
            self._handle_error("Dispatch event received with no task_id.")
            return

        task = self.task_management_service.get_task_by_id(task_id)
        if not task:
            self._handle_error(f"Could not find task with ID {task_id}")
            return

        logger.info(f"Engineer Agent activated for task: '{task.description}'")
        file_path = self._extract_file_path(task.description)

        # AST-driven context retrieval
        context_data = self.context_retrieval_service.get_context_for_task(task.description, file_path)

        # Prepare additional context: current source and optional parent class source
        current_source = self.context_retrieval_service._read_file_content(file_path) or ""

        parent_class_name = None
        parent_class_source = None
        spec = getattr(task, 'spec', None) or {}
        if isinstance(spec, dict):
            parent_class_name = (
                spec.get('parent_class')
                or spec.get('base_class')
                or spec.get('inherits_from')
                or spec.get('extends')
            )
        # If the spec indicates inheritance, try to fetch the parent class source code
        if parent_class_name:
            try:
                ast_service = getattr(self.context_retrieval_service, 'ast_service', None)
                if ast_service:
                    parent_path = ast_service.find_class_file_path(parent_class_name)
                    if parent_path:
                        parent_class_source = self.context_retrieval_service._read_file_content(parent_path) or None
                        logger.info(f"Including parent class '{parent_class_name}' from: {parent_path}")
                    else:
                        logger.info(f"Parent class '{parent_class_name}' not found in index.")
            except Exception as e:
                logger.warning(f"Failed to retrieve parent class source for '{parent_class_name}': {e}")

        prompt = self.prompt_manager.render(
            "engineer.jinja2",
            file_path=file_path,
            user_request=task.description,
            source_code=current_source,
            spec=task.spec,
            context_files=context_data,
            parent_class_name=parent_class_name,
            parent_class_source=parent_class_source,
        )
        if not prompt:
            self._handle_error("Failed to render the engineer prompt.")
            return

        # Run the code generation in a thread to avoid blocking the UI
        thread = threading.Thread(
            target=self._generate_and_dispatch_code,
            args=(prompt, "engineer_agent", file_path, task),
        )
        thread.start()

    def _generate_and_dispatch_code(self, prompt: str, agent_name: str, file_path: str, task=None):
        """Generates code and dispatches it through the Phoenix Initiative validation pipeline."""
        provider, model_name, config = self.llm._get_provider_for_agent(agent_name)
        if not provider or not model_name:
            self._handle_error(f"Agent '{agent_name}' is not configured for code generation.")
            return

        try:
            logger.info(f"Engineer: Generating code for '{file_path}' with agent '{agent_name}' (streaming).")
            # Stream chunks from the provider and dispatch incremental updates
            full_code_parts = []
            stream = self.llm.stream_chat_for_agent(agent_name, prompt)
            for chunk in stream:
                # Dispatch each chunk so the UI can render in real-time
                try:
                    self.event_bus.dispatch(Event(
                        event_type="CODE_CHUNK_GENERATED",
                        payload={
                            "file_path": file_path,
                            "chunk": chunk or ""
                        }
                    ))
                except Exception:
                    # Don't let UI-dispatch hiccups break generation; continue accumulating
                    logger.warning("Dispatch of CODE_CHUNK_GENERATED failed; continuing stream.", exc_info=True)
                # Accumulate full text for validation/finalization
                if chunk:
                    full_code_parts.append(chunk)

            full_code = "".join(full_code_parts)

            if full_code.startswith("ERROR:"):
                self._handle_error(full_code)
                return

            sanitized_code = self._sanitize_code(full_code)

            if task and hasattr(task, 'spec') and task.spec:
                logger.info(f"Phoenix Initiative: Routing task {task.id} through Quality Gate")
                self.event_bus.dispatch(Event(
                    event_type="VALIDATE_CODE",
                    payload={
                        "spec": task.spec,
                        "generated_code": sanitized_code,
                        "task_id": task.id,
                        "file_path": file_path
                    }
                ))
            else:
                logger.info(f"Legacy: Direct dispatch for non-spec task in '{file_path}'")
                self.event_bus.dispatch(Event(
                    event_type="CODE_GENERATED",
                    payload={"file_path": file_path, "code": sanitized_code}
                ))

            logger.info(f"Successfully processed code generation for '{file_path}'.")
        except Exception as e:
            logger.error(f"Error during code generation thread for {file_path}: {e}", exc_info=True)
            self._handle_error(f"A critical error occurred while generating code for {file_path}.")

    # ------------------- Utilities -------------------
    def _extract_file_path(self, task_description: str) -> str:
        """
        Extract a likely file path from a task description using patterns and AST-driven hints.
        """
        # Look for explicit file paths in backticks
        backtick_match = re.search(r'`([^`]+\.py)`', task_description)
        if backtick_match:
            return backtick_match.group(1)

        # Look for file paths without backticks (workspace/*.py, src/*.py, etc.)
        file_path_pattern = r'\b(?:workspace|src|app|lib|modules?|components?)/[^\s]+\.py\b'
        file_match = re.search(file_path_pattern, task_description, re.IGNORECASE)
        if file_match:
            return file_match.group(0)

        # Look for bare .py files
        py_file_match = re.search(r'\b\w+\.py\b', task_description)
        if py_file_match:
            return f"workspace/{py_file_match.group(0)}"

        # Heuristic fallback on common keywords
        if re.search(r'\b(?:main|app|server|client|model|view|controller|service|util|helper)\b', task_description, re.IGNORECASE):
            main_match = re.search(r'\b(main|app|server|client|model|view|controller|service|util|helper)\b', task_description, re.IGNORECASE)
            if main_match:
                return f"workspace/{main_match.group(1).lower()}.py"

        logger.warning(f"Could not extract file path from task: '{task_description}'. Using default.")
        return "workspace/generated.py"

    def _sanitize_code(self, code: str) -> str:
        """Remove markdown fences and language identifiers from generated code."""
        code = re.sub(r'^```\w*\s*\n?', '', code, flags=re.MULTILINE)
        code = re.sub(r'\n?```\s*$', '', code, flags=re.MULTILINE)
        code = code.replace('```', '')
        return code.strip()

    def _handle_error(self, message: str):
        logger.error(message)
        self.event_bus.dispatch(Event(event_type="MODEL_ERROR", payload={"message": message}))
