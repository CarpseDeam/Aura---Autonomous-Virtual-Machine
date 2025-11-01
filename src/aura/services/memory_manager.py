"""Project memory management service.

This service maintains PROJECT_MEMORY.md files that capture:
- Architectural decisions and patterns
- Project timeline and feature history
- Current state and known issues
- Context for AI to maintain consistency across sessions
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ArchitectureDecision(BaseModel):
    """Represents a significant architectural decision."""

    category: str = Field(..., description="Category (framework, database, auth, etc.)")
    decision: str = Field(..., description="What was chosen")
    rationale: str = Field(..., description="Why it was chosen")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CodePattern(BaseModel):
    """Represents a code pattern or convention used in the project."""

    category: str = Field(..., description="Category (validation, error handling, etc.)")
    pattern: str = Field(..., description="The pattern or convention")
    example: Optional[str] = Field(None, description="Optional code example")


class TimelineEntry(BaseModel):
    """Represents a project timeline event."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    task_id: str = Field(..., description="Associated task ID")
    description: str = Field(..., description="What was accomplished")
    files_modified: List[str] = Field(default_factory=list)
    outcome: str = Field(..., description="Success, failure, partial, etc.")
    notes: Optional[str] = Field(None, description="Additional context")


class KnownIssue(BaseModel):
    """Represents a known issue or technical debt item."""

    description: str = Field(..., description="Issue description")
    severity: str = Field("medium", description="low, medium, high, critical")
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = Field(None)


