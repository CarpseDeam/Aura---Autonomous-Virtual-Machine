import uuid
from typing import Any, Dict, List
from pydantic import BaseModel, Field


class Session(BaseModel):
    """
    Represents a single, isolated conversation session.

    Each session has a unique ID and maintains its own conversation history,
    allowing the AI to manage multiple contexts simultaneously.

    Attributes:
        id: A unique identifier for the session.
        history: A list of message dictionaries, where each dictionary
                 contains a 'role', 'content', and optional metadata such as images.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    history: List[Dict[str, Any]] = Field(default_factory=list)
