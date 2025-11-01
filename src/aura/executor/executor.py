from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from src.aura.app.event_bus import EventBus
from src.aura.models.action import Action, ActionType
from src.aura.models.events import Event
from src.aura.models.project_context import ProjectContext
from src.aura.models.result import Result
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.services.ast_service import ASTService
from src.aura.services.context_retrieval_service import ContextRetrievalService
from src.aura.services.llm_service import LLMService
from src.aura.services.workspace_service import WorkspaceService
from src.aura.services.file_registry import FileRegistry
from src.aura.services.import_validator import ImportValidator

from .blueprint_handler import BlueprintHandler
from .code_generator import CodeGenerator
from .code_sanitizer import CodeSanitizer
from .conversation_handler import ConversationHandler
from .file_operations import FileOperations
from .project_resolver import ProjectResolver
from .prompt_builder import PromptBuilder


logger = logging.getLogger(__name__)


Handler = Callable[[Action, ProjectContext], Any]


class AuraExecutor:
    """Execution layer: delegates work to specialized handlers."""

    def __init__(
        self,
        event_bus: EventBus,
        llm: LLMService,
        prompts: PromptManager,
        ast: ASTService,
        context: ContextRetrievalService,
        workspace: WorkspaceService,
        file_registry: Optional[FileRegistry] = None,
        import_validator: Optional[ImportValidator] = None,
    ) -> None:
        self.event_bus = event_bus
        self.file_registry = file_registry
        self.import_validator = import_validator

        self.code_sanitizer = CodeSanitizer()
        self.prompt_builder = PromptBuilder(prompts)
        self.project_resolver = ProjectResolver(workspace)
        self.file_operations = FileOperations(workspace, ast)
        self.conversation_handler = ConversationHandler(llm, prompts, self.code_sanitizer)
        self.code_generator = CodeGenerator(
            event_bus=event_bus,
            llm=llm,
            prompts=prompts,
            ast=ast,
            context=context,
            workspace=workspace,
            prompt_builder=self.prompt_builder,
            project_resolver=self.project_resolver,
            code_sanitizer=self.code_sanitizer,
        )
        self.blueprint_handler = BlueprintHandler(
            event_bus=event_bus,
            llm=llm,
            prompts=prompts,
            project_resolver=self.project_resolver,
            prompt_builder=self.prompt_builder,
            code_sanitizer=self.code_sanitizer,
            file_registry=file_registry,
        )
        self._tools: Dict[ActionType, Handler] = {
            ActionType.DESIGN_BLUEPRINT: self.blueprint_handler.execute_design_blueprint,
            ActionType.REFINE_CODE: self.code_generator.execute_refine_code,
            ActionType.DISCUSS: self.conversation_handler.execute_discuss,
            ActionType.SIMPLE_REPLY: self.conversation_handler.execute_simple_reply,
            ActionType.RESEARCH: self.conversation_handler.execute_research,
            ActionType.LIST_FILES: self.file_operations.execute_list_files,
            ActionType.READ_FILE: self.file_operations.execute_read_file,
            ActionType.WRITE_FILE: self.file_operations.execute_write_file,
        }

    def execute(self, action: Action, project_context: ProjectContext) -> Any:
        """Execute a single action using the registered handler."""
        tool = self._tools.get(action.type)
        if not tool:
            logger.warning("Unsupported action type requested: %s", action.type)
            return Result(ok=False, kind="unknown", error="Unsupported action type", data={})
        return tool(action, project_context)

    def execute_blueprint(self, user_request: str, project_context: ProjectContext) -> Dict[str, Any]:
        """Run the full blueprint workflow (design + code generation) for a request."""
        if not isinstance(user_request, str) or not user_request.strip():
            raise ValueError("user_request must be a non-empty string")

        request_text = user_request.strip()
        logger.info("Executing blueprint workflow for routed code request.")

        action = Action(type=ActionType.DESIGN_BLUEPRINT, params={"request": request_text})
        blueprint = self.blueprint_handler.execute_design_blueprint(action, project_context)
        blueprint_data = blueprint if isinstance(blueprint, dict) else {}

        try:
            self.event_bus.dispatch(Event(event_type="BLUEPRINT_GENERATED", payload=blueprint_data))
        except Exception:
            logger.debug("Failed to dispatch BLUEPRINT_GENERATED event.", exc_info=True)

        planned_specs = self.blueprint_handler.files_from_blueprint(blueprint_data)
        for spec in planned_specs:
            try:
                self.code_generator.execute_generate_code_for_spec(spec, request_text)
            except Exception as exc:
                logger.error("Failed to generate code for spec %s: %s", spec.get("file_path"), exc, exc_info=True)

        # VALIDATION GATE: Validate all generated files before marking build complete
        validation_result = None
        if self.file_registry and self.import_validator:
            try:
                logger.info("Running validation gate on generated files...")
                self.event_bus.dispatch(Event(
                    event_type="GENERATION_PROGRESS",
                    payload={"message": "Validating generated code...", "category": "SYSTEM"}
                ))

                # End the generation session to finalize the registry
                session_files = self.file_registry.end_generation_session()
                logger.info("Generation session ended: %d files generated", len(session_files))

                # Run validation and auto-fixing
                validation_result = self.import_validator.validate_and_fix()

                # Log validation results
                if validation_result.files_auto_fixed > 0:
                    logger.info("Auto-fixed %d file(s) during validation", validation_result.files_auto_fixed)
                if validation_result.files_with_errors > 0:
                    logger.warning("Validation completed with %d error(s)", validation_result.files_with_errors)
                else:
                    logger.info("All files passed validation")

            except Exception as exc:
                logger.error("Validation gate failed: %s", exc, exc_info=True)
                # Don't fail the build if validation fails - just log it
                try:
                    self.event_bus.dispatch(Event(
                        event_type="GENERATION_PROGRESS",
                        payload={"message": f"Validation error: {exc}", "category": "WARNING"}
                    ))
                except Exception:
                    pass

        try:
            self.event_bus.dispatch(Event(event_type="BUILD_COMPLETED", payload={}))
        except Exception:
            logger.debug("Failed to dispatch BUILD_COMPLETED event.", exc_info=True)

        file_paths = [
            spec.get("file_path")
            for spec in planned_specs
            if isinstance(spec, dict) and isinstance(spec.get("file_path"), str)
        ]
        result = {"blueprint": blueprint_data, "planned_files": file_paths}
        if validation_result:
            result["validation"] = {
                "success": validation_result.success,
                "files_validated": validation_result.files_validated,
                "files_with_errors": validation_result.files_with_errors,
                "files_auto_fixed": validation_result.files_auto_fixed,
            }
        return result

    def _build_generation_messages(
        self,
        prompt: str,
        *,
        prototype_override: Optional[bool] = None,
    ) -> List[Dict[str, str]]:
        return self.prompt_builder.build_generation_messages(
            prompt,
            prototype_override=prototype_override,
        )

    def _strip_code_fences(self, text: str) -> str:
        return self.code_sanitizer.strip_code_fences(text)

    def _sanitize_code(self, code: str) -> str:
        return self.code_sanitizer.sanitize_code(code)

    def _parse_json_safely(self, text: str) -> Dict[str, Any]:
        return self.code_sanitizer.parse_json_safely(text)

    def _files_from_blueprint(self, blueprint_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        return self.blueprint_handler.files_from_blueprint(blueprint_data)
