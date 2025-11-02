from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel


class ActionType(str, Enum):
    DESIGN_BLUEPRINT = "design_blueprint"
    REFINE_CODE = "refine_code"
    SPAWN_AGENT = "spawn_agent"
    MONITOR_WORKSPACE = "monitor_workspace"
    INTEGRATE_RESULTS = "integrate_results"
    SIMPLE_REPLY = "simple_reply"
    RESEARCH = "research"
    DISCUSS = "discuss"
    LIST_FILES = "list_files"
    READ_FILE = "read_file"
    READ_TERMINAL_OUTPUT = "read_terminal_output"
    SEND_TO_TERMINAL = "send_to_terminal"
    # MCP actions
    MCP_START_SERVER = "mcp_start_server"
    MCP_STOP_SERVER = "mcp_stop_server"
    MCP_LIST_TOOLS = "mcp_list_tools"
    MCP_CALL_TOOL = "mcp_call_tool"
    MCP_SERVER_STATUS = "mcp_server_status"


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

