import json
import logging
from datetime import datetime, timezone
from json import JSONDecodeError
from pathlib import Path
from typing import List, Optional

from pydantic import ValidationError

from src.aura.models.project import Project, ProjectSummary


logger = logging.getLogger(__name__)


class ProjectManager:
    """Manages project-level persistence and lifecycle."""

    def __init__(self, storage_dir: str = "~/.aura/projects") -> None:
        """
        Initialize project manager.

        Args:
            storage_dir: Directory to store all project data
        """
        self.storage_dir = Path(storage_dir).expanduser()
        try:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("Unable to initialize project storage at %s: %s", self.storage_dir, exc)
            raise
        self.current_project: Optional[Project] = None

    def load_project(self, project_name: str) -> Project:
        """Load existing project by name.

        Args:
            project_name: Name of the project to load.

        Returns:
            Loaded Project instance.

        Raises:
            FileNotFoundError: If the project file is missing.
            ValueError: If project data cannot be decoded or validated.
        """
        project_path = self.get_project_path(project_name) / "project.json"
        logger.debug("Attempting to load project: %s", project_path)
        if not project_path.exists():
            logger.error("Project '%s' not found at %s", project_name, project_path)
            raise FileNotFoundError(f"Project '{project_name}' not found.")

        try:
            raw_data = project_path.read_text(encoding="utf-8")
            data = json.loads(raw_data)
        except JSONDecodeError as exc:
            logger.error("Project '%s' has invalid JSON: %s", project_name, exc)
            raise ValueError(f"Project '{project_name}' data is corrupted.") from exc
        except OSError as exc:
            logger.error("Failed reading project '%s': %s", project_name, exc)
            raise

        try:
            project = Project(**data)
        except ValidationError as exc:
            logger.error("Project '%s' failed validation: %s", project_name, exc)
            raise ValueError(f"Project '{project_name}' data is invalid.") from exc

        self.current_project = project
        logger.info("Loaded project '%s' from disk.", project_name)
        return project

    def create_project(self, name: str, root_path: str) -> Project:
        """Create new project.

        Args:
            name: Filesystem-safe project name.
            root_path: Absolute path to the project root directory.

        Returns:
            Newly created Project instance.

        Raises:
            ValueError: If project already exists or name/root is invalid.
        """
        self._validate_project_name(name)
        root = Path(root_path).expanduser()
        if not root.is_absolute():
            logger.error("Root path for project '%s' must be absolute: %s", name, root_path)
            raise ValueError("Project root_path must be absolute.")
        if self.project_exists(name):
            logger.error("Project creation aborted: '%s' already exists.", name)
            raise ValueError(f"Project '{name}' already exists.")

        project_dir = self.get_project_path(name)
        try:
            project_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("Failed to create project storage directory %s: %s", project_dir, exc)
            raise

        now = datetime.now(timezone.utc)
        project = Project(
            name=name,
            root_path=str(root),
            created_at=now,
            last_active=now,
        )
        self.save_project(project)
        self.current_project = project
        logger.info("Created new project '%s' at %s.", name, project_dir)
        return project

    def save_project(self, project: Project) -> None:
        """Save project state to disk.

        Args:
            project: Project instance to persist.
        """
        project_dir = self.get_project_path(project.name)
        project_file = project_dir / "project.json"
        try:
            project_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("Unable to ensure directory for project '%s': %s", project.name, exc)
            raise

        if hasattr(project, "model_dump_json"):
            payload = json.loads(project.model_dump_json())
        else:
            payload = json.loads(project.json())
        payload["last_active"] = datetime.now(timezone.utc).isoformat()
        try:
            project_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to save project '%s': %s", project.name, exc)
            raise

        project.last_active = datetime.fromisoformat(payload["last_active"])
        logger.debug("Project '%s' saved successfully.", project.name)

    def list_projects(self) -> List[ProjectSummary]:
        """List all projects sorted by last_active.

        Returns:
            List of ProjectSummary objects sorted by most recently active first.
        """
        summaries: List[ProjectSummary] = []
        for entry in sorted(self.storage_dir.iterdir(), key=lambda p: p.name.lower()):
            project_file = entry / "project.json"
            if not project_file.is_file():
                continue
            try:
                data = json.loads(project_file.read_text(encoding="utf-8"))
                project = Project(**data)
            except (OSError, JSONDecodeError, ValidationError) as exc:
                logger.warning("Skipping project at %s due to load error: %s", project_file, exc)
                continue
            recent_topics = []
            metadata = project.metadata or {}
            if isinstance(metadata.get("recent_topics"), list):
                recent_topics = [str(topic) for topic in metadata["recent_topics"][:5]]
            summaries.append(
                ProjectSummary(
                    name=project.name,
                    root_path=project.root_path,
                    last_active=project.last_active,
                    message_count=len(project.conversation_history),
                    recent_topics=recent_topics,
                )
            )

        summaries.sort(key=lambda s: s.last_active, reverse=True)
        logger.debug("Listed %d project(s).", len(summaries))
        return summaries

    def switch_project(self, project_name: str) -> Project:
        """Switch to different project (saves current first).

        Args:
            project_name: Name of the project to switch to.

        Returns:
            The newly active Project instance.
        """
        if self.current_project and self.current_project.name != project_name:
            try:
                self.save_project(self.current_project)
            except Exception as exc:
                logger.warning("Failed to save current project '%s' before switch: %s", self.current_project.name, exc)

        project = self.load_project(project_name)
        project.last_active = datetime.now(timezone.utc)
        self.save_project(project)
        self.current_project = project
        logger.info("Switched to project '%s'.", project_name)
        return project

    def get_project_path(self, project_name: str) -> Path:
        """Get storage path for project.

        Args:
            project_name: Name of the project.

        Returns:
            Path to the project directory within storage.
        """
        self._validate_project_name(project_name)
        return self.storage_dir / project_name

    def project_exists(self, project_name: str) -> bool:
        """Check if project exists.

        Args:
            project_name: Name of the project.

        Returns:
            True if the project directory and JSON file exist.
        """
        project_file = self.get_project_path(project_name) / "project.json"
        exists = project_file.exists()
        logger.debug("Project '%s' exists: %s", project_name, exists)
        return exists

    def _validate_project_name(self, project_name: str) -> None:
        """Ensure project names are filesystem-safe."""
        if not project_name or not project_name.strip():
            logger.error("Invalid project name: blank.")
            raise ValueError("Project name must be provided.")
        if any(char in project_name for char in r"<>:\"/\\|?*"):
            logger.error("Invalid project name '%s': illegal characters present.", project_name)
            raise ValueError("Project name contains invalid filesystem characters.")
        if project_name != Path(project_name).name:
            logger.error("Invalid project name '%s': must not contain path separators.", project_name)
            raise ValueError("Project name must not contain path separators.")
