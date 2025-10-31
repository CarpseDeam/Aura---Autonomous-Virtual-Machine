from enum import Enum


class Intent(str, Enum):
    """Supported user intent categories used by Aura when selecting actions."""

    CASUAL_CHAT = "casual_chat"
    SEEKING_ADVICE = "seeking_advice"
    BRAINSTORM = "brainstorm"
    BUILD_VAGUE = "build_vague"
    BUILD_CLEAR = "build_clear"
