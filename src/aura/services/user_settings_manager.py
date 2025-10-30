import copy
import json
import logging
from pathlib import Path
from typing import Any, Dict, Tuple

from src.aura.config import AGENT_CONFIG, SETTINGS_FILE

logger = logging.getLogger(__name__)

DEFAULT_PREFERENCES = {
    "auto_accept_changes": True,
}


def _default_settings() -> Dict[str, Any]:
    return {
        "agents": copy.deepcopy(AGENT_CONFIG),
        "preferences": copy.deepcopy(DEFAULT_PREFERENCES),
    }


def load_user_settings() -> Dict[str, Any]:
    """
    Load user settings, providing backwards compatibility with legacy layouts.

    Returns:
        Dict with keys: 'agents' (dict) and 'preferences' (dict).
    """
    settings = _default_settings()

    if not SETTINGS_FILE.exists():
        return settings

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (IOError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read user settings from %s: %s", SETTINGS_FILE, exc)
        return settings

    if not isinstance(data, dict):
        logger.warning("User settings file %s does not contain a JSON object.", SETTINGS_FILE)
        return settings

    agents, preferences = _split_settings_payload(data)

    if agents:
        settings["agents"].update(agents)
    if preferences:
        settings["preferences"].update(preferences)

    return settings


def save_user_settings(settings: Dict[str, Any]) -> None:
    """
    Persist the combined user settings structure to disk.
    """
    payload = {
        "agents": settings.get("agents", {}),
        "preferences": settings.get("preferences", {}),
    }

    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=4)

    logger.info("User settings saved to %s", SETTINGS_FILE)


def update_user_preferences(updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge and persist preference updates.

    Returns:
        The full settings dict after update.
    """
    settings = load_user_settings()
    prefs = settings.setdefault("preferences", {})
    prefs.update(updates or {})
    save_user_settings(settings)
    return settings


def update_agent_settings(agent_updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge and persist agent configuration updates.

    Returns:
        The full settings dict after update.
    """
    settings = load_user_settings()
    agents = settings.setdefault("agents", {})
    agents.update(agent_updates or {})
    save_user_settings(settings)
    return settings


def _split_settings_payload(
    payload: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Normalize mixed legacy/new payloads into separate agent and preference dicts.

    Legacy format stored agents at the top level. New format keeps them under
    'agents' and preferences under 'preferences'. This helper extracts both.
    """
    agents: Dict[str, Any] = {}
    preferences: Dict[str, Any] = {}

    if "agents" in payload or "preferences" in payload:
        agents_section = payload.get("agents", {})
        if isinstance(agents_section, dict):
            agents.update(agents_section)

        prefs_section = payload.get("preferences", {})
        if isinstance(prefs_section, dict):
            preferences.update(prefs_section)
    else:
        # Heuristic: treat dict entries that look like known agent configs as agents.
        for key, value in payload.items():
            if isinstance(value, dict):
                agents[key] = value
            else:
                # Non-dict entries are ignored but logged for visibility.
                logger.debug("Ignoring non-dict entry '%s' in legacy settings payload.", key)

    return agents, preferences


def get_auto_accept_changes() -> bool:
    """
    Convenience accessor for the auto-accept preference.
    """
    settings = load_user_settings()
    preferences = settings.get("preferences") or {}
    return bool(preferences.get("auto_accept_changes", True))

