from __future__ import annotations

"""
MCP Server Configurations

Preconfigured templates and configuration helpers for common MCP servers.
"""

import os
from pathlib import Path
from typing import Dict, Optional

from pydantic import BaseModel, Field


class MCPServerConfig(BaseModel):
    """Configuration for spawning an MCP server subprocess."""

    name: str
    command: list[str]
    description: Optional[str] = None
    env: Dict[str, str] = Field(default_factory=dict)
    cwd: Optional[Path] = None
    init_timeout_seconds: float = 15.0
    request_timeout_seconds: float = 20.0

    def resolved_env(self) -> Dict[str, str]:
        """Resolve ${VAR} references in env values using current environment."""
        def _subst(value: str) -> str:
            return os.path.expandvars(value)

        return {k: _subst(v) for k, v in self.env.items()}


def _filesystem_template(root: Optional[Path] = None) -> MCPServerConfig:
    # Filesystem server from MCP repo; assumes `npx` available. Uses CWD as root if not provided.
    cmd = ["npx", "-y", "@modelcontextprotocol/server-filesystem"]
    return MCPServerConfig(
        name="filesystem",
        command=cmd,
        description="Local filesystem operations via MCP",
        cwd=root if root else None,
    )


def _airtable_template() -> MCPServerConfig:
    # Expects AIRTABLE_API_KEY and AIRTABLE_BASE_ID in environment
    cmd = ["npx", "-y", "@modelcontextprotocol/server-airtable"]
    return MCPServerConfig(
        name="airtable",
        command=cmd,
        description="Airtable operations via MCP",
        env={
            "AIRTABLE_API_KEY": "${AIRTABLE_API_KEY}",
            "AIRTABLE_BASE_ID": "${AIRTABLE_BASE_ID}",
        },
    )


def _postgres_template() -> MCPServerConfig:
    # Expects PG connection string in POSTGRES_URL
    cmd = ["npx", "-y", "@modelcontextprotocol/server-postgres"]
    return MCPServerConfig(
        name="postgresql",
        command=cmd,
        description="PostgreSQL operations via MCP",
        env={
            "POSTGRES_URL": "${POSTGRES_URL}",
        },
    )


TEMPLATES: Dict[str, MCPServerConfig] = {
    "filesystem": _filesystem_template(),
    "airtable": _airtable_template(),
    "postgresql": _postgres_template(),
}


def build_config(template: str, *, root: Optional[str | Path] = None, overrides: Optional[dict] = None) -> MCPServerConfig:
    """Construct a config from a template name with optional overrides.

    Args:
        template: One of the keys in TEMPLATES.
        root: Optional filesystem root for servers that support it (e.g., filesystem).
        overrides: Optional dict to override fields on the config.

    Returns:
        MCPServerConfig instance with environment variables unresolved (resolved at spawn).
    """
    if template not in TEMPLATES:
        raise ValueError(f"Unknown MCP server template: {template}")

    base = TEMPLATES[template].model_copy(deep=True)
    if template == "filesystem" and root:
        base.cwd = Path(root)

    if overrides:
        for key, value in overrides.items():
            if hasattr(base, key):
                setattr(base, key, value)

    return base

