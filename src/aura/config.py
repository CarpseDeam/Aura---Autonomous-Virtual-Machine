from pathlib import Path

# This calculates the absolute path to the project's root directory
# It starts from this file's location (.../src/aura/config.py) and goes up three levels.
ROOT_DIR = Path(__file__).resolve().parent.parent.parent

# All other important paths are built from the ROOT_DIR to ensure they are always correct.
ASSETS_DIR = ROOT_DIR / "assets"
LOGS_DIR = ROOT_DIR / "logs"

# Agent Configurations
AGENT_CONFIG = {
    "cognitive_router": {
        "model": "gemini-2.5-pro",
        "temperature": 0.2,
        "top_p": 0.8,
    },
    "default_streaming": {
        "model": "gemini-2.5-pro",
        "temperature": 0.7,
        "top_p": 1.0,
    },
    "engineer_agent": {
        "model": "gemini-2.5-pro",
        "temperature": 0.1,
        "top_p": 1.0,
    }
}
