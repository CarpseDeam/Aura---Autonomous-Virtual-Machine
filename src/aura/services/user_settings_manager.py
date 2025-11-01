import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from src.aura.config import SETTINGS_FILE

logger = logging.getLogger(__name__)

# Friendly display labels paired with internal identifiers for Aura's brain model.
AURA_BRAIN_MODEL_CHOICES: List[Tuple[str, str]] = [
    ("claude-sonnet-4-5", "Claude Sonnet 4.5"),
    ("claude-opus-4", "Claude Opus 4"),
    ("gpt-5", "GPT-5 (ChatGPT)"),
    ("gemini-2.0", "Gemini 2.0"),
    ("gemini-1.5-pro", "Gemini 1.5 Pro"),
    ("ollama-local", "Ollama (local model)"),
]

# Preset terminal agent options with command templates that users can select from.
TERMINAL_AGENT_PRESETS: Dict[str, Dict[str, str]] = {
    "codex": {
        "label": "Codex (GPT-5)",
        "command_template": (
            "Write-Host 'Aura Agent Task {task_id}'; "
            "Write-Host ''; "
            "Get-Content '{spec_path}'; "
            "Write-Host ''; "
            "Write-Host 'Press any key to continue...'; "
            "$null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')"
        ),
    },
    "claude_code": {
        "label": "Claude Code",
        "command_template": "claude-code --prompt-file \"{spec_path}\"",
    },
}

# Default command template used when the selection cannot be resolved.
DEFAULT_TERMINAL_COMMAND_TEMPLATE = TERMINAL_AGENT_PRESETS["codex"]["command_template"]

# Baseline API key structure for the simplified settings payload.
DEFAULT_API_KEYS = {
    "anthropic": "",
    "openai": "",
    "google": "",
}


def _default_settings() -> Dict[str, Any]:
    return {
        "aura_brain_model": AURA_BRAIN_MODEL_CHOICES[0][0],
        "terminal_agent": "codex",
        "terminal_agent_custom_command": "",
        "api_keys": DEFAULT_API_KEYS.copy(),
        "auto_accept_changes": True,
    }


