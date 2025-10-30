import os
import logging
import difflib
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

# Application-specific imports
from src.aura.config import ROOT_DIR
from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.services.ast_service import ASTService
from src.aura.services.user_settings_manager import get_auto_accept_changes

logger = logging.getLogger(__name__)


class WorkspaceService:
    """
    Professional-grade workspace management service with project sandboxing and symlink importing.
    Implements the Prime Directive: "re-index on context switch" for seamless project transitions.
    """

    def __init__(self, event_bus: EventBus, workspace_root: Path, ast_service: ASTService):
        """
        Initialize the WorkspaceService with critical dependencies.
        
        Args:
            event_bus: The application event bus for communication
            workspace_root: Root directory for all workspace projects
            ast_service: AST service for project indexing (Prime Directive)
        """
        self.event_bus = event_bus
        self.workspace_root = Path(workspace_root)
        self.ast_service = ast_service
        self.active_project: Optional[str] = None
        self.active_project_path: Optional[Path] = None
        self.auto_accept_changes: bool = get_auto_accept_changes()
        self._pending_changes: Dict[str, Dict[str, Any]] = {}
        
        # Ensure workspace root exists
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        
        self._register_event_handlers()
        logger.info(f"WorkspaceService initialized with workspace root: {self.workspace_root}")

    def _register_event_handlers(self):
        """Register event handlers for workspace operations."""
        # Phoenix Initiative: Subscribe to validation events instead of direct CODE_GENERATED
        self.event_bus.subscribe("VALIDATION_SUCCESSFUL", self._handle_validation_successful)
        # Legacy: Keep CODE_GENERATED for non-spec tasks
        self.event_bus.subscribe("CODE_GENERATED", self._handle_code_generated)
        self.event_bus.subscribe("IMPORT_PROJECT_REQUESTED", self._handle_import_project_requested)
        self.event_bus.subscribe("USER_PREFERENCES_UPDATED", self._handle_preferences_updated)
        self.event_bus.subscribe("APPLY_FILE_CHANGES", self._handle_apply_changes)
        self.event_bus.subscribe("REJECT_FILE_CHANGES", self._handle_reject_changes)
        logger.info("WorkspaceService subscribed to validation and import events")

    def set_active_project(self, project_name: str):
        """
        Set the active project and trigger the Prime Directive: immediate re-indexing.
        This is the core of the workspace context switching logic.
        
        Args:
            project_name: Name of the project to activate
        """
        logger.info(f"Setting active project: {project_name}")
        
        # Set active project state
        self.active_project = project_name
        self.active_project_path = self.workspace_root / project_name
        
        # Ensure project directory exists (create sandbox if needed)
        self.active_project_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Project directory ensured: {self.active_project_path}")
        
        # PRIME DIRECTIVE: Immediately re-index on context switch
        try:
            logger.info("PRIME DIRECTIVE: Triggering AST re-indexing for context switch")
            self.ast_service.index_project(str(self.active_project_path))
            logger.info(f"Prime Directive completed: Project '{project_name}' indexed successfully")
            
            # Automatic language detection after indexing completes
            try:
                self._detect_project_language()
            except Exception as det_err:
                logger.warning(f"Language detection failed: {det_err}")
            
            # Dispatch project activation event
            self.event_bus.dispatch(Event(
                event_type="PROJECT_ACTIVATED",
                payload={
                    "project_name": project_name,
                    "project_path": str(self.active_project_path)
                }
            ))
            
        except Exception as e:
            logger.error(f"Prime Directive failed during project activation: {e}")
            raise RuntimeError(f"Failed to activate project '{project_name}': AST indexing failed") from e

    def _detect_project_language(self) -> None:
        """
        Detect the dominant programming language for the active project based on
        indexed files and dispatch a PROJECT_LANGUAGE_DETECTED event.

        Strategy:
        - Query ASTService for indexed file paths
        - Count extensions (e.g., .py, .gd)
        - Map dominant extension to language name
        - Fallback to 'python' if uncertain
        """
        if not self.active_project_path:
            logger.debug("Language detection skipped: no active project path.")
            return

        try:
            indexed_files = []
            if hasattr(self.ast_service, "get_indexed_file_paths"):
                indexed_files = self.ast_service.get_indexed_file_paths()
            else:
                # Fallback: use project_index keys if available
                indexed_files = list(getattr(self.ast_service, "project_index", {}).keys())

            # Count file extensions
            counts = {}
            for rel_path in indexed_files or []:
                ext = os.path.splitext(rel_path)[1].lower()
                if not ext:
                    continue
                counts[ext] = counts.get(ext, 0) + 1

            # Determine dominant extension
            dominant_ext = None
            dominant_count = -1
            for ext, cnt in counts.items():
                if cnt > dominant_count:
                    dominant_ext = ext
                    dominant_count = cnt

            # Map extension to language
            ext_to_lang = {
                ".py": "python",
                ".gd": "gdscript",
                ".ts": "typescript",
                ".js": "javascript",
                ".rs": "rust",
                ".java": "java",
                ".cs": "csharp",
                ".cpp": "cpp",
                ".cxx": "cpp",
                ".cc": "cpp",
                ".c": "c",
                ".go": "go",
                ".rb": "ruby",
                ".php": "php",
                ".kt": "kotlin",
                ".swift": "swift",
            }

            detected_language = ext_to_lang.get(dominant_ext or "", "python")

            language_guide_path = ROOT_DIR / "src" / "aura" / "prompts" / "language_guides" / f"{detected_language}.md"
            logger.info(f"PROJECT_LANGUAGE_DETECTED -> {detected_language} (ext: {dominant_ext}, counts: {counts})")
            self.event_bus.dispatch(Event(
                event_type="PROJECT_LANGUAGE_DETECTED",
                payload={"language": detected_language}
            ))

            if not language_guide_path.exists():
                logger.info(f"Knowledge gap detected for language: {detected_language}")
                self.event_bus.dispatch(Event(
                    event_type="KNOWLEDGE_GAP_DETECTED",
                    payload={"language": detected_language}
                ))
        except Exception as e:
            logger.warning(f"Language detection encountered an error: {e}")

    def import_project_from_path(self, source_path: str):
        """
        Import an external project via symbolic link and activate it.
        
        Args:
            source_path: Absolute path to the external project to import
        """
        source_path = Path(source_path).resolve()
        
        if not source_path.exists():
            raise ValueError(f"Source path does not exist: {source_path}")
        
        if not source_path.is_dir():
            raise ValueError(f"Source path is not a directory: {source_path}")
        
        # Generate project name from the source directory
        project_name = source_path.name
        target_link = self.workspace_root / project_name
        
        logger.info(f"Importing project '{project_name}' from {source_path}")
        
        try:
            # Remove existing link/directory if it exists
            if target_link.exists() or target_link.is_symlink():
                if target_link.is_symlink():
                    target_link.unlink()
                    logger.info(f"Removed existing symlink: {target_link}")
                else:
                    logger.warning(f"Target already exists as directory: {target_link}")
                    raise ValueError(f"Cannot import: target directory '{project_name}' already exists")
            
            # Create symbolic link
            target_link.symlink_to(source_path, target_is_directory=True)
            logger.info(f"Created symbolic link: {target_link} -> {source_path}")
            
            # Activate the imported project (triggers Prime Directive)
            self.set_active_project(project_name)
            
            # Dispatch import success event
            self.event_bus.dispatch(Event(
                event_type="PROJECT_IMPORTED",
                payload={
                    "project_name": project_name,
                    "source_path": str(source_path),
                    "target_link": str(target_link)
                }
            ))
            
            logger.info(f"Project import completed successfully: {project_name}")
            
        except Exception as e:
            logger.error(f"Failed to import project from {source_path}: {e}")
            raise RuntimeError(f"Project import failed: {e}") from e

    def save_code_to_project(self, file_path: str, code: str):
        """
        Public API for writing code that respects the auto-accept preference.
        """
        self._handle_change_request(
            file_path=file_path,
            code=code,
            task_id=None,
            origin="manual_save",
        )

    def get_active_project_info(self) -> dict:
        """
        Get information about the currently active project.
        
        Returns:
            Dictionary containing active project information
        """
        return {
            "active_project": self.active_project,
            "active_project_path": str(self.active_project_path) if self.active_project_path else None,
            "workspace_root": str(self.workspace_root),
            "is_symlink": self.active_project_path.is_symlink() if self.active_project_path else False
        }

    def list_workspace_projects(self) -> list:
        """
        List all projects in the workspace.
        
        Returns:
            List of project names in the workspace
        """
        projects = []
        try:
            for item in self.workspace_root.iterdir():
                if item.is_dir() or item.is_symlink():
                    projects.append({
                        "name": item.name,
                        "path": str(item),
                        "is_symlink": item.is_symlink(),
                        "is_active": item.name == self.active_project
                    })
        except Exception as e:
            logger.warning(f"Failed to list workspace projects: {e}")
        
        return projects

    def _handle_code_generated(self, event: Event):
        """
        Handle CODE_GENERATED events by staging or applying changes based on preferences.
        """
        file_path = event.payload.get("file_path")
        code = event.payload.get("code")

        if not file_path or code is None:
            logger.warning("CODE_GENERATED event missing file_path or code payload")
            return

        if not self.active_project:
            logger.warning("No active project set, cannot process generated code.")
            return

        self._handle_change_request(
            file_path=file_path,
            code=code,
            task_id=event.payload.get("task_id"),
            origin="code_generated",
        )

    def _handle_import_project_requested(self, event: Event):
        """
        Handle IMPORT_PROJECT_REQUESTED events from the UI.
        
        Args:
            event: Event containing the project path to import
        """
        project_path = event.payload.get("path")
        
        if not project_path:
            logger.error("IMPORT_PROJECT_REQUESTED event missing path payload")
            return
        
        try:
            self.import_project_from_path(project_path)
        except Exception as e:
            logger.error(f"Failed to import project from UI request: {e}")
            # Dispatch error event for UI feedback
            self.event_bus.dispatch(Event(
                event_type="PROJECT_IMPORT_ERROR",
                payload={"error": str(e)}
            ))

    def _handle_validation_successful(self, event: Event):
        """
        Handle VALIDATION_SUCCESSFUL events by staging or applying validated code.
        """
        file_path = event.payload.get("file_path")
        validated_code = event.payload.get("validated_code")
        task_id = event.payload.get("task_id")

        if not file_path or validated_code is None:
            logger.warning("VALIDATION_SUCCESSFUL event missing file_path or validated_code payload")
            return

        if not self.active_project:
            logger.warning("No active project set, cannot save validated code")
            return

        self._handle_change_request(
            file_path=file_path,
            code=validated_code,
            task_id=task_id,
            origin="validation_successful",
        )

    def _handle_change_request(
        self,
        file_path: str,
        code: str,
        task_id: Optional[str] = None,
        origin: str = "generated",
    ) -> None:
        """
        Central handler for any generated or validated code writes.
        """
        if not code:
            logger.info("Empty code payload received for %s; skipping.", file_path)
            return

        try:
            change_entry = self._build_change_entry(file_path, code, task_id=task_id, origin=origin)
        except Exception as exc:
            logger.error(f"Failed to prepare change entry for {file_path}: {exc}", exc_info=True)
            return

        if not change_entry:
            logger.info("No changes detected for %s; nothing to stage or apply.", file_path)
            return

        change_id = change_entry["change_id"]

        if self.auto_accept_changes:
            logger.info("Auto-accept enabled; applying change %s immediately.", change_id)
            self._apply_change_entry(change_entry, auto_applied=True)
        else:
            logger.info("Auto-accept disabled; staging change %s for review.", change_id)
            self._pending_changes[change_id] = change_entry
            self._emit_diff_ready(change_entry, pending=True, auto_applied=False)

    def _build_change_entry(
        self,
        file_path: str,
        code: str,
        task_id: Optional[str],
        origin: str,
    ) -> Optional[Dict[str, Any]]:
        if not self.active_project_path:
            raise RuntimeError("Active project path is not set.")

        sanitized_relative = self._sanitize_relative_path(file_path)
        display_path = file_path
        target_file = self.active_project_path / sanitized_relative
        existing_code = ""
        file_exists = target_file.exists()

        if file_exists:
            try:
                existing_code = target_file.read_text(encoding="utf-8")
            except Exception as exc:
                logger.warning("Failed to read existing contents of %s: %s", target_file, exc)
                existing_code = ""

        diff_result = self._compute_diff(sanitized_relative, existing_code, code)
        if diff_result is None:
            return None

        diff_text, additions, deletions = diff_result
        line_count = len(code.strip().split("\n")) if code.strip() else 0

        change_id = str(uuid.uuid4())
        file_entry = {
            "display_path": display_path,
            "relative_path": sanitized_relative,
            "diff": diff_text,
            "additions": additions,
            "deletions": deletions,
            "line_count": line_count,
            "is_new_file": not file_exists,
            "code": code,
        }

        change_entry = {
            "change_id": change_id,
            "files": [file_entry],
            "origin": origin,
            "task_id": task_id,
            "summary": {
                "total_files": 1,
                "total_additions": additions,
                "total_deletions": deletions,
                "total_lines": line_count,
            },
        }

        return change_entry

    def _compute_diff(
        self,
        relative_path: str,
        existing_code: str,
        new_code: str,
    ) -> Optional[tuple]:
        old_lines = (existing_code or "").splitlines()
        new_lines = (new_code or "").splitlines()

        diff_lines = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{relative_path}",
                tofile=f"b/{relative_path}",
                lineterm="",
            )
        )

        if not diff_lines:
            return None

        additions = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        deletions = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
        diff_text = "\n".join(diff_lines)
        return diff_text, additions, deletions

    def _sanitize_relative_path(self, file_path: str) -> str:
        if not file_path:
            raise ValueError("File path cannot be empty.")

        normalized = str(file_path).strip()
        if not normalized:
            raise ValueError("File path cannot be whitespace.")

        normalized = normalized.replace("\\", "/")
        path_obj = Path(normalized)

        if path_obj.is_absolute():
            if self.active_project_path:
                try:
                    path_obj = path_obj.relative_to(self.active_project_path)
                except ValueError:
                    path_obj = Path(path_obj.name)
            else:
                path_obj = Path(path_obj.name)

        safe_parts: List[str] = []
        for part in path_obj.parts:
            if part in ("", "."):
                continue
            if part == "..":
                raise ValueError("Parent directory traversal is not allowed in file paths.")
            safe_parts.append(part)

        if not safe_parts:
            safe_parts.append(path_obj.name or "generated.py")

        return "/".join(safe_parts)

    def _emit_diff_ready(self, change_entry: Dict[str, Any], *, pending: bool, auto_applied: bool) -> None:
        payload = self._serialize_change_entry(change_entry)
        payload.update({
            "change_id": change_entry["change_id"],
            "pending": pending,
            "auto_applied": auto_applied,
        })
        self.event_bus.dispatch(Event(event_type="FILE_DIFF_READY", payload=payload))

    def _serialize_change_entry(self, change_entry: Dict[str, Any]) -> Dict[str, Any]:
        files_payload = []
        for file_info in change_entry.get("files", []):
            files_payload.append({
                "display_path": file_info.get("display_path"),
                "relative_path": file_info.get("relative_path"),
                "diff": file_info.get("diff"),
                "additions": file_info.get("additions"),
                "deletions": file_info.get("deletions"),
                "line_count": file_info.get("line_count"),
                "is_new_file": file_info.get("is_new_file"),
            })

        return {
            "files": files_payload,
            "summary": change_entry.get("summary", {}),
            "origin": change_entry.get("origin"),
            "task_id": change_entry.get("task_id"),
        }

    def _apply_change_entry(self, change_entry: Dict[str, Any], *, auto_applied: bool) -> None:
        change_id = change_entry.get("change_id")
        if not change_id:
            logger.warning("Cannot apply change entry without an ID.")
            return

        try:
            self._apply_change_files(change_entry)
            self._pending_changes.pop(change_id, None)
            self._emit_diff_ready(change_entry, pending=False, auto_applied=auto_applied)
            self._emit_changes_applied(change_entry, auto_applied=auto_applied)
        except Exception as exc:
            logger.error(f"Failed to apply change {change_id}: {exc}", exc_info=True)

    def _apply_change_files(self, change_entry: Dict[str, Any]) -> None:
        for file_info in change_entry.get("files", []):
            relative_path = file_info.get("relative_path")
            code = file_info.get("code", "")
            if relative_path is None:
                logger.warning("Skipping file without relative_path in change entry.")
                continue
            self._write_file(relative_path, code)

            line_count = file_info.get("line_count", 0)
            self.event_bus.dispatch(Event(
                event_type="VALIDATED_CODE_SAVED",
                payload={
                    "task_id": change_entry.get("task_id"),
                    "file_path": file_info.get("display_path"),
                    "code": code,
                    "project_name": self.active_project,
                    "line_count": line_count,
                }
            ))

    def _write_file(self, relative_path: str, code: str) -> None:
        if not self.active_project_path:
            raise RuntimeError("Active project path is not set.")

        target_file = self.active_project_path / relative_path
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(code, encoding="utf-8")
        logger.info("Saved code to project file: %s", target_file)

        self.event_bus.dispatch(Event(
            event_type="FILE_SAVED_TO_PROJECT",
            payload={
                "project_name": self.active_project,
                "file_path": str(target_file),
                "relative_path": relative_path,
            }
        ))

    def _emit_changes_applied(self, change_entry: Dict[str, Any], *, auto_applied: bool) -> None:
        payload = self._serialize_change_entry(change_entry)
        payload.update({
            "change_id": change_entry.get("change_id"),
            "auto_applied": auto_applied,
        })
        self.event_bus.dispatch(Event(event_type="FILE_CHANGES_APPLIED", payload=payload))

    def _emit_changes_rejected(self, change_entry: Dict[str, Any]) -> None:
        payload = self._serialize_change_entry(change_entry)
        payload.update({
            "change_id": change_entry.get("change_id"),
        })
        self.event_bus.dispatch(Event(event_type="FILE_CHANGES_REJECTED", payload=payload))

    def _handle_preferences_updated(self, event: Event) -> None:
        preferences = (event.payload or {}).get("preferences") or {}
        if "auto_accept_changes" in preferences:
            new_value = bool(preferences.get("auto_accept_changes"))
            if new_value != self.auto_accept_changes:
                self.auto_accept_changes = new_value
                if new_value:
                    logger.info(
                        "Auto-accept of generated changes enabled. %d pending change(s) remain staged.",
                        len(self._pending_changes),
                    )
                else:
                    logger.info("Auto-accept of generated changes disabled.")

    def _handle_apply_changes(self, event: Event) -> None:
        change_id = (event.payload or {}).get("change_id")
        if not change_id:
            logger.warning("APPLY_FILE_CHANGES event missing change_id.")
            return

        change_entry = self._pending_changes.get(change_id)
        if not change_entry:
            logger.warning("No pending changes found for id %s.", change_id)
            return

        self._apply_change_entry(change_entry, auto_applied=False)

    def _handle_reject_changes(self, event: Event) -> None:
        change_id = (event.payload or {}).get("change_id")
        if not change_id:
            logger.warning("REJECT_FILE_CHANGES event missing change_id.")
            return

        change_entry = self._pending_changes.pop(change_id, None)
        if not change_entry:
            logger.warning("No pending changes found for id %s.", change_id)
            return

        logger.info("Rejected pending change %s.", change_id)
        self._emit_changes_rejected(change_entry)
