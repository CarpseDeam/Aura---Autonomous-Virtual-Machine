import uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Session(BaseModel):
    """
    Represents a single, isolated conversation session.

    Each session has a unique ID and maintains its own conversation history,
    allowing the AI to manage multiple contexts simultaneously.

    Attributes:
        id: A unique identifier for the session.
        project_name: Name of the workspace project this session belongs to.
        title: Optional human-readable title derived from the first user prompt.
        created_at: ISO8601 timestamp of when the session was created.
        updated_at: ISO8601 timestamp of the most recent update.
        is_active: Whether the session is the current active thread for its project.
        history: A list of message dictionaries, where each dictionary
                 contains a 'role', 'content', and optional metadata such as images.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_name: str = "default_project"
    title: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    is_active: bool = True
    history: List[Dict[str, Any]] = Field(default_factory=list)
