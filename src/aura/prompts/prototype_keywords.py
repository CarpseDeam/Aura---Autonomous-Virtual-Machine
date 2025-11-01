from typing import List

PROTOTYPE_KEYWORDS: List[str] = [
    "quick prototype",
    "just show me the pattern",
    "basic example",
    "simple version",
    "rough draft",
]


def matches_prototype_request(text: str) -> bool:
    """
    Determine whether the provided text requests prototype mode.

    Args:
        text: Arbitrary user text to inspect.

    Returns:
        True if any keyword indicating prototype mode is present, otherwise False.
    """
    if not isinstance(text, str):
        return False
    lowered = text.lower()
    return any(keyword in lowered for keyword in PROTOTYPE_KEYWORDS)