def _normalize_brain_model(value: Optional[str], fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    value = value.strip()
    valid_ids = {identifier for identifier, _ in AURA_BRAIN_MODEL_CHOICES}
    return value if value in valid_ids else fallback


def _normalize_terminal_selection(value: Any) -> str:
    if isinstance(value, str):
        selection = value.strip().lower()
        aliases = {
            "claude code": "claude_code",
            "claude-code": "claude_code",
            "codex (gpt-5)": "codex",
        }
        selection = aliases.get(selection, selection)
        if selection in TERMINAL_AGENT_PRESETS or selection == "custom":
            return "custom" if selection == "custom" else selection
    return "codex"


def _infer_terminal_preset_from_command(command: Optional[str]) -> str:
    if not command:
        return "codex"
    for key, preset in TERMINAL_AGENT_PRESETS.items():
        if command.strip() == preset["command_template"].strip():
            return key
    return "custom"


def _sanitize_api_keys(api_keys: Any) -> Dict[str, str]:
    sanitized = DEFAULT_API_KEYS.copy()
    if isinstance(api_keys, dict):
        for key in sanitized.keys():
            value = api_keys.get(key)
            if isinstance(value, str):
                sanitized[key] = value.strip()
    return sanitized


def load_user_settings() -> Dict[str, Any]:
    """
    Load user settings from disk, normalizing legacy payloads into the new simplified format.
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

    # Aura brain model
    brain_model = data.get("aura_brain_model")
    if brain_model is None and isinstance(data.get("agents"), dict):
        # Legacy payload – derive from the first agent that declares a model.
        for agent_config in data["agents"].values():
            if isinstance(agent_config, dict):
                candidate = agent_config.get("model")
                if isinstance(candidate, str) and candidate.strip():
                    brain_model = candidate
                    break
    settings["aura_brain_model"] = _normalize_brain_model(brain_model, settings["aura_brain_model"])

    # Terminal agent selection and custom command.
    custom_command = ""
    terminal_section = data.get("terminal_agent")
    if isinstance(terminal_section, str):
        settings["terminal_agent"] = _normalize_terminal_selection(terminal_section)
    elif isinstance(terminal_section, dict):
        command_template = terminal_section.get("command_template")
        settings["terminal_agent"] = _infer_terminal_preset_from_command(command_template)
        custom_command = command_template or ""
    else:
        settings["terminal_agent"] = "codex"

    if not custom_command:
        legacy_custom = data.get("terminal_agent_custom_command")
        if isinstance(legacy_custom, str):
            custom_command = legacy_custom
    settings["terminal_agent_custom_command"] = custom_command.strip()

    # API keys
    api_keys = data.get("api_keys")
    if api_keys is None:
        # Legacy placements may store keys under preferences.
        api_keys = (data.get("preferences") or {}).get("api_keys")
    settings["api_keys"] = _sanitize_api_keys(api_keys)

    # Auto-accept preference
    auto_accept = data.get("auto_accept_changes")
    if auto_accept is None:
        auto_accept = (data.get("preferences") or {}).get("auto_accept_changes")
    settings["auto_accept_changes"] = bool(auto_accept) if auto_accept is not None else settings["auto_accept_changes"]

    return settings


def save_user_settings(settings: Dict[str, Any]) -> None:
    """
    Persist the simplified user settings payload to disk.
    """
    normalized = _default_settings()
    normalized.update({k: v for k, v in settings.items() if k in normalized})

    payload: Dict[str, Any] = {
        "aura_brain_model": _normalize_brain_model(
            normalized.get("aura_brain_model"),
            _default_settings()["aura_brain_model"],
        ),
        "terminal_agent": _normalize_terminal_selection(normalized.get("terminal_agent")),
        "api_keys": _sanitize_api_keys(normalized.get("api_keys")),
        "auto_accept_changes": bool(normalized.get("auto_accept_changes", True)),
    }

    custom_command = (normalized.get("terminal_agent_custom_command") or "").strip()
    if payload["terminal_agent"] == "custom":
        payload["terminal_agent_custom_command"] = custom_command

    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=4)

    logger.info("User settings saved to %s", SETTINGS_FILE)


def update_user_preferences(updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge and persist preference updates while preserving the simplified structure.
    """
    settings = load_user_settings()
    if "auto_accept_changes" in updates:
        settings["auto_accept_changes"] = bool(updates.get("auto_accept_changes"))
    if "api_keys" in updates:
        settings["api_keys"] = _sanitize_api_keys(updates.get("api_keys"))

    save_user_settings(settings)
    return settings


def update_agent_settings(agent_updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update the Aura brain model or terminal agent selection.
    """
    settings = load_user_settings()

    if "aura_brain_model" in agent_updates:
        settings["aura_brain_model"] = _normalize_brain_model(
            agent_updates.get("aura_brain_model"),
            settings["aura_brain_model"],
        )

    if "terminal_agent" in agent_updates:
        settings["terminal_agent"] = _normalize_terminal_selection(agent_updates.get("terminal_agent"))

    if "terminal_agent_custom_command" in agent_updates:
        settings["terminal_agent_custom_command"] = str(agent_updates.get("terminal_agent_custom_command") or "").strip()

    save_user_settings(settings)
    return settings


def get_auto_accept_changes() -> bool:
    """
    Convenience accessor for the auto-accept preference.
    """
    settings = load_user_settings()
    return bool(settings.get("auto_accept_changes", True))


def get_terminal_agent_command_template(settings: Optional[Dict[str, Any]] = None) -> str:
    """
    Resolve the command template string that should be used when spawning the terminal agent.
    """
    if settings is None:
        settings = load_user_settings()

    selection = (settings.get("terminal_agent") or "").strip().lower()
    if selection == "custom":
        custom = str(settings.get("terminal_agent_custom_command") or "").strip()
        if custom:
            return custom
        logger.debug("Custom terminal agent selected without command; falling back to default template.")
        return DEFAULT_TERMINAL_COMMAND_TEMPLATE

    preset = TERMINAL_AGENT_PRESETS.get(selection)
    if preset:
        return preset["command_template"]

    legacy = settings.get("terminal_agent")
    if isinstance(legacy, dict):
        command_template = legacy.get("command_template")
        if isinstance(command_template, str) and command_template.strip():
            return command_template

    return DEFAULT_TERMINAL_COMMAND_TEMPLATE
