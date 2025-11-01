"""Resolve project context and activation for generation workflows."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from src.aura.models.project_context import ProjectContext
from src.aura.services.workspace_service import WorkspaceService

from .project_match_utils import (
    looks_like_creation_request,
    looks_like_edit_request,
    match_project_name,
)


logger = logging.getLogger(__name__)


class GenerationContext(BaseModel):
    """Context required to render prompts for code generation."""

    mode: str
    project_name: Optional[str]
    existing_files: List[str]


class ProjectResolver:
    """Infer the target project for a request and keep related state."""

    _DEFAULT_PROJECT_NAME = "default_project"

    def __init__(self, workspace: WorkspaceService, *, max_existing_files: int = 200) -> None:
        self.workspace = workspace
        self.max_existing_files = max_existing_files
        self._current_generation_mode: str = "create"
        self._current_project_name: Optional[str] = None
        self._current_project_files: List[str] = []

    @property
    def max_existing_files(self) -> int:  # type: ignore[override]
        return self._max_existing_files

    @max_existing_files.setter
    def max_existing_files(self, value: int) -> None:
        self._max_existing_files = value

    @property
    def current_generation_mode(self) -> str: return self._current_generation_mode

    @property
    def current_project_name(self) -> Optional[str]: return self._current_project_name

    @property
    def current_project_files(self) -> List[str]: return list(self._current_project_files)

    def determine_generation_context(self, user_text: str, ctx: ProjectContext) -> GenerationContext:
        projects = self.workspace.list_workspace_projects()
        matched_project = self._match_project_name(user_text, projects) or self._match_project_from_context(
            ctx, projects, user_text
        )
        if matched_project:
            self._ensure_active_project(matched_project)
            try:
                available_files = self.workspace.get_project_files()
            except Exception:
                available_files = []

            limited_files = available_files[: self.max_existing_files]
            self._current_generation_mode = "edit"
            self._current_project_name = matched_project
            self._current_project_files = limited_files
            return GenerationContext(mode="edit", project_name=matched_project, existing_files=limited_files)

        self._current_generation_mode = "create"
        self._current_project_name = None
        self._current_project_files = []
        return GenerationContext(mode="create", project_name=None, existing_files=[])

    def activate_project_from_blueprint(
        self,
        blueprint: Dict[str, Any],
        generation_context: GenerationContext,
        user_text: str,
    ) -> None:
        if generation_context.mode == "edit":
            return

        project_label = (user_text or "").strip()
        if isinstance(blueprint, dict):
            project_label = next(
                (value for value in (str(blueprint.get(k) or "").strip() for k in ("project_slug", "project_name", "slug", "name")) if value),
                project_label,
            )
        project_slug = self._ensure_unique_slug(self._to_project_slug(project_label))

        try:
            self.workspace.set_active_project(project_slug)
        except Exception as exc:
            logger.error("Failed to activate project '%s' from blueprint: %s", project_slug, exc, exc_info=True)
            return

        self._current_project_name = project_slug
        self._current_project_files = []

        if isinstance(blueprint, dict):
            blueprint.setdefault("project_slug", project_slug)
            blueprint.setdefault("project_name", project_label)

    def _match_project_name(self, user_text: str, projects: List[Dict[str, Any]]) -> Optional[str]:
        return match_project_name(user_text, projects)

    def _match_project_from_context(
        self,
        ctx: ProjectContext,
        projects: List[Dict[str, Any]],
        user_text: str,
    ) -> Optional[str]:
        active_project = (ctx.active_project or "").strip() if ctx else ""
        if not active_project or not self._project_exists(active_project, projects):
            return None
        if active_project == self._DEFAULT_PROJECT_NAME:
            return None
        if looks_like_edit_request(user_text):
            return active_project

        non_default = [
            (project or {}).get("name")
            for project in projects
            if (project or {}).get("name") and (project or {}).get("name") != self._DEFAULT_PROJECT_NAME
        ]
        if len(non_default) == 1 and non_default[0] == active_project:
            if not looks_like_creation_request(user_text):
                return active_project
        return None

    def _ensure_active_project(self, project_name: str) -> None:
        if not project_name or getattr(self.workspace, "active_project", None) == project_name:
            return
        try:
            self.workspace.set_active_project(project_name)
        except Exception as exc:
            logger.error("Failed to activate project '%s': %s", project_name, exc, exc_info=True)

    def _ensure_unique_slug(self, slug: str) -> str:
        existing = {
            (entry or {}).get("name")
            for entry in self.workspace.list_workspace_projects()
        }
        existing.discard(None)
        if slug not in existing:
            return slug

        counter = 2
        candidate = f"{slug}-{counter}"
        while candidate in existing:
            counter += 1
            candidate = f"{slug}-{counter}"
        return candidate

    @staticmethod
    def _to_project_slug(text: str) -> str:
        tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
        slug = "-".join(tokens)
        return slug or "project"

    @staticmethod
    def _project_exists(project_name: str, projects: List[Dict[str, Any]]) -> bool:
        return any((entry or {}).get("name") == project_name for entry in projects)
