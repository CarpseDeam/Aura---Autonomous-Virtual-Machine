"""Code generation and refinement handlers."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from src.aura.app.event_bus import EventBus
from src.aura.models.action import Action
from src.aura.models.events import Event
from src.aura.models.project_context import ProjectContext
from src.aura.models.result import Result
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.services.ast_service import ASTService
from src.aura.services.context_retrieval_service import ContextRetrievalService
from src.aura.services.llm_service import LLMService
from src.aura.services.workspace_service import WorkspaceService

from .code_generation_stream import stream_and_finalize
from .code_sanitizer import CodeSanitizer
from .project_resolver import GenerationContext, ProjectResolver
from .prompt_builder import PromptBuilder


logger = logging.getLogger(__name__)


class CodeGenerator:
    """Generate and refine code via the engineer agent."""

    def __init__(
        self,
        event_bus: EventBus,
        llm: LLMService,
        prompts: PromptManager,
        ast: ASTService,
        context: ContextRetrievalService,
        workspace: WorkspaceService,
        prompt_builder: PromptBuilder,
        project_resolver: ProjectResolver,
        code_sanitizer: CodeSanitizer,
    ) -> None:
        self.event_bus = event_bus
        self.llm = llm
        self.prompts = prompts
        self.ast = ast
        self.context = context
        self.workspace = workspace
        self.prompt_builder = prompt_builder
        self.project_resolver = project_resolver
        self.code_sanitizer = code_sanitizer

    def execute_generate_code_for_spec(self, spec: Dict[str, Any], user_request: str) -> Dict[str, Any]:
        """Generate code for a single blueprint spec."""
        file_path = spec.get("file_path") or "workspace/generated.py"
        description = spec.get("description") or user_request or f"Implement the file {file_path}."
        self.prompt_builder.update_prototype_mode(user_request)

        try:
            payload = {"task_id": None, "task_description": description}
            self.event_bus.dispatch(Event(event_type="DISPATCH_TASK", payload=payload))
        except Exception:
            logger.debug("Failed to dispatch DISPATCH_TASK event for %s", file_path, exc_info=True)

        context_data = self.context.get_context_for_task(description, file_path)
        file_already_exists = self.workspace.file_exists(file_path)

        parent_class_name: Optional[str] = None
        parent_class_source: Optional[str] = None
        if isinstance(spec, dict):
            parent_class_name = next(
                (
                    spec.get(key)
                    for key in ("parent_class", "base_class", "inherits_from", "extends")
                    if isinstance(spec.get(key), str) and spec.get(key)
                ),
                None,
            )
        if parent_class_name:
            try:
                parent_path = self.ast.find_class_file_path(parent_class_name) if hasattr(self.ast, "find_class_file_path") else None
                if parent_path:
                    parent_class_source = self.context._read_file_content(parent_path) or None  # type: ignore[attr-defined]
            except Exception:
                parent_class_source = None

        workspace_source = self.workspace.get_file_content(file_path)
        read_backing = self.context._read_file_content(file_path) if hasattr(self.context, "_read_file_content") else ""  # type: ignore[attr-defined]
        current_source = workspace_source if workspace_source is not None else (read_backing or "")
        prompt = self.prompts.render(
            "engineer.jinja2",
            file_path=file_path,
            user_request=description,
            source_code=current_source,
            spec=spec,
            context_files=context_data,
            parent_class_name=parent_class_name,
            parent_class_source=parent_class_source,
            generation_mode=self.project_resolver.current_generation_mode,
            existing_project=self.project_resolver.current_project_name,
            file_already_exists=file_already_exists,
            project_file_index=self.project_resolver.current_project_files,
        )
        if not prompt:
            self._handle_error("Failed to render engineer prompt for spec.")
            return {"file_path": file_path, "status": "prompt_error"}

        self._stream_and_finalize(prompt, "engineer_agent", file_path, validate_with_spec=spec)
        return {"file_path": file_path}

    def execute_refine_code(self, action: Action, ctx: ProjectContext) -> Result:
        """Refine code for an existing file."""
        file_path = action.get_param("file_path", "workspace/generated.py")
        request_text = action.get_param("request", "")
        self.prompt_builder.update_prototype_mode(request_text)

        source_code = ""
        workspace_source = self.workspace.get_file_content(file_path)
        if workspace_source:
            source_code = workspace_source
        elif hasattr(self.context, "_read_file_content"):
            try:
                fallback = self.context._read_file_content(file_path)  # type: ignore[attr-defined]
                source_code = fallback or ""
            except Exception:
                source_code = ""

        project_files = []
        try:
            project_files = self.workspace.get_project_files()[: self.project_resolver.max_existing_files]  # type: ignore[attr-defined]
        except Exception:
            project_files = []

        prompt = self.prompts.render(
            "engineer.jinja2",
            file_path=file_path,
            user_request=request_text,
            source_code=source_code,
            spec=None,
            context_files=[],
            parent_class_name=None,
            parent_class_source=None,
            generation_mode="edit",
            existing_project=getattr(self.workspace, "active_project", None),
            file_already_exists=bool(source_code),
            project_file_index=project_files,
        )
        if not prompt:
            return Result(ok=False, kind="code", error="Failed to render engineer prompt", data={})

        self._stream_and_finalize(prompt, "engineer_agent", file_path, validate_with_spec=None)
        return Result(ok=True, kind="code", data={"file_path": file_path})

    def _determine_generation_context(self, user_text: str, ctx: ProjectContext) -> GenerationContext:
        """Expose project resolution to callers that still rely on the legacy helper."""
        return self.project_resolver.determine_generation_context(user_text, ctx)

    def _stream_and_finalize(
        self,
        prompt: str,
        agent_name: str,
        file_path: str,
        validate_with_spec: Optional[Dict[str, Any]],
        *,
        prototype_override: Optional[bool] = None,
    ) -> None:
        """Stream generated code and forward results to the event bus."""

        stream_and_finalize(
            llm=self.llm,
            event_bus=self.event_bus,
            prompt_builder=self.prompt_builder,
            code_sanitizer=self.code_sanitizer,
            prompt=prompt,
            agent_name=agent_name,
            file_path=file_path,
            validate_with_spec=validate_with_spec,
            prototype_override=prototype_override,
            on_error=self._handle_error,
        )

    @staticmethod
    def _handle_error(message: str) -> None:
        logger.error(message)
