from pydantic import BaseModel
from typing import Any, Dict

class Event(BaseModel):
    """
    Data contract for all events flowing through the EventBus.

    Attributes:
        event_type (str): The type of the event (e.g., "USER_MESSAGE_SENT").
        payload (Dict[str, Any]): The data associated with the event.
    """
    event_type: str
    payload: Dict[str, Any] = {}