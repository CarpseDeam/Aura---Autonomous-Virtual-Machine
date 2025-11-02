from __future__ import annotations

"""
One-time migration: Import project.json conversations into SQLite.

This script scans ~/.aura/projects/*/project.json and writes each project's
conversation_history as a single conversation in aura_conversations.db.

Preserves: conversation messages, timestamps (when available), basic metadata.

Usage:
    python -m scripts.migrate_project_json_to_sqlite
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.aura.services.conversation_persistence_service import ConversationPersistenceService
from src.aura.project.project_manager import ProjectManager

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def _parse_iso(ts: Optional[str]) -> Optional[str]:
    if not ts:
        return None
    try:
        # Normalize to ISO8601 if parseable
        return datetime.fromisoformat(ts).isoformat()
    except Exception:
        return None


def _generate_title_from_history(history: List[Dict[str, Any]]) -> Optional[str]:
    for msg in history:
        if msg.get("role") == "user":
            text = (msg.get("content") or "").strip()
            if not text:
                continue
            words = " ".join(text.split()).split()
            title = " ".join(words[:8])
            return title[:59] + "." if len(title) > 60 else title
    return None


def migrate() -> None:
    pm = ProjectManager()
    cps = ConversationPersistenceService()

    projects_dir = pm.storage_dir
    logger.info("Scanning projects under %s", projects_dir)

    for project_dir in projects_dir.iterdir():
        pj = project_dir / "project.json"
        if not pj.is_file():
            continue
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("Failed to read %s: %s", pj, exc)
            continue

        name = data.get("name") or project_dir.name
        history: List[Dict[str, Any]] = list(data.get("conversation_history") or [])
        created_at = _parse_iso(data.get("created_at"))
        updated_at = _parse_iso(data.get("last_active"))

        title = _generate_title_from_history(history)
        convo = cps.create_conversation(name, title=title, active=False)
        if created_at or updated_at:
            try:
                cps.update_conversation_timestamp(convo["id"])  # ensure row exists
                # Direct update for created/updated when provided
                if cps._connection:  # type: ignore[attr-defined]
                    with cps._connection:  # type: ignore[union-attr]
                        if created_at:
                            cps._connection.execute("UPDATE conversations SET created_at = ? WHERE id = ?;", (created_at, convo["id"]))
                        if updated_at:
                            cps._connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?;", (updated_at, convo["id"]))
            except Exception:
                logger.debug("Failed to backfill timestamps for %s", convo["id"], exc_info=True)

        for msg in history:
            role = msg.get("role") or "user"
            content = msg.get("content") or ""
            metadata = {}
            images = msg.get("images")
            if images:
                metadata["images"] = images
            created = _parse_iso(msg.get("created_at"))
            try:
                cps.save_message(convo["id"], role, content, metadata or None)
                if created and cps._connection:  # type: ignore[attr-defined]
                    with cps._connection:  # type: ignore[union-attr]
                        cps._connection.execute(
                            "UPDATE messages SET created_at = ? WHERE rowid = (SELECT MAX(rowid) FROM messages WHERE conversation_id = ?);",
                            (created, convo["id"]),
                        )
            except Exception as exc:
                logger.error("Failed to import message into %s: %s", convo["id"], exc)

        logger.info("Imported project '%s' with %d messages", name, len(history))


if __name__ == "__main__":
    migrate()

