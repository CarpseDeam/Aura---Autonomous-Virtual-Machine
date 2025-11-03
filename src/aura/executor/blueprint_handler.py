"""Generate agent-facing specifications for external terminal builders."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.aura.app.event_bus import EventBus
from src.aura.models.action import Action
from src.aura.models.agent_task import AgentSpecification
from src.aura.models.events import Event
from src.aura.models.project_context import ProjectContext
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.services.llm_service import LLMService

from .code_sanitizer import CodeSanitizer
from .project_resolver import ProjectResolver
from .prompt_builder import PromptBuilder


logger = logging.getLogger(__name__)


class BlueprintHandler:
    """Coordinate prompt generation for terminal coding agents."""

    def __init__(
        self,
        event_bus: EventBus,
        llm: LLMService,
        prompts: PromptManager,
        project_resolver: ProjectResolver,
        prompt_builder: PromptBuilder,
        code_sanitizer: CodeSanitizer,
    ) -> None:
        self.event_bus = event_bus
        self.llm = llm
        self.prompts = prompts
        self.project_resolver = project_resolver
        self.prompt_builder = prompt_builder
        self.code_sanitizer = code_sanitizer

    def execute_design_blueprint(self, action: Action, ctx: ProjectContext) -> AgentSpecification:
        """
        Generate a high-quality specification for the external terminal agent.
        """
        user_text = action.get_param("request", "")
        generation_context = self.project_resolver.determine_generation_context(user_text, ctx)
        logger.debug(
            "Generation context resolved: mode=%s, project_name=%s",
            generation_context.mode,
            generation_context.project_name,
        )
        architect_prompt = self.prompts.render(
            "architect.jinja2",
            user_text=user_text,
            generation_mode=generation_context.mode,
            target_project=generation_context.project_name,
            existing_files=generation_context.existing_files,
        )
        if not architect_prompt:
            raise RuntimeError("Failed to render architect prompt")

        self._dispatch_progress("Planning file structure...")

        response = self.llm.run_for_agent("architect_agent", architect_prompt)
        blueprint = self.code_sanitizer.parse_json_safely(response)
        if isinstance(blueprint, dict):
            blueprint["_aura_mode"] = generation_context.mode
            if generation_context.mode == "edit" and generation_context.project_name:
                project = generation_context.project_name
                blueprint.setdefault("project_name", project)
                blueprint.setdefault("project_slug", project)

        if isinstance(blueprint, dict):
            logger.debug("Blueprint returned project_name: %s", blueprint.get("project_name"))
            logger.debug("Blueprint returned project_slug: %s", blueprint.get("project_slug"))

        self.project_resolver.activate_project_from_blueprint(
            blueprint if isinstance(blueprint, dict) else {},
            generation_context,
            user_text,
        )

        if not self._blueprint_has_files(blueprint):
            raise RuntimeError("Architect returned no files in blueprint")

        planned_files = self._files_from_blueprint(blueprint)
        self._dispatch_progress(
            f"Blueprint ready: {len(planned_files)} file{'s' if len(planned_files) != 1 else ''}"
        )

        # Resolve the project name for the outgoing specification with strict rules:
        # - In EDIT mode: always use the matched project from generation_context
        # - In CREATE mode: prefer blueprint project_slug/name, then active context project
        # - Never allow metadata directory names like ".aura" to be used
        invalid_project_names = {".aura"}
        resolved_name: Optional[str]
        if generation_context.mode == "edit":
            resolved_name = generation_context.project_name
        else:
            bp_name = None
            if isinstance(blueprint, dict):
                bp_name = (
                    blueprint.get("project_slug")
                    or blueprint.get("project_name")
                    or blueprint.get("slug")
                    or blueprint.get("name")
                )
            resolved_name = (str(bp_name).strip() if isinstance(bp_name, str) and bp_name.strip() else None) or (
                (ctx.active_project.strip() if getattr(ctx, "active_project", None) else None)
            ) or self.project_resolver.current_project_name

        # Final guard: never propagate an invalid/metadata project name
        if isinstance(resolved_name, str) and resolved_name.strip() in invalid_project_names:
            logger.warning(
                "Resolved project_name '%s' is invalid for specifications; falling back to workspace root",
                resolved_name,
            )
            resolved_name = None

        # Default to a safe project when still unresolved
        if not resolved_name:
            resolved_name = "default_project"

        project_name = resolved_name
        logger.debug("Resolved spec project_name: %s", project_name)

        task_id = str(uuid.uuid4())
        prompt_content = self._render_terminal_prompt(
            task_id=task_id,
            request=user_text,
            project_name=project_name,
            planned_files=planned_files,
            project_files=generation_context.existing_files,
        )

        files_to_watch = [
            spec["file_path"]
            for spec in planned_files
            if isinstance(spec.get("file_path"), str)
        ]

        return AgentSpecification(
            task_id=task_id,
            request=user_text,
            project_name=project_name,
            blueprint=blueprint if isinstance(blueprint, dict) else {},
            prompt=prompt_content,
            files_to_watch=files_to_watch,
            metadata={
                "mode": generation_context.mode,
                "generated_at": datetime.utcnow().isoformat(),
            },
        )

    def build_manual_specification(
        self,
        *,
        request: str,
        ctx: ProjectContext,
        target_files: Optional[List[str]] = None,
        notes: Optional[List[str]] = None,
    ) -> AgentSpecification:
        """
        Construct an agent specification without consulting the architect model.
        Useful for refinement or targeted edits.
        """
        generation_context = self.project_resolver.determine_generation_context(request, ctx)
        planned_files: List[Dict[str, Any]] = []
        for path in target_files or []:
            planned_files.append({
                "file_path": path,
                "description": f"Refine or update `{path}`.",
                "notes": notes or [],
            })
        if not planned_files:
            planned_files.append({
                "file_path": "<unspecified>",
                "description": "Clarify the target file with Aura before editing.",
                "notes": notes or [],
            })

        task_id = str(uuid.uuid4())
        prompt_content = self._render_terminal_prompt(
            task_id=task_id,
            request=request,
            project_name=generation_context.project_name or self.project_resolver.current_project_name,
            planned_files=planned_files,
            project_files=generation_context.existing_files,
        )

        return AgentSpecification(
            task_id=task_id,
            request=request,
            project_name=generation_context.project_name,
            blueprint={"manual": True, "files": planned_files},
            prompt=prompt_content,
            files_to_watch=[
                spec["file_path"] for spec in planned_files if isinstance(spec.get("file_path"), str)
            ],
            metadata={
                "mode": generation_context.mode,
                "generated_at": datetime.utcnow().isoformat(),
                "type": "refine",
            },
        )

    def _render_terminal_prompt(
        self,
        *,
        task_id: str,
        request: str,
        project_name: Optional[str],
        planned_files: List[Dict[str, Any]],
        project_files: List[str],
    ) -> str:
        planned_payload = [
            {
                "file_path": spec.get("file_path", ""),
                "description": spec.get("description", ""),
                "classes": self._extract_names(spec.get("classes")),
                "methods": self._extract_function_signatures(spec.get("functions")),
                "extra": self._collect_extra_notes(spec),
            }
            for spec in planned_files
        ]

        mission_summary = request.strip() or "Implement the requested feature set."
        generated_at = datetime.utcnow().isoformat()

        rendered = self.prompts.render(
            "terminal_agent_prompt.jinja2",
            task_id=task_id,
            request=request,
            project_name=project_name,
            mission_summary=mission_summary,
            planned_files=planned_payload,
            project_files=project_files,
            generated_at=generated_at,
        )
        if not rendered:
            raise RuntimeError("Failed to render terminal agent prompt")
        return rendered

    def files_from_blueprint(self, blueprint_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Expose parsed file specs for external callers."""
        return self._files_from_blueprint(blueprint_data)

    def blueprint_has_files(self, blueprint_data: Any) -> bool:
        """Public wrapper around the file presence check."""
        return self._blueprint_has_files(blueprint_data)

    def _dispatch_progress(self, message: str) -> None:
        try:
            self.event_bus.dispatch(Event(
                event_type="GENERATION_PROGRESS",
                payload={"message": message, "category": "SYSTEM"},
            ))
        except Exception:
            logger.debug("Failed to dispatch generation progress event.", exc_info=True)

    @staticmethod
    def _files_from_blueprint(blueprint_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        files: List[Dict[str, Any]] = []
        if not isinstance(blueprint_data, dict):
            return files

        project_metadata: Dict[str, Any] = {}
        for key in ("project_slug", "project_name", "slug", "name"):
            value = blueprint_data.get(key)
            if isinstance(value, str) and value.strip():
                project_metadata[key] = value.strip()

        def _augment_spec(raw_spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            if not isinstance(raw_spec, dict):
                return None
            spec_copy: Dict[str, Any] = dict(raw_spec)
            if "file_path" not in spec_copy or not isinstance(spec_copy["file_path"], str):
                return None
            for meta_key, meta_value in project_metadata.items():
                spec_copy.setdefault(meta_key, meta_value)
            return spec_copy

        file_entries = blueprint_data.get("files")
        if isinstance(file_entries, list):
            for entry in file_entries:
                spec_copy = _augment_spec(entry)
                if spec_copy:
                    files.append(spec_copy)
            return files

        blueprint_entries = blueprint_data.get("blueprint")
        if isinstance(blueprint_entries, dict):
            for file_path, spec in blueprint_entries.items():
                if not isinstance(file_path, str):
                    continue
                spec_copy = dict(spec) if isinstance(spec, dict) else {}
                spec_copy["file_path"] = file_path
                augmented = _augment_spec(spec_copy)
                if augmented:
                    files.append(augmented)
        return files

    @staticmethod
    def _extract_names(entries: Any) -> List[str]:
        results: List[str] = []
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    name = entry.get("name")
                    if isinstance(name, str) and name.strip():
                        results.append(name.strip())
                elif isinstance(entry, str) and entry.strip():
                    results.append(entry.strip())
        return results

    @staticmethod
    def _extract_function_signatures(entries: Any) -> List[str]:
        signatures: List[str] = []
        if not isinstance(entries, list):
            return signatures
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            params = entry.get("parameters") or entry.get("params") or []
            if isinstance(params, list):
                param_names = [
                    param.get("name")
                    if isinstance(param, dict)
                    else str(param)
                    for param in params
                ]
            else:
                param_names = []
            if isinstance(name, str) and name.strip():
                signatures.append(f"{name.strip()}({', '.join([p for p in param_names if isinstance(p, str)])})")
        return signatures

    @staticmethod
    def _collect_extra_notes(spec: Dict[str, Any]) -> List[str]:
        notes: List[str] = []
        for key in ("notes", "requirements", "considerations", "todo"):
            value = spec.get(key)
            if isinstance(value, str) and value.strip():
                notes.append(value.strip())
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.strip():
                        notes.append(item.strip())
        return notes

    @staticmethod
    def _blueprint_has_files(blueprint_data: Any) -> bool:
        if not isinstance(blueprint_data, dict):
            return False
        files = blueprint_data.get("files")
        if isinstance(files, list) and any(isinstance(f, dict) for f in files):
            return True
        bp = blueprint_data.get("blueprint")
        if isinstance(bp, dict) and bp:
            return True
        return False
