from typing import Any, Dict, Optional
from pydantic import BaseModel


class Result(BaseModel):
    """Execution outcome returned by the Executor layer.

    Attributes:
        ok: Whether execution completed successfully.
        kind: A short label for the result (e.g., 'blueprint', 'code').
        data: Arbitrary payload with structured data.
        error: Optional error message.
    """

    ok: bool
    kind: str
    data: Dict[str, Any] = {}
    error: Optional[str] = None

