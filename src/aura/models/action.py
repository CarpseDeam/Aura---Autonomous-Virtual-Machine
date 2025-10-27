from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel


class ActionType(str, Enum):
    DESIGN_BLUEPRINT = "design_blueprint"
    REFINE_CODE = "refine_code"
    SIMPLE_REPLY = "simple_reply"


class Action(BaseModel):
    """Decision unit produced by the Brain layer.

    Attributes:
        type: High-level action type to execute.
        params: Arbitrary parameters needed by the executor.
    """

    type: ActionType
    params: Dict[str, Any] = {}

    def get_param(self, key: str, default: Optional[Any] = None) -> Any:
        return self.params.get(key, default)

