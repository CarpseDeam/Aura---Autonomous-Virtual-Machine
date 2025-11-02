from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from src.aura.app.event_bus import EventBus
from src.aura.models.action import Action, ActionType
from src.aura.models.agent_task import AgentSpecification, TerminalSession
from src.aura.models.project_context import ProjectContext
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.services.file_registry import FileRegistry
from src.aura.services.llm_service import LLMService
from src.aura.services.terminal_agent_service import TerminalAgentService
from src.aura.services.terminal_session_manager import TerminalSessionManager
from src.aura.services.workspace_monitor import WorkspaceChangeMonitor, WorkspaceChanges
from src.aura.services.workspace_service import WorkspaceService

from .blueprint_handler import BlueprintHandler
from .code_sanitizer import CodeSanitizer
from .conversation_handler import ConversationHandler
from .file_operations import FileOperations
from .project_resolver import ProjectResolver
from .prompt_builder import PromptBuilder


logger = logging.getLogger(__name__)


Handler = Callable[[Action, ProjectContext], Any]


class AuraExecutor:
    """
    Execution layer responsible for orchestrating terminal-based agent work.
    """

    def __init__(
        self,
        event_bus: EventBus,
        llm: LLMService,
        prompts: PromptManager,
        workspace: WorkspaceService,
        file_registry: FileRegistry,
        terminal_service: TerminalAgentService,
        workspace_monitor: WorkspaceChangeMonitor,
        terminal_session_manager: TerminalSessionManager,
    ) -> None:
        self.event_bus = event_bus
        self.workspace = workspace
        self.file_registry = file_registry
        self.terminal_service = terminal_service
        self.workspace_monitor = workspace_monitor
        self.terminal_session_manager = terminal_session_manager

        self.code_sanitizer = CodeSanitizer()
        self.prompt_builder = PromptBuilder(prompts)
        self.project_resolver = ProjectResolver(workspace)
        self.file_operations = FileOperations(workspace)
        self.conversation_handler = ConversationHandler(llm, prompts, self.code_sanitizer)
        self.blueprint_handler = BlueprintHandler(
            event_bus=event_bus,
            llm=llm,
            prompts=prompts,
            project_resolver=self.project_resolver,
            prompt_builder=self.prompt_builder,
            code_sanitizer=self.code_sanitizer,
        )

        self._tools: Dict[ActionType, Handler] = {
            ActionType.DESIGN_BLUEPRINT: self._handle_design_blueprint,
            ActionType.REFINE_CODE: self._handle_refine_code,
            ActionType.SPAWN_AGENT: self._handle_spawn_agent,
            ActionType.MONITOR_WORKSPACE: self._handle_monitor_workspace,
            ActionType.INTEGRATE_RESULTS: self._handle_integrate_results,
            ActionType.DISCUSS: self.conversation_handler.execute_discuss,
            ActionType.SIMPLE_REPLY: self.conversation_handler.execute_simple_reply,
            ActionType.RESEARCH: self.conversation_handler.execute_research,
            ActionType.LIST_FILES: self.file_operations.execute_list_files,
            ActionType.READ_FILE: self.file_operations.execute_read_file,
        }

    def execute(self, action: Action, project_context: ProjectContext) -> Any:
        """Execute a single action using the registered handler."""
        tool = self._tools.get(action.type)
        if not tool:
            logger.warning("Unsupported action type requested: %s", action.type)
            raise RuntimeError(f"Unsupported action type: {action.type}")
        return tool(action, project_context)

    def execute_blueprint(self, user_request: str, context: ProjectContext) -> Dict[str, Any]:
        """
        Public wrapper that coordinates blueprint generation for external callers.
        """
        logger.info("Starting blueprint workflow for request: %s", user_request)
        action = Action(
            type=ActionType.DESIGN_BLUEPRINT,
            params={
                "request": user_request,
                "auto_spawn": False,
            },
        )

        try:
            result = self.execute(action, context)
        except Exception as exc:
            logger.exception("Blueprint workflow failed for request '%s'", user_request)
            raise RuntimeError(f"Failed to execute blueprint for request: {user_request}") from exc

        if not isinstance(result, AgentSpecification):
            logger.error(
                "Blueprint workflow returned unexpected result type: %s",
                type(result).__name__,
            )
            raise TypeError("Blueprint execution did not produce an AgentSpecification")

        specification = result
        planned_files: List[str] = []
        blueprint_payload = specification.blueprint if isinstance(specification.blueprint, dict) else {}
        files_payload = blueprint_payload.get("files")
        if isinstance(files_payload, list):
            for file_item in files_payload:
                if isinstance(file_item, dict):
                    file_path = file_item.get("file_path")
                    if isinstance(file_path, str):
                        planned_files.append(file_path)

        context.extras["latest_specification"] = specification.model_dump()

        logger.info(
            "Completed blueprint workflow for request '%s' with %d planned file(s)",
            user_request,
            len(planned_files),
        )

        return {
            "specification": specification,
            "planned_files": planned_files,
        }

    # -- Design & specification generation -------------------------------------------------

    def _handle_design_blueprint(self, action: Action, context: ProjectContext) -> AgentSpecification:
        spec = self.blueprint_handler.execute_design_blueprint(action, context)
        self.file_registry.refresh()
        context.active_files = self.file_registry.list_files()

        # Auto-spawn terminal agent if enabled
        auto_spawn = action.get_param("auto_spawn", True)  # Default to True
        if auto_spawn and spec:
            logger.info("Auto-spawning terminal agent for task %s", spec.task_id)
            try:
                session = self.terminal_service.spawn_agent(spec)
                self.terminal_session_manager.register_session(session)
                context.extras["last_terminal_session"] = session.model_dump()
                logger.info("Auto-spawned terminal session %s", session.task_id)
            except Exception as exc:
                logger.error("Failed to auto-spawn terminal agent: %s", exc)
                # Don't fail the whole operation, just log the error

        return spec

    def _handle_refine_code(self, action: Action, context: ProjectContext) -> AgentSpecification:
        request = action.get_param("request", "")
        target_files = action.get_param("files") or []
        notes = action.get_param("notes") or []
        spec = self.blueprint_handler.build_manual_specification(
            request=request,
            ctx=context,
            target_files=target_files if isinstance(target_files, list) else [target_files],
            notes=notes if isinstance(notes, list) else [notes],
        )
        self.file_registry.refresh()
        context.active_files = self.file_registry.list_files()
        return spec

    # -- Terminal agent orchestration ------------------------------------------------------

    def _handle_spawn_agent(self, action: Action, context: ProjectContext) -> TerminalSession:
        spec_payload = action.get_param("specification")
        command_override = action.get_param("command")

        specification: Optional[AgentSpecification] = None
        if isinstance(spec_payload, AgentSpecification):
            specification = spec_payload
        elif isinstance(spec_payload, dict):
            specification = AgentSpecification(**spec_payload)
        elif isinstance(spec_payload, str) and spec_payload.lower() == "latest":
            extras_payload = (context.extras or {}).get("latest_specification")
            if isinstance(extras_payload, AgentSpecification):
                specification = extras_payload
            elif isinstance(extras_payload, dict):
                specification = AgentSpecification(**extras_payload)
        else:
            extras_payload = (context.extras or {}).get("latest_specification")
            if isinstance(extras_payload, AgentSpecification):
                specification = extras_payload
            elif isinstance(extras_payload, dict):
                specification = AgentSpecification(**extras_payload)

        if specification is None:
            raise ValueError("SPAWN_AGENT requires an AgentSpecification payload or a cached latest specification")

        command = command_override if isinstance(command_override, list) else None
        session = self.terminal_service.spawn_agent(
            specification,
            command_override=command,
        )

        # Register the session for monitoring
        self.terminal_session_manager.register_session(session)
        logger.info("Registered terminal session %s for monitoring", session.task_id)

        context.extras["last_terminal_session"] = session.model_dump()
        return session

    def _handle_monitor_workspace(self, action: Action, context: ProjectContext) -> WorkspaceChanges:
        changes = self.workspace_monitor.snapshot()
        if changes.has_changes():
            self.file_registry.refresh()
            context.active_files = self.file_registry.list_files()
        return changes

    def _handle_integrate_results(self, action: Action, context: ProjectContext) -> Dict[str, Any]:
        """
        Read the latest workspace changes and update project context memory.
        """
        changes = self.workspace_monitor.snapshot()
        if not changes.has_changes():
            return {"integrated": False, "message": "No workspace changes detected."}

        project_path = getattr(self.workspace, "active_project_path", None)
        if not project_path:
            raise RuntimeError("No active project set for integration.")

        updated_files: Dict[str, str] = {}
        for rel_path in changes.created + changes.modified:
            file_path = project_path / rel_path
            if file_path.exists():
                try:
                    updated_files[rel_path] = file_path.read_text(encoding="utf-8")
                except Exception as exc:
                    logger.warning("Failed to read updated file %s: %s", file_path, exc)

        result = {
            "integrated": True,
            "updated_files": updated_files,
            "deleted_files": changes.deleted,
        }
        if changes.has_changes():
            self.file_registry.refresh()
            context.active_files = self.file_registry.list_files()
        return result
