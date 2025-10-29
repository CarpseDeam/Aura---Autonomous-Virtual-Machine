from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ProjectContext(BaseModel):
    """Shared state snapshot for decision making and execution.

    Attributes:
        active_project: Name of the current project.
        active_files: List of known files in the project index.
        conversation_history: Chat history (role/content pairs plus optional metadata such as images).
        extras: Additional context bag for future needs.
    """

    active_project: Optional[str] = None
    active_files: List[str] = Field(default_factory=list)
    conversation_history: List[Dict[str, Any]] = Field(default_factory=list)
    extras: Dict[str, Any] = Field(default_factory=dict)
