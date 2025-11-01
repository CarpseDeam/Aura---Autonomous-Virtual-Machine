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
