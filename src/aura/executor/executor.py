from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

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
from src.aura.services.mcp.mcp_client_service import MCPClientService

from .blueprint_handler import BlueprintHandler
from .code_sanitizer import CodeSanitizer
from .conversation_handler import ConversationHandler
from .file_operations import FileOperations
from .project_resolver import ProjectResolver
from .terminal_supervisor import TerminalSupervisor
from .prompt_builder import PromptBuilder
from .mcp_handler import MCPHandler


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
        self.terminal_supervisor = TerminalSupervisor(terminal_session_manager)

        # MCP: client + handler
        self.mcp_client = MCPClientService()
        self.mcp_handler = MCPHandler(self.mcp_client)

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
            ActionType.READ_TERMINAL_OUTPUT: self.terminal_supervisor.execute_read_terminal_output,
            ActionType.SEND_TO_TERMINAL: self.terminal_supervisor.execute_send_to_terminal,
            # MCP actions
            ActionType.MCP_START_SERVER: self._handle_mcp_start_server,
            ActionType.MCP_STOP_SERVER: self._handle_mcp_stop_server,
            ActionType.MCP_LIST_TOOLS: self._handle_mcp_list_tools,
            ActionType.MCP_CALL_TOOL: self._handle_mcp_call_tool,
            ActionType.MCP_SERVER_STATUS: self._handle_mcp_server_status,
        }

    def execute(self, action: Action, project_context: ProjectContext) -> Any:
        """Execute a single action using the registered handler."""
        tool = self._tools.get(action.type)
        if not tool:
            logger.warning("Unsupported action type requested: %s", action.type)
            raise RuntimeError(f"Unsupported action type: {action.type}")
        return tool(action, project_context)

    # -- MCP handlers ---------------------------------------------------------------

    def _handle_mcp_start_server(self, action: Action, context: ProjectContext) -> dict:
        template = action.get_param("template")
        if not template:
            raise RuntimeError("Missing 'template' parameter for MCP_START_SERVER")
        root = action.get_param("root")
        overrides = action.get_param("overrides")
        logger.info("Starting MCP server from template %s", template)
        return self.mcp_handler.start_server(template=template, root=root, overrides=overrides, project_name=context.active_project)

    def _handle_mcp_stop_server(self, action: Action, context: ProjectContext) -> dict:
        server_id = action.get_param("server_id")
        if not server_id:
            raise RuntimeError("Missing 'server_id' parameter for MCP_STOP_SERVER")
        logger.info("Stopping MCP server %s", server_id)
        return self.mcp_handler.stop_server(server_id=server_id)

    def _handle_mcp_list_tools(self, action: Action, context: ProjectContext) -> dict:
        server_id = action.get_param("server_id")
        if not server_id:
            raise RuntimeError("Missing 'server_id' parameter for MCP_LIST_TOOLS")
        logger.info("Listing tools for MCP server %s", server_id)
        return self.mcp_handler.list_tools(server_id=server_id)

    def _handle_mcp_call_tool(self, action: Action, context: ProjectContext) -> dict:
        server_id = action.get_param("server_id")
        tool_name = action.get_param("tool_name")
        arguments = action.get_param("arguments", {})
        timeout = action.get_param("timeout")
        if not server_id or not tool_name:
            raise RuntimeError("Missing 'server_id' or 'tool_name' for MCP_CALL_TOOL")
        logger.info("Calling tool %s on MCP server %s", tool_name, server_id)
        return self.mcp_handler.call_tool(server_id=server_id, tool_name=tool_name, arguments=arguments, timeout=timeout)

    def _handle_mcp_server_status(self, action: Action, context: ProjectContext) -> dict:
        server_id = action.get_param("server_id")
        return self.mcp_handler.server_status(server_id=server_id)

    def __del__(self) -> None:
        try:
            # Best-effort cleanup of MCP servers
            if hasattr(self, "mcp_client") and self.mcp_client:
                self.mcp_client.shutdown_all()
        except Exception:
            # Avoid destructor-time exceptions
            pass

    # -- Design & specification generation -------------------------------------------------

    def _handle_design_blueprint(self, action: Action, context: ProjectContext) -> AgentSpecification:
        request_summary = action.get_param("request", "")
        logger.info("Starting blueprint workflow for request: %s", request_summary or "<no request provided>")

        spec = self.blueprint_handler.execute_design_blueprint(action, context)
        if not isinstance(spec, AgentSpecification):
            logger.error(
                "DESIGN_BLUEPRINT handler returned unexpected result type: %s",
                type(spec).__name__,
            )
            raise TypeError("DESIGN_BLUEPRINT handler must return an AgentSpecification")

        self.file_registry.refresh()
        context.active_files = self.file_registry.list_files()

        blueprint = spec.blueprint if isinstance(spec.blueprint, dict) else {}
        files_payload = blueprint.get("files")
        planned_files: list[str] = []
        if isinstance(files_payload, list):
            for file_item in files_payload:
                if isinstance(file_item, dict):
                    file_path = file_item.get("file_path")
                    if isinstance(file_path, str):
                        planned_files.append(file_path)

        context.extras["latest_specification"] = spec.model_dump()
        logger.info(
            "Blueprint ready for request '%s': %d planned file(s)",
            request_summary or spec.request,
            len(planned_files),
        )

        # Auto-spawn terminal agent if enabled
        auto_spawn = action.get_param("auto_spawn", True)  # Default to True
        if auto_spawn:
            logger.info("Auto-spawning terminal agent for task %s", spec.task_id)
            try:
                session, process = self.terminal_service.spawn_agent_for_supervision(spec)
                self.terminal_session_manager.register_session(session, process=process)
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
        if command is not None:
            # If an explicit command is provided, use the legacy spawn (may open external terminal)
            session = self.terminal_service.spawn_agent(
                specification,
                command_override=command,
            )
            self.terminal_session_manager.register_session(session)
        else:
            # Default to supervised spawn with I/O capture
            session, process = self.terminal_service.spawn_agent_for_supervision(
                specification,
            )
            self.terminal_session_manager.register_session(session, process=process)
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

    # -- Terminal I/O supervision handled by TerminalSupervisor --------------------------
