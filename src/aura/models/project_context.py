from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class ProjectContext(BaseModel):
    """Shared state snapshot for decision making and execution.

    Attributes:
        active_project: Name of the current project.
        active_files: List of known files in the project index.
        conversation_history: Chat history (role/content pairs).
        extras: Additional context bag for future needs.
    """

    active_project: Optional[str] = None
    active_files: List[str] = []
    conversation_history: List[Dict[str, str]] = []
    extras: Dict[str, Any] = {}

