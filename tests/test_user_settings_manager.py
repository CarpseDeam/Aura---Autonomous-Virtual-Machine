"""Tests for UserSettingsManager - settings persistence and migration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

from src.aura.services.user_settings_manager import (
    TERMINAL_AGENT_PRESETS,
    DEFAULT_TERMINAL_COMMAND_TEMPLATE,
    load_user_settings,
    save_user_settings,
    update_user_preferences,
    update_agent_settings,
    get_auto_accept_changes,
    get_terminal_agent_command_template,
)


@pytest.fixture
def temp_settings_file(tmp_path: Path) -> Path:
    """Create a temporary settings file location."""
    settings_file = tmp_path / "aura_settings.json"
    return settings_file


def _mock_settings_file(settings_file: Path) -> Any:
    """Create a context manager that patches SETTINGS_FILE."""
    return patch("src.aura.services.user_settings_manager.SETTINGS_FILE", settings_file)


# -- Loading settings tests ------------------------------------------------------------


def test_load_user_settings_returns_defaults_when_file_missing(temp_settings_file: Path) -> None:
    """Test that load_user_settings returns default settings when file doesn't exist."""
    with _mock_settings_file(temp_settings_file):
        settings = load_user_settings()

    assert "aura_brain_model" in settings
    assert "terminal_agent" in settings
    assert "api_keys" in settings
    assert "auto_accept_changes" in settings
    assert settings["terminal_agent"] == "codex"
    assert settings["auto_accept_changes"] is True


def test_load_user_settings_reads_valid_file(temp_settings_file: Path) -> None:
    """Test that load_user_settings correctly reads a valid settings file."""
    settings_data = {
        "aura_brain_model": "claude-opus-4",
        "terminal_agent": "claude_code",
        "api_keys": {
            "anthropic": "sk-ant-test123",
            "openai": "sk-test456",
            "google": "",
        },
        "auto_accept_changes": False,
    }

    temp_settings_file.write_text(json.dumps(settings_data), encoding="utf-8")

    with _mock_settings_file(temp_settings_file):
        settings = load_user_settings()

    assert settings["aura_brain_model"] == "claude-opus-4"
    assert settings["terminal_agent"] == "claude_code"
    assert settings["api_keys"]["anthropic"] == "sk-ant-test123"
    assert settings["auto_accept_changes"] is False


def test_load_user_settings_handles_malformed_json(temp_settings_file: Path) -> None:
    """Test that load_user_settings handles malformed JSON gracefully."""
    temp_settings_file.write_text("{ invalid json }", encoding="utf-8")

    with _mock_settings_file(temp_settings_file):
        settings = load_user_settings()

    # Should return defaults instead of crashing
    assert settings["terminal_agent"] == "codex"
    assert "api_keys" in settings


def test_load_user_settings_handles_non_dict_content(temp_settings_file: Path) -> None:
    """Test that load_user_settings handles non-dict JSON content."""
    temp_settings_file.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")

    with _mock_settings_file(temp_settings_file):
        settings = load_user_settings()

    # Should return defaults
    assert settings["terminal_agent"] == "codex"


# -- Legacy migration tests ------------------------------------------------------------


def test_load_user_settings_migrates_legacy_agent_format(temp_settings_file: Path) -> None:
    """Test that legacy multi-agent config is migrated to new simplified format."""
    legacy_settings = {
        "agents": {
            "reasoning_agent": {
                "model": "claude-sonnet-4-5",
                "provider": "anthropic",
            },
            "architect_agent": {
                "model": "claude-sonnet-4-5",
                "provider": "anthropic",
            },
        },
        "terminal_agent": {
            "command_template": "codex",
        },
        "preferences": {
            "api_keys": {
                "anthropic": "sk-legacy-key",
            },
            "auto_accept_changes": True,
        },
    }

    temp_settings_file.write_text(json.dumps(legacy_settings), encoding="utf-8")

    with _mock_settings_file(temp_settings_file):
        settings = load_user_settings()

    # Should extract brain model from agents
    assert settings["aura_brain_model"] == "claude-sonnet-4-5"

    # Should infer terminal agent from command template
    assert settings["terminal_agent"] == "codex"

    # Should extract API keys from preferences
    assert settings["api_keys"]["anthropic"] == "sk-legacy-key"

    # Should extract auto_accept from preferences
    assert settings["auto_accept_changes"] is True


