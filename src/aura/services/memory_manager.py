"""Project memory management service.

This service maintains PROJECT_MEMORY.md files that capture:
- Architectural decisions and patterns
- Project timeline and feature history
- Current state and known issues
- Context for AI to maintain consistency across sessions
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .memory_blueprint_parser import BlueprintParser
from .memory_markdown import MemoryMarkdownGenerator
from .memory_models import (
    ArchitectureDecision,
    CodePattern,
    KnownIssue,
    ProjectMemory,
    TimelineEntry,
)
from .memory_updater import apply_blueprint_result, refresh_project_metrics

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    Manages project memory documents that persist architectural decisions,
    patterns, and project history across sessions.

    This service:
    - Subscribes to completion events (blueprint, terminal, integration)
    - Updates PROJECT_MEMORY.md with significant milestones
    - Maintains structured memory in Project.metadata
    - Generates human-readable markdown documentation
    """

    def __init__(
        self,
        project_manager: Any,
        event_bus: Optional[Any] = None
    ) -> None:
        """
        Initialize memory manager.

        Args:
            project_manager: ProjectManager instance for loading/saving projects
            event_bus: Optional event bus for subscribing to events
        """
        self.project_manager = project_manager
        self.event_bus = event_bus
        self.markdown_generator = MemoryMarkdownGenerator()
        self.blueprint_parser = BlueprintParser()

        # Register event handlers if event bus is available
        if self.event_bus:
            self._register_event_handlers()
            logger.info("MemoryManager initialized with event bus subscriptions")
        else:
            logger.warning("MemoryManager initialized without event bus")

    def _register_event_handlers(self) -> None:
        """Register event handlers for memory updates."""
        from src.aura.models.event_types import (
            BLUEPRINT_GENERATED,
            TERMINAL_SESSION_COMPLETED,
            TRIGGER_AUTO_INTEGRATE,
            PROJECT_ACTIVATED,
        )

        self.event_bus.subscribe(BLUEPRINT_GENERATED, self._on_blueprint_generated)
        self.event_bus.subscribe(TERMINAL_SESSION_COMPLETED, self._on_session_completed)
        self.event_bus.subscribe(TRIGGER_AUTO_INTEGRATE, self._on_auto_integrate)
        self.event_bus.subscribe(PROJECT_ACTIVATED, self._on_project_activated)

        logger.debug("Registered event handlers: BLUEPRINT_GENERATED, TERMINAL_SESSION_COMPLETED, TRIGGER_AUTO_INTEGRATE, PROJECT_ACTIVATED")

    def get_memory(self) -> Optional[ProjectMemory]:
        """
        Load project memory from current project metadata.

        Returns:
            ProjectMemory instance or None if not available
        """
        if not self.project_manager.current_project:
            logger.debug("No current project - cannot load memory")
            return None

        project = self.project_manager.current_project
        memory_data = project.metadata.get("project_memory")

        if not memory_data:
            logger.debug("No memory data in project metadata - initializing new memory")
            return self._initialize_memory(project.name)

        try:
            memory = ProjectMemory(**memory_data)
            logger.debug("Loaded project memory for '%s' with %d timeline entries",
                        project.name, len(memory.timeline))
            return memory
        except Exception as exc:
            logger.error("Failed to parse project memory: %s", exc)
            logger.warning("Reinitializing project memory due to parse error")
            return self._initialize_memory(project.name)

    def save_memory(self, memory: ProjectMemory) -> None:
        """
        Save project memory to current project metadata and generate markdown file.

        Args:
            memory: ProjectMemory instance to save
        """
        if not self.project_manager.current_project:
            logger.warning("Cannot save memory - no current project")
            return

        project = self.project_manager.current_project

        # Update timestamp
        memory.last_updated = datetime.now(timezone.utc)

        # Save to project metadata
        try:
            if hasattr(memory, "model_dump"):
                memory_data = memory.model_dump()
            else:
                memory_data = memory.dict()

            project.metadata["project_memory"] = memory_data
            self.project_manager.save_project(project)
            logger.debug("Saved memory to project metadata for '%s'", project.name)
        except Exception as exc:
            logger.error("Failed to save memory to project metadata: %s", exc)
            return

        # Generate and save markdown file
        try:
            self._generate_markdown_file(memory)
        except Exception as exc:
            logger.error("Failed to generate PROJECT_MEMORY.md: %s", exc)

    def add_architecture_decision(
        self,
        category: str,
        decision: str,
        rationale: str
    ) -> None:
        """
        Record an architectural decision.

        Args:
            category: Category (framework, database, auth, etc.)
            decision: What was chosen
            rationale: Why it was chosen
        """
        memory = self.get_memory()
        if not memory:
            return

        arch_decision = ArchitectureDecision(
            category=category,
            decision=decision,
            rationale=rationale
        )

        memory.architecture_decisions.append(arch_decision)
        self.save_memory(memory)

        logger.info("Recorded architecture decision: %s -> %s", category, decision)

    def add_code_pattern(
        self,
        category: str,
        pattern: str,
        example: Optional[str] = None
    ) -> None:
        """
        Record a code pattern or convention.

        Args:
            category: Category (validation, error handling, etc.)
            pattern: The pattern description
            example: Optional code example
        """
        memory = self.get_memory()
        if not memory:
            return

        code_pattern = CodePattern(
            category=category,
            pattern=pattern,
            example=example
        )

        memory.code_patterns.append(code_pattern)
        self.save_memory(memory)

        logger.info("Recorded code pattern: %s", category)

    def add_timeline_entry(
        self,
        task_id: str,
        description: str,
        outcome: str = "success",
        files_modified: Optional[List[str]] = None,
        notes: Optional[str] = None
    ) -> None:
        """
        Add a timeline entry for a significant event.

        Args:
            task_id: Associated task ID
            description: What was accomplished
            outcome: success, failure, partial, etc.
            files_modified: List of modified files
            notes: Additional context
        """
        memory = self.get_memory()
        if not memory:
            return

        entry = TimelineEntry(
            task_id=task_id,
            description=description,
            outcome=outcome,
            files_modified=files_modified or [],
            notes=notes
        )

        memory.timeline.append(entry)
        self.save_memory(memory)

        logger.info("Added timeline entry: %s (%s)", description, outcome)

    def add_known_issue(
        self,
        description: str,
        severity: str = "medium"
    ) -> None:
        """
        Record a known issue or technical debt.

        Args:
            description: Issue description
            severity: low, medium, high, critical
        """
        memory = self.get_memory()
        if not memory:
            return

        issue = KnownIssue(
            description=description,
            severity=severity
        )

        memory.known_issues.append(issue)
        self.save_memory(memory)

        logger.info("Recorded known issue: %s [%s]", description, severity)

    def update_project_state(self, state_updates: Dict[str, Any]) -> None:
        """
        Update current project state.

        Args:
            state_updates: Dictionary of state updates to apply
        """
        memory = self.get_memory()
        if not memory:
            return

        memory.current_state.update(state_updates)
        refresh_project_metrics(self.project_manager, memory)
        self.save_memory(memory)

        logger.debug("Updated project state: %s", list(state_updates.keys()))

    def get_memory_context(self) -> str:
        """
        Get formatted memory context for inclusion in AI prompts.

        Returns:
            Formatted markdown string with project memory
        """
        memory = self.get_memory()
        if not memory:
            return ""

        # Return the markdown representation
        return self.markdown_generator.generate_content(memory)

    def _initialize_memory(self, project_name: str) -> ProjectMemory:
        """
        Initialize new project memory.

        Args:
            project_name: Name of the project

        Returns:
            New ProjectMemory instance
        """
        memory = ProjectMemory(project_name=project_name)
        logger.info("Initialized new project memory for '%s'", project_name)
        return memory

    def _generate_markdown_file(self, memory: ProjectMemory) -> None:
        """
        Generate PROJECT_MEMORY.md file on disk.

        Args:
            memory: ProjectMemory instance
        """
        if not self.project_manager.current_project:
            return

        project_path = self.project_manager.get_project_path(
            self.project_manager.current_project.name
        )

        try:
            self.markdown_generator.generate_file(memory, project_path)
        except OSError as exc:
            logger.error("Failed to generate PROJECT_MEMORY.md: %s", exc)

    # Event Handlers

    def _on_blueprint_generated(self, event: Any) -> None:
        """
        Handle BLUEPRINT_GENERATED event.

        Extracts architectural decisions from the blueprint and records them.

        Args:
            event: Event object with payload containing blueprint
        """
        try:
            payload = event.payload or {}
            task_id = payload.get("task_id", "unknown")

            logger.debug("Processing BLUEPRINT_GENERATED event for task %s", task_id)

            memory = self.get_memory()
            if not memory:
                return

            has_blueprint_content = any(
                isinstance(payload.get(key), (str, dict, list))
                for key in ("blueprint", "prompt")
            ) or isinstance((payload.get("metadata") or {}).get("blueprint_markdown"), str)

            if not has_blueprint_content:
                logger.debug("BLUEPRINT_GENERATED payload contained no blueprint content")
                return

            apply_blueprint_result(memory, payload, task_id, self.blueprint_parser)
            refresh_project_metrics(self.project_manager, memory)
            self.save_memory(memory)

        except Exception as exc:
            logger.error("Error processing BLUEPRINT_GENERATED event: %s", exc)

    def _on_session_completed(self, event: Any) -> None:
        """
        Handle TERMINAL_SESSION_COMPLETED event.

        Records completion in timeline with outcome and changes.

        Args:
            event: Event object with payload containing session details
        """
        try:
            payload = event.payload or {}
            task_id = payload.get("task_id", "unknown")
            completion_reason = payload.get("completion_reason", "completed")
            changes_made = payload.get("changes_made", 0)

            logger.debug("Processing TERMINAL_SESSION_COMPLETED for task %s", task_id)

            memory = self.get_memory()
            if not memory:
                return

            description = f"Terminal session completed: {completion_reason}"
            notes = f"{changes_made} file changes detected" if changes_made > 0 else None

            memory.timeline.append(
                TimelineEntry(
                    task_id=task_id,
                    description=description,
                    outcome="success",
                    notes=notes,
                )
            )

            memory.current_state.update({
                "last_terminal_session": task_id,
                "last_completion_time": datetime.now(timezone.utc).isoformat()
            })
            if changes_made is not None:
                memory.current_state["last_changes_detected"] = changes_made

            refresh_project_metrics(self.project_manager, memory)
            self.save_memory(memory)

        except Exception as exc:
            logger.error("Error processing TERMINAL_SESSION_COMPLETED event: %s", exc)

    def _on_auto_integrate(self, event: Any) -> None:
        """
        Handle TRIGGER_AUTO_INTEGRATE event.

        This is triggered after session completion for result integration.

        Args:
            event: Event object with payload containing task_id
        """
        try:
            payload = event.payload or {}
            task_id = payload.get("task_id", "unknown")

            logger.debug("Processing TRIGGER_AUTO_INTEGRATE for task %s", task_id)

            memory = self.get_memory()
            if not memory:
                return

            memory.current_state.update({
                "last_integration": task_id,
                "integration_time": datetime.now(timezone.utc).isoformat()
            })
            refresh_project_metrics(self.project_manager, memory)
            self.save_memory(memory)

        except Exception as exc:
            logger.error("Error processing TRIGGER_AUTO_INTEGRATE event: %s", exc)

    def _on_project_activated(self, event: Any) -> None:
        """
        Handle PROJECT_ACTIVATED event.

        Ensures memory is initialized when a project is activated.

        Args:
            event: Event object with payload containing project details
        """
        try:
            logger.debug("Processing PROJECT_ACTIVATED event")

            # Ensure memory is initialized
            memory = self.get_memory()
            if memory:
                logger.info("Project memory loaded for '%s'", memory.project_name)

        except Exception as exc:
            logger.error("Error processing PROJECT_ACTIVATED event: %s", exc)
