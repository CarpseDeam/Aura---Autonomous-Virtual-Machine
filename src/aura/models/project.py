import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class Project(BaseModel):
    """Represents a persistent project with full conversation history."""

    name: str = Field(..., description="Project name (must be filesystem-safe)")
    root_path: str = Field(..., description="Absolute path to project root")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_active: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    conversation_history: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Full conversation history for this project"
    )
    active_files: List[str] = Field(
        default_factory=list,
        description="Files currently being worked on"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional project context (recent topics, preferences, etc.)"
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        """Ensure project names are non-empty."""
        if not value or not value.strip():
            logger.error("Attempted to create project with empty name.")
            raise ValueError("Project name must be a non-empty string.")
        return value

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class ProjectSummary(BaseModel):
    """Lightweight project info for listing/selection."""

    name: str = Field(..., description="Project name")
    root_path: str = Field(..., description="Absolute path to project root")
    last_active: datetime = Field(..., description="Timestamp of last activity")
    message_count: int = Field(..., ge=0, description="Number of conversation turns")
    recent_topics: List[str] = Field(default_factory=list, description="Recently discussed topics")