def test_load_user_settings_migrates_legacy_terminal_agent_dict(temp_settings_file: Path) -> None:
    """Test that legacy terminal_agent dict is migrated correctly."""
    legacy_settings = {
        "aura_brain_model": "claude-sonnet-4-5",
        "terminal_agent": {
            "command_template": "claude-code",
        },
        "api_keys": {
            "anthropic": "test",
        },
    }

    temp_settings_file.write_text(json.dumps(legacy_settings), encoding="utf-8")

    with _mock_settings_file(temp_settings_file):
        settings = load_user_settings()

    assert settings["terminal_agent"] == "claude_code"


def test_load_user_settings_normalizes_invalid_brain_model(temp_settings_file: Path) -> None:
    """Test that invalid brain models are normalized to default."""
    invalid_settings = {
        "aura_brain_model": "invalid-model-xyz",
        "terminal_agent": "codex",
    }

    temp_settings_file.write_text(json.dumps(invalid_settings), encoding="utf-8")

    with _mock_settings_file(temp_settings_file):
        settings = load_user_settings()

    # Should fall back to default
    assert settings["aura_brain_model"] == "claude-sonnet-4-5"


# -- Saving settings tests -------------------------------------------------------------


def test_save_user_settings_creates_file(temp_settings_file: Path) -> None:
    """Test that save_user_settings creates settings file."""
    settings = {
        "aura_brain_model": "claude-opus-4",
        "terminal_agent": "codex",
        "api_keys": {
            "anthropic": "sk-test",
            "openai": "",
            "google": "",
        },
        "auto_accept_changes": True,
    }

    with _mock_settings_file(temp_settings_file):
        save_user_settings(settings)

    assert temp_settings_file.exists()

    saved_data = json.loads(temp_settings_file.read_text(encoding="utf-8"))
    assert saved_data["aura_brain_model"] == "claude-opus-4"
    assert saved_data["terminal_agent"] == "codex"


def test_save_user_settings_normalizes_values(temp_settings_file: Path) -> None:
    """Test that save_user_settings normalizes invalid values before saving."""
    settings = {
        "aura_brain_model": "invalid-model",
        "terminal_agent": "unknown-agent",
        "api_keys": "not-a-dict",  # Invalid type
        "auto_accept_changes": "yes",  # Should be bool
    }

    with _mock_settings_file(temp_settings_file):
        save_user_settings(settings)

    saved_data = json.loads(temp_settings_file.read_text(encoding="utf-8"))

    # Should normalize invalid brain model
    assert saved_data["aura_brain_model"] == "claude-sonnet-4-5"

    # Should normalize invalid terminal agent
    assert saved_data["terminal_agent"] == "codex"

    # Should provide default api_keys dict
    assert isinstance(saved_data["api_keys"], dict)

    # Should convert to bool
    assert saved_data["auto_accept_changes"] is True


def test_save_user_settings_persists_custom_command_when_custom_selected(temp_settings_file: Path) -> None:
    """Test that custom terminal command is saved when terminal_agent is 'custom'."""
    settings = {
        "aura_brain_model": "claude-sonnet-4-5",
        "terminal_agent": "custom",
        "terminal_agent_custom_command": "my-custom-agent --mode advanced",
        "api_keys": {
            "anthropic": "",
            "openai": "",
            "google": "",
        },
        "auto_accept_changes": True,
    }

    with _mock_settings_file(temp_settings_file):
        save_user_settings(settings)

    saved_data = json.loads(temp_settings_file.read_text(encoding="utf-8"))
    assert saved_data["terminal_agent"] == "custom"
    assert saved_data["terminal_agent_custom_command"] == "my-custom-agent --mode advanced"


def test_save_user_settings_omits_custom_command_when_not_custom(temp_settings_file: Path) -> None:
    """Test that custom command is not saved when using a preset."""
    settings = {
        "aura_brain_model": "claude-sonnet-4-5",
        "terminal_agent": "codex",
        "terminal_agent_custom_command": "should-be-ignored",
        "api_keys": {},
        "auto_accept_changes": True,
    }

    with _mock_settings_file(temp_settings_file):
        save_user_settings(settings)

    saved_data = json.loads(temp_settings_file.read_text(encoding="utf-8"))
    assert "terminal_agent_custom_command" not in saved_data


# -- API key sanitization tests --------------------------------------------------------


