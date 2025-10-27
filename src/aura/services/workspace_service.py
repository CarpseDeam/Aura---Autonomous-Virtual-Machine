import os
import logging
from pathlib import Path
from typing import Optional

# Application-specific imports
from src.aura.config import ROOT_DIR
from src.aura.app.event_bus import EventBus
from src.aura.models.events import Event
from src.aura.services.ast_service import ASTService

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
        Save code to a file within the active project directory.
        
        Args:
            file_path: Relative file path within the project
            code: Code content to save
        """
        if not self.active_project or not self.active_project_path:
            raise RuntimeError("No active project set. Call set_active_project() first.")
        
        # Ensure file_path is relative and safe
        file_path = file_path.lstrip('/')  # Remove leading slashes
        target_file = self.active_project_path / file_path
        
        # Ensure parent directories exist
        target_file.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            # Write code to file
            target_file.write_text(code, encoding='utf-8')
            logger.info(f"Code saved to project file: {target_file}")
            
            # Dispatch file saved event
            self.event_bus.dispatch(Event(
                event_type="FILE_SAVED_TO_PROJECT",
                payload={
                    "project_name": self.active_project,
                    "file_path": str(target_file),
                    "relative_path": file_path
                }
            ))
            
        except Exception as e:
            logger.error(f"Failed to save code to {target_file}: {e}")
            raise RuntimeError(f"Failed to save code to project: {e}") from e

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
        Handle CODE_GENERATED events by surgically inserting a code snippet into the target file.
        
        Args:
            event: Event containing file_path and code payload
        """
        file_path = event.payload.get("file_path")
        code = event.payload.get("code")
        
        if not file_path or not code:
            logger.warning("CODE_GENERATED event missing file_path or code payload")
            return
        
        if not self.active_project:
            logger.warning("No active project set, cannot save generated code")
            return
        
        try:
            # Extract just the filename if it's a full path
            if os.path.sep in file_path:
                filename = os.path.basename(file_path)
            else:
                filename = file_path

            # Overwrite the target file with the new content
            self.save_code_to_project(filename, code)
            logger.info(f"Automatically saved generated code to active project: {filename}")

            # Notify UI: send final saved content (for viewer updates)
            self.event_bus.dispatch(Event(
                event_type="VALIDATED_CODE_SAVED",
                payload={
                    "task_id": None,
                    "file_path": file_path,
                    "code": code,
                    "project_name": self.active_project,
                    "line_count": len(code.strip().split('\n')) if code.strip() else 0
                }
            ))
            
        except Exception as e:
            logger.error(f"Failed to auto-save generated code: {e}")

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
        Phoenix Initiative: Handle VALIDATION_SUCCESSFUL events by surgically inserting the validated snippet.
        Only code that passes the Quality Gate gets inserted into the workspace file.
        
        Args:
            event: Event containing validated code and file path
        """
        file_path = event.payload.get("file_path")
        validated_code = event.payload.get("validated_code")
        task_id = event.payload.get("task_id")
        
        if not file_path or not validated_code:
            logger.warning("VALIDATION_SUCCESSFUL event missing file_path or validated_code payload")
            return
        
        if not self.active_project:
            logger.warning("No active project set, cannot save validated code")
            return
        
        try:
            # Extract just the filename if it's a full path
            if os.path.sep in file_path:
                filename = os.path.basename(file_path)
            else:
                filename = file_path

            self.save_code_to_project(filename, validated_code)
            logger.info(f"Phoenix Initiative: Saved validated code for task {task_id} to {filename}")

            # Dispatch a special event indicating validated code was saved
            self.event_bus.dispatch(Event(
                event_type="VALIDATED_CODE_SAVED",
                payload={
                    "task_id": task_id,
                    "file_path": file_path,
                    "code": validated_code,
                    "project_name": self.active_project,
                    "line_count": len(validated_code.strip().split('\n')) if validated_code.strip() else 0
                }
            ))

        except Exception as e:
            logger.error(f"Failed to save validated code for task {task_id}: {e}")
