"""Pydantic models for project memory system.

This module contains all data models used by the MemoryManager service.
Each model represents a specific aspect of project memory.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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
