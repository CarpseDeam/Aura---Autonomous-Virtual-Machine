from pathlib import Path

# This calculates the absolute path to the project's root directory
# It starts from this file's location (.../src/aura/config.py) and goes up three levels.
ROOT_DIR = Path(__file__).resolve().parent.parent.parent

# All other important paths are built from the ROOT_DIR to ensure they are always correct.
ASSETS_DIR = ROOT_DIR / "assets"
LOGS_DIR = ROOT_DIR / "logs"
SETTINGS_FILE = ROOT_DIR / "user_settings.json"
WORKSPACE_DIR = ROOT_DIR / "workspace"

# Agent configuration for the simplified architecture.
AGENT_CONFIG = {
    "architect_agent": {
        "temperature": 0.05,  # Deterministic coding output
        "top_p": 0.9,
    },
}