def test_api_keys_are_sanitized_on_load(temp_settings_file: Path) -> None:
    """Test that API keys are sanitized (whitespace trimmed) on load."""
    settings_data = {
        "api_keys": {
            "anthropic": "  sk-ant-123  ",
            "openai": " sk-456\n",
            "google": "",
        },
    }

    temp_settings_file.write_text(json.dumps(settings_data), encoding="utf-8")

    with _mock_settings_file(temp_settings_file):
        settings = load_user_settings()

    assert settings["api_keys"]["anthropic"] == "sk-ant-123"
    assert settings["api_keys"]["openai"] == "sk-456"
    assert settings["api_keys"]["google"] == ""


def test_api_keys_missing_keys_get_defaults(temp_settings_file: Path) -> None:
    """Test that missing API keys are filled with empty defaults."""
    settings_data = {
        "api_keys": {
            "anthropic": "sk-test",
            # Missing openai and google
        },
    }

    temp_settings_file.write_text(json.dumps(settings_data), encoding="utf-8")

    with _mock_settings_file(temp_settings_file):
        settings = load_user_settings()

    assert settings["api_keys"]["anthropic"] == "sk-test"
    assert settings["api_keys"]["openai"] == ""
    assert settings["api_keys"]["google"] == ""


# -- Terminal agent command template tests ---------------------------------------------


def test_get_terminal_agent_command_template_returns_preset(temp_settings_file: Path) -> None:
    """Test that get_terminal_agent_command_template returns correct preset."""
    settings = {
        "terminal_agent": "codex",
        "api_keys": {},
    }

    with _mock_settings_file(temp_settings_file):
        save_user_settings(settings)
        command = get_terminal_agent_command_template(settings)

    assert command == TERMINAL_AGENT_PRESETS["codex"]["command_template"]


def test_get_terminal_agent_command_template_returns_custom_command(temp_settings_file: Path) -> None:
    """Test that custom command is returned when terminal_agent is 'custom'."""
    settings = {
        "terminal_agent": "custom",
        "terminal_agent_custom_command": "my-agent --special-mode",
        "api_keys": {},
    }

    command = get_terminal_agent_command_template(settings)

    assert command == "my-agent --special-mode"


def test_get_terminal_agent_command_template_falls_back_when_custom_empty(temp_settings_file: Path) -> None:
    """Test that default is returned when custom is selected but command is empty."""
    settings = {
        "terminal_agent": "custom",
        "terminal_agent_custom_command": "",
        "api_keys": {},
    }

    command = get_terminal_agent_command_template(settings)

    assert command == DEFAULT_TERMINAL_COMMAND_TEMPLATE


def test_get_terminal_agent_command_template_handles_unknown_preset(temp_settings_file: Path) -> None:
    """Test that default is returned for unknown terminal agent preset."""
    settings = {
        "terminal_agent": "unknown-preset",
        "api_keys": {},
    }

    command = get_terminal_agent_command_template(settings)

    assert command == DEFAULT_TERMINAL_COMMAND_TEMPLATE


def test_get_terminal_agent_command_template_loads_from_disk_when_no_settings(temp_settings_file: Path) -> None:
    """Test that get_terminal_agent_command_template loads from disk when no settings provided."""
    settings_data = {
        "terminal_agent": "claude_code",
        "api_keys": {},
    }

    temp_settings_file.write_text(json.dumps(settings_data), encoding="utf-8")

    with _mock_settings_file(temp_settings_file):
        command = get_terminal_agent_command_template(None)

    assert command == TERMINAL_AGENT_PRESETS["claude_code"]["command_template"]


# -- Update functions tests ------------------------------------------------------------


def test_update_user_preferences_merges_auto_accept(temp_settings_file: Path) -> None:
    """Test that update_user_preferences correctly updates auto_accept_changes."""
    # Initial settings
    initial = {
        "aura_brain_model": "claude-sonnet-4-5",
        "terminal_agent": "codex",
        "api_keys": {"anthropic": "test"},
        "auto_accept_changes": True,
    }

    with _mock_settings_file(temp_settings_file):
        save_user_settings(initial)

        # Update preference
        updated = update_user_preferences({"auto_accept_changes": False})

    assert updated["auto_accept_changes"] is False
    assert updated["aura_brain_model"] == "claude-sonnet-4-5"  # Unchanged


def test_update_user_preferences_merges_api_keys(temp_settings_file: Path) -> None:
    """Test that update_user_preferences correctly updates API keys."""
    initial = {
        "aura_brain_model": "claude-sonnet-4-5",
        "terminal_agent": "codex",
        "api_keys": {
            "anthropic": "old-key",
            "openai": "",
            "google": "",
        },
        "auto_accept_changes": True,
    }

    with _mock_settings_file(temp_settings_file):
        save_user_settings(initial)

        updated = update_user_preferences({
            "api_keys": {
                "anthropic": "new-key",
                "openai": "sk-openai",
            }
        })

    assert updated["api_keys"]["anthropic"] == "new-key"
    assert updated["api_keys"]["openai"] == "sk-openai"


