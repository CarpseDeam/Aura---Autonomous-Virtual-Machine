"""Shared helpers for updating project memory state."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .memory_blueprint_parser import BlueprintParser
from .memory_models import (
    ArchitectureDecision,
    CodePattern,
    KnownIssue,
    ProjectMemory,
    TimelineEntry,
)

logger = logging.getLogger(__name__)


def apply_blueprint_result(
    memory: ProjectMemory,
    payload: Dict[str, Any],
    task_id: str,
    parser: BlueprintParser,
) -> None:
    """Parse blueprint payload and merge findings into project memory."""
    try:
        result = parser.parse(payload)
    except Exception as exc:
        logger.error("Failed to parse blueprint payload: %s", exc)
        return

    if result.decisions:
        existing = {
            (decision.category.lower(), decision.decision.lower()): decision
            for decision in memory.architecture_decisions
        }
        for parsed in result.decisions:
            key = (parsed.category.lower(), parsed.decision.lower())
            match = existing.get(key)
            if match:
                if parsed.rationale and parsed.rationale != match.rationale:
                    match.rationale = parsed.rationale
                continue
            decision_model = ArchitectureDecision(
                category=parsed.category,
                decision=parsed.decision,
                rationale=parsed.rationale,
            )
            memory.architecture_decisions.append(decision_model)
            existing[key] = decision_model

    if result.patterns:
        pattern_index = {
            (pattern.category.lower(), pattern.pattern.lower()): pattern
            for pattern in memory.code_patterns
        }
        for parsed in result.patterns:
            key = (parsed.category.lower(), parsed.description.lower())
            match = pattern_index.get(key)
            if match:
                if parsed.example and not match.example:
                    match.example = parsed.example
                continue
            pattern_model = CodePattern(
                category=parsed.category,
                pattern=parsed.description,
                example=parsed.example,
            )
            memory.code_patterns.append(pattern_model)
            pattern_index[key] = pattern_model

    if result.issues:
        known_descriptions = {
            issue.description.lower()
            for issue in memory.known_issues
            if not issue.resolved_at
        }
        for parsed in result.issues:
            key = parsed.description.lower()
            if key in known_descriptions:
                continue
            memory.known_issues.append(
                KnownIssue(
                    description=parsed.description,
                    severity=parsed.severity,
                )
            )
            known_descriptions.add(key)

    if result.state_updates:
        result.state_updates["last_blueprint_task"] = task_id
        memory.current_state.update(result.state_updates)

    notes_parts: List[str] = []
    if result.summary_notes:
        notes_parts.extend(result.summary_notes)
    if result.next_steps:
        notes_parts.append("Next steps: " + "; ".join(result.next_steps[:5]))

    memory.timeline.append(
        TimelineEntry(
            task_id=task_id,
            description="Design blueprint generated",
            outcome="success",
            notes="; ".join(notes_parts) if notes_parts else None,
        )
    )


def refresh_project_metrics(project_manager: Any, memory: ProjectMemory) -> None:
    """Compute lightweight filesystem metrics for the active project."""
    project = getattr(project_manager, "current_project", None)
    if project is None:
        return

    root_path = getattr(project, "root_path", None)
    if not root_path:
        return

    try:
        root = Path(root_path).expanduser()
    except Exception as exc:
        logger.debug("Invalid project root path: %s", exc)
        return

    if not root.exists():
        return

    try:
        file_count = sum(
            1
            for path in root.rglob("*")
            if path.is_file() and ".aura_backups" not in path.parts
        )
    except OSError as exc:
        logger.debug("Unable to compute project file metrics: %s", exc)
        return

    memory.current_state["total_files"] = file_count
    memory.current_state["last_state_refresh"] = datetime.now(timezone.utc).isoformat()
