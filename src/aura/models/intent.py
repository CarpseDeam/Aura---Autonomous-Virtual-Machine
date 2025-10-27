from enum import Enum


class Intent(str, Enum):
    """
    Enumeration for the user's detected intent.
    This is the core of the "Cognitive Router".
    """
    CHITCHAT = "CHITCHAT"
    PLANNING_SESSION = "PLANNING_SESSION"
    UNKNOWN = "UNKNOWN"