def test_update_agent_settings_updates_brain_model(temp_settings_file: Path) -> None:
    """Test that update_agent_settings updates brain model."""
    initial = {
        "aura_brain_model": "claude-sonnet-4-5",
        "terminal_agent": "codex",
        "api_keys": {},
        "auto_accept_changes": True,
    }

    with _mock_settings_file(temp_settings_file):
        save_user_settings(initial)

        updated = update_agent_settings({"aura_brain_model": "claude-opus-4"})

    assert updated["aura_brain_model"] == "claude-opus-4"


def test_update_agent_settings_updates_terminal_agent(temp_settings_file: Path) -> None:
    """Test that update_agent_settings updates terminal agent selection."""
    initial = {
        "aura_brain_model": "claude-sonnet-4-5",
        "terminal_agent": "codex",
        "api_keys": {},
        "auto_accept_changes": True,
    }

    with _mock_settings_file(temp_settings_file):
        save_user_settings(initial)

        updated = update_agent_settings({
            "terminal_agent": "claude_code",
        })

    assert updated["terminal_agent"] == "claude_code"


def test_update_agent_settings_updates_custom_command(temp_settings_file: Path) -> None:
    """Test that update_agent_settings updates custom terminal command."""
    initial = {
        "aura_brain_model": "claude-sonnet-4-5",
        "terminal_agent": "codex",
        "api_keys": {},
        "auto_accept_changes": True,
    }

    with _mock_settings_file(temp_settings_file):
        save_user_settings(initial)

        updated = update_agent_settings({
            "terminal_agent": "custom",
            "terminal_agent_custom_command": "my-custom-agent",
        })

    assert updated["terminal_agent"] == "custom"
    assert updated["terminal_agent_custom_command"] == "my-custom-agent"


# -- get_auto_accept_changes convenience function --------------------------------------


def test_get_auto_accept_changes_returns_current_value(temp_settings_file: Path) -> None:
    """Test that get_auto_accept_changes returns the current setting value."""
    settings = {
        "aura_brain_model": "claude-sonnet-4-5",
        "terminal_agent": "codex",
        "api_keys": {},
        "auto_accept_changes": False,
    }

    with _mock_settings_file(temp_settings_file):
        save_user_settings(settings)
        result = get_auto_accept_changes()

    assert result is False


def test_get_auto_accept_changes_defaults_to_true(temp_settings_file: Path) -> None:
    """Test that get_auto_accept_changes defaults to True when not set."""
    with _mock_settings_file(temp_settings_file):
        result = get_auto_accept_changes()

    assert result is True


# -- Round-trip persistence tests ------------------------------------------------------


def test_save_load_round_trip_preserves_all_fields(temp_settings_file: Path) -> None:
    """Test that save and load preserve all settings fields."""
    original_settings = {
        "aura_brain_model": "gemini-2.5-pro",
        "terminal_agent": "claude_code",
        "api_keys": {
            "anthropic": "sk-ant-test",
            "openai": "sk-openai-test",
            "google": "google-key-test",
        },
        "auto_accept_changes": False,
    }

    with _mock_settings_file(temp_settings_file):
        save_user_settings(original_settings)
        loaded_settings = load_user_settings()

    assert loaded_settings["aura_brain_model"] == original_settings["aura_brain_model"]
    assert loaded_settings["terminal_agent"] == original_settings["terminal_agent"]
    assert loaded_settings["api_keys"] == original_settings["api_keys"]
    assert loaded_settings["auto_accept_changes"] == original_settings["auto_accept_changes"]


def test_save_load_round_trip_with_custom_command(temp_settings_file: Path) -> None:
    """Test that custom terminal command persists across save/load."""
    original_settings = {
        "aura_brain_model": "claude-sonnet-4-5",
        "terminal_agent": "custom",
        "terminal_agent_custom_command": "my-agent --verbose --mode=production",
        "api_keys": {
            "anthropic": "",
            "openai": "",
            "google": "",
        },
        "auto_accept_changes": True,
    }

    with _mock_settings_file(temp_settings_file):
        save_user_settings(original_settings)
        loaded_settings = load_user_settings()

    assert loaded_settings["terminal_agent"] == "custom"
    assert loaded_settings["terminal_agent_custom_command"] == "my-agent --verbose --mode=production"