class ProjectMemory(BaseModel):
    """Complete project memory structure."""

    project_name: str = Field(..., description="Name of the project")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Core memory sections
    architecture_decisions: List[ArchitectureDecision] = Field(default_factory=list)
    code_patterns: List[CodePattern] = Field(default_factory=list)
    timeline: List[TimelineEntry] = Field(default_factory=list)
    known_issues: List[KnownIssue] = Field(default_factory=list)

    # Project state
    current_state: Dict[str, Any] = Field(
        default_factory=dict,
        description="Current project state (file count, status, next steps)"
    )


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
        return self._generate_markdown_content(memory)

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

    def _generate_markdown_content(self, memory: ProjectMemory) -> str:
        """
        Generate markdown content from memory structure.

        Args:
            memory: ProjectMemory instance

        Returns:
            Formatted markdown string
        """
        lines = [
            f"# Project Memory: {memory.project_name}",
            "",
            f"*Last updated: {memory.last_updated.strftime('%Y-%m-%d %H:%M UTC')}*",
            "",
        ]

        # Architecture Decisions
        if memory.architecture_decisions:
            lines.extend([
                "## Architecture Decisions",
                "",
            ])

            decisions_by_category: Dict[str, List[ArchitectureDecision]] = {}
            for decision in memory.architecture_decisions:
                if decision.category not in decisions_by_category:
                    decisions_by_category[decision.category] = []
                decisions_by_category[decision.category].append(decision)

            for category in sorted(decisions_by_category.keys()):
                lines.append(f"### {category}")
                for decision in decisions_by_category[category]:
                    lines.append(f"- **{decision.decision}**: {decision.rationale}")
                lines.append("")

        # Code Patterns
        if memory.code_patterns:
            lines.extend([
                "## Code Patterns We Follow",
                "",
            ])

            patterns_by_category: Dict[str, List[CodePattern]] = {}
            for pattern in memory.code_patterns:
                if pattern.category not in patterns_by_category:
                    patterns_by_category[pattern.category] = []
                patterns_by_category[pattern.category].append(pattern)

            for category in sorted(patterns_by_category.keys()):
                lines.append(f"### {category}")
                for pattern in patterns_by_category[category]:
                    lines.append(f"- {pattern.pattern}")
                    if pattern.example:
                        lines.extend([
                            "  ```",
                            f"  {pattern.example}",
                            "  ```",
                        ])
                lines.append("")

        # Project Timeline (most recent 20 entries)
        if memory.timeline:
            lines.extend([
                "## Project Timeline",
                "",
            ])

            # Sort by timestamp descending, take most recent 20
            sorted_timeline = sorted(memory.timeline, key=lambda e: e.timestamp, reverse=True)[:20]

            for entry in sorted_timeline:
                date_str = entry.timestamp.strftime('%Y-%m-%d')
                outcome_emoji = "✅" if entry.outcome == "success" else "⚠️" if entry.outcome == "partial" else "❌"
                lines.append(f"- **{date_str}** {outcome_emoji}: {entry.description}")
                if entry.notes:
                    lines.append(f"  - *{entry.notes}*")

            lines.append("")

        # Current State
        if memory.current_state:
            lines.extend([
                "## Current State",
                "",
            ])

            for key, value in memory.current_state.items():
                lines.append(f"- **{key}**: {value}")

            lines.append("")

        # Known Issues
        if memory.known_issues:
            lines.extend([
                "## Known Issues",
                "",
            ])

            # Group by severity
            unresolved = [issue for issue in memory.known_issues if not issue.resolved_at]

            if unresolved:
                by_severity = {"critical": [], "high": [], "medium": [], "low": []}
                for issue in unresolved:
                    severity = issue.severity.lower()
                    if severity in by_severity:
                        by_severity[severity].append(issue)

                for severity in ["critical", "high", "medium", "low"]:
                    if by_severity[severity]:
                        severity_emoji = {
                            "critical": "🔥",
                            "high": "🔴",
                            "medium": "🟡",
                            "low": "🟢"
                        }[severity]

                        lines.append(f"### {severity_emoji} {severity.title()}")
                        for issue in by_severity[severity]:
                            lines.append(f"- {issue.description}")
                        lines.append("")

        lines.extend([
            "---",
            "",
            "*This file is auto-generated by Aura's Project Memory System.*",
            "*Last sync: " + datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC') + "*",
        ])

        return "\n".join(lines)

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
        memory_file = project_path / "PROJECT_MEMORY.md"

        content = self._generate_markdown_content(memory)

        try:
            memory_file.write_text(content, encoding="utf-8")
            logger.info("Generated PROJECT_MEMORY.md at %s", memory_file)
        except OSError as exc:
            logger.error("Failed to write PROJECT_MEMORY.md: %s", exc)
            raise

    # Event Handlers

    def _on_blueprint_generated(self, event: Any) -> None:
        """
        Handle BLUEPRINT_GENERATED event.

        Extracts architectural decisions from the blueprint and records them.

        Args:
            event: Event object with payload containing blueprint
        """
        try:
            payload = event.payload
            task_id = payload.get("task_id", "unknown")

            logger.debug("Processing BLUEPRINT_GENERATED event for task %s", task_id)

            # Extract blueprint if available
            blueprint_text = payload.get("blueprint", "")
            if not blueprint_text:
                logger.debug("No blueprint text in event payload")
                return

            # Add timeline entry for blueprint generation
            self.add_timeline_entry(
                task_id=task_id,
                description="Design blueprint generated",
                outcome="success",
                notes="Initial project architecture defined"
            )

            # TODO: In future, parse blueprint to extract architecture decisions
            # For now, just log that a blueprint was created

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
            payload = event.payload
            task_id = payload.get("task_id", "unknown")
            completion_reason = payload.get("completion_reason", "completed")
            changes_made = payload.get("changes_made", 0)

            logger.debug("Processing TERMINAL_SESSION_COMPLETED for task %s", task_id)

            # Add timeline entry
            description = f"Terminal session completed: {completion_reason}"
            notes = f"{changes_made} file changes detected" if changes_made > 0 else None

            self.add_timeline_entry(
                task_id=task_id,
                description=description,
                outcome="success",
                notes=notes
            )

            # Update project state
            self.update_project_state({
                "last_terminal_session": task_id,
                "last_completion_time": datetime.now(timezone.utc).isoformat()
            })

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
            payload = event.payload
            task_id = payload.get("task_id", "unknown")

            logger.debug("Processing TRIGGER_AUTO_INTEGRATE for task %s", task_id)

            # Update state to reflect integration phase
            self.update_project_state({
                "last_integration": task_id,
                "integration_time": datetime.now(timezone.utc).isoformat()
            })

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
