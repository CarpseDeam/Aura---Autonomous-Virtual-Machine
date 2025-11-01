"""Handle blueprint design and related utilities."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.aura.app.event_bus import EventBus
from src.aura.models.action import Action
from src.aura.models.events import Event
from src.aura.models.project_context import ProjectContext
from src.aura.prompts.prompt_manager import PromptManager
from src.aura.services.llm_service import LLMService

from .code_sanitizer import CodeSanitizer
from .project_resolver import GenerationContext, ProjectResolver
from .prompt_builder import PromptBuilder


logger = logging.getLogger(__name__)


class BlueprintHandler:
    """Coordinate blueprint design flows."""

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

    def execute_design_blueprint(self, action: Action, ctx: ProjectContext) -> Dict[str, Any]:
        """Generate a project blueprint for a user request."""
        user_text = action.get_param("request", "")
        self.prompt_builder.update_prototype_mode(user_text)
        generation_context = self.project_resolver.determine_generation_context(user_text, ctx)
        prompt = self.prompts.render(
            "architect.jinja2",
            user_text=user_text,
            generation_mode=generation_context.mode,
            target_project=generation_context.project_name,
            existing_files=generation_context.existing_files,
        )
        if not prompt:
            raise RuntimeError("Failed to render architect prompt")

        try:
            self.event_bus.dispatch(Event(
                event_type="GENERATION_PROGRESS",
                payload={"message": "Planning file structure...", "category": "SYSTEM"},
            ))
        except Exception:
            logger.debug("Failed to dispatch planning progress event.", exc_info=True)

        response = self.llm.run_for_agent("architect_agent", prompt)
        data = self.code_sanitizer.parse_json_safely(response)
        if isinstance(data, dict):
            data["_aura_mode"] = generation_context.mode
            if generation_context.mode == "edit" and generation_context.project_name:
                target_name = generation_context.project_name
                data.setdefault("project_name", target_name)
                data.setdefault("project_slug", target_name)

        self.project_resolver.activate_project_from_blueprint(
            data if isinstance(data, dict) else {},
            generation_context,
            user_text,
        )

        if not self._blueprint_has_files(data):
            raise RuntimeError("Architect returned no files in blueprint")

        files = self._files_from_blueprint(data)
        try:
            self.event_bus.dispatch(Event(
                event_type="GENERATION_PROGRESS",
                payload={"message": f"Blueprint ready: {len(files)} file{'s' if len(files) != 1 else ''}", "category": "SYSTEM"},
            ))
        except Exception:
            logger.debug("Failed to dispatch blueprint progress event.", exc_info=True)

        return data

    def files_from_blueprint(self, blueprint_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Expose parsed file specs for external callers."""
        return self._files_from_blueprint(blueprint_data)

    def blueprint_has_files(self, blueprint_data: Any) -> bool:
        """Public wrapper around the file presence check."""
        return self._blueprint_has_files(blueprint_data)

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
    def _blueprint_has_files(blueprint_data: Any) -> bool:
        if not isinstance(blueprint_data, dict):
            return False
        files = blueprint_data.get("files")
        if isinstance(files, list) and len([f for f in files if isinstance(f, dict)]) > 0:
            return True
        bp = blueprint_data.get("blueprint")
        if isinstance(bp, dict) and len(bp.keys()) > 0:
            return True
        return False
