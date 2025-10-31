from pathlib import Path

# This calculates the absolute path to the project's root directory
# It starts from this file's location (.../src/aura/config.py) and goes up three levels.
ROOT_DIR = Path(__file__).resolve().parent.parent.parent

# All other important paths are built from the ROOT_DIR to ensure they are always correct.
ASSETS_DIR = ROOT_DIR / "assets"
LOGS_DIR = ROOT_DIR / "logs"
SETTINGS_FILE = ROOT_DIR / "user_settings.json"
WORKSPACE_DIR = ROOT_DIR / "workspace"

# Agent Configurations
# In config.py - Lower temperatures for more consistent professional code
AGENT_CONFIG = {
    "architect_agent": {
        "temperature": 0.05,  # Very low for consistent architecture
        "top_p": 0.9,
    },
    "engineer_agent": {
        "temperature": 0.1,   # Low for consistent implementation
        "top_p": 0.9,
    },
    "lead_companion_agent": {
        "temperature": 0.2,   # Slightly higher for creative problem solving
        "top_p": 0.95,
    },
    "reasoning_agent": {
        "temperature": 0.0,
        "top_p": 1.0,
    },
    "intent_detection_agent": {
        "temperature": 0.0,
        "top_p": 1.0,
    },
    "cognitive_router": {
        "temperature": 0.0,  # Deterministic routing decisions
        "top_p": 1.0,
    },
}
