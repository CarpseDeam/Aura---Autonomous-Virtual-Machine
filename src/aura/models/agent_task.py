from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AgentSpecification(BaseModel):
    """Specification document provided to an external coding agent."""

    task_id: str
    request: str
    project_name: Optional[str] = None
    blueprint: Dict[str, Any] = Field(default_factory=dict)
    prompt: str
    files_to_watch: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TerminalSession(BaseModel):
    """Represents a spawned terminal agent session."""

    task_id: str
    command: List[str]
    spec_path: str
    process_id: Optional[int] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)


class TaskSummary(BaseModel):
    """Structured summary written by terminal agents upon completion.

    Fields mirror the expected `.aura/{task_id}.summary.json` structure.
    """

    status: str = Field(description="completed | failed | partial")
    files_created: List[str] = Field(default_factory=list)
    files_modified: List[str] = Field(default_factory=list)
    files_deleted: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    execution_time_seconds: Optional[float] = Field(default=None)
    suggestions: List[str] = Field(default_factory=list)

    def short_outcome(self) -> str:
        """Return a compact human-readable outcome label."""
        created = len(self.files_created)
        modified = len(self.files_modified)
        deleted = len(self.files_deleted)
        parts: List[str] = []
        if created:
            parts.append(f"created {created}")
        if modified:
            parts.append(f"modified {modified}")
        if deleted:
            parts.append(f"deleted {deleted}")
        files_part = ", ".join(parts) if parts else "no file changes"
        return f"{self.status}: {files_part}"
