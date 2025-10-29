import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.aura.config import ROOT_DIR

logger = logging.getLogger(__name__)


class ConversationPersistenceService:
    """
    Persists conversations and messages to a SQLite database, with a graceful
    in-memory fallback when the database is unavailable or corrupted.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else ROOT_DIR / "aura_conversations.db"
        self._connection: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()
        self._fallback_mode = False
        self._fallback_conversations: Dict[str, Dict[str, Any]] = {}
        self._fallback_messages: Dict[str, List[Dict[str, Any]]] = {}

        self._initialize_database()

    # --------------------------------------------------------------------- #
    # Initialization & teardown
    # --------------------------------------------------------------------- #
    def _initialize_database(self) -> None:
        """Attempt to set up the SQLite database; enable in-memory fallback on failure."""
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
            self._connection.execute("PRAGMA journal_mode=WAL;")
            self._connection.execute("PRAGMA foreign_keys = ON;")
            self._create_schema()
            logger.info("Conversation persistence initialized at %s", self.db_path)
        except Exception as exc:  # Broad except to guarantee fallback
            logger.error(
                "Failed to initialize conversation database at %s: %s. "
                "Falling back to in-memory storage.",
                self.db_path,
                exc,
            )
            self._activate_fallback_mode()

    def _create_schema(self) -> None:
        """Create tables and indexes if they do not already exist."""
        if not self._connection:
            return
        with self._connection:  # type: ignore[call-arg]
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    title TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                );
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_project_name ON conversations(project_name);"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON conversations(updated_at);"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_conversation_timestamp ON messages(conversation_id, created_at);"
            )

    def close(self) -> None:
        """Close the SQLite connection if it is open."""
        if self._connection:
            try:
                self._connection.close()
            except Exception:
                logger.debug("Failed to close conversation database connection cleanly.", exc_info=True)
        self._connection = None

    def _activate_fallback_mode(self) -> None:
        """Switch to in-memory persistence to ensure the app remains functional."""
        self._fallback_mode = True
        self._fallback_conversations = {}
        self._fallback_messages = {}
        self.close()

    @property
    def fallback_mode(self) -> bool:
        return self._fallback_mode

    # --------------------------------------------------------------------- #
    # Conversation helpers
    # --------------------------------------------------------------------- #
    def get_most_recent_conversation(self, project_name: str) -> Optional[Dict[str, Any]]:
        """Return the most recently updated conversation for a project."""
        if self._fallback_mode:
            return self._get_recent_conversation_fallback(project_name)

        try:
            with self._lock:
                cursor = self._connection.execute(  # type: ignore[union-attr]
                    """
                    SELECT id, project_name, title, created_at, updated_at, is_active
                    FROM conversations
                    WHERE project_name = ?
                    ORDER BY datetime(updated_at) DESC
                    LIMIT 1;
                    """,
                    (project_name,),
                )
                row = cursor.fetchone()
                return self._row_to_conversation(row) if row else None
        except sqlite3.DatabaseError as exc:
            logger.error("Database error while loading conversation: %s", exc, exc_info=True)
            self._activate_fallback_mode()
            return self._get_recent_conversation_fallback(project_name)
        except Exception as exc:
            logger.error("Unexpected error while loading conversation: %s", exc, exc_info=True)
            return None

    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        if self._fallback_mode:
            return self._fallback_conversations.get(conversation_id)

        try:
            with self._lock:
                cursor = self._connection.execute(  # type: ignore[union-attr]
                    """
                    SELECT id, project_name, title, created_at, updated_at, is_active
                    FROM conversations
                    WHERE id = ?;
                    """,
                    (conversation_id,),
                )
                row = cursor.fetchone()
                return self._row_to_conversation(row) if row else None
        except sqlite3.DatabaseError as exc:
            logger.error("Database error while fetching conversation %s: %s", conversation_id, exc, exc_info=True)
            self._activate_fallback_mode()
            return self._fallback_conversations.get(conversation_id)
        except Exception:
            logger.debug("Unexpected error while fetching conversation %s", conversation_id, exc_info=True)
            return None

    def create_conversation(self, project_name: str, title: Optional[str] = None, active: bool = True) -> Dict[str, Any]:
        conversation_id = str(uuid.uuid4())
        timestamp = self._now()
        conversation = {
            "id": conversation_id,
            "project_name": project_name,
            "title": title,
            "created_at": timestamp,
            "updated_at": timestamp,
            "is_active": 1 if active else 0,
        }

        if self._fallback_mode:
            self._record_conversation_fallback(conversation, set_active=active)
            return conversation

        try:
            with self._lock:
                if not self._connection:
                    raise RuntimeError("Conversation database connection is not available.")
                if active:
                    self._connection.execute(
                        "UPDATE conversations SET is_active = 0 WHERE project_name = ?;",
                        (project_name,),
                    )
                self._connection.execute(
                    """
                    INSERT INTO conversations (id, project_name, title, created_at, updated_at, is_active)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (
                        conversation_id,
                        project_name,
                        title,
                        timestamp,
                        timestamp,
                        1 if active else 0,
                    ),
                )
                self._connection.commit()
            return conversation
        except sqlite3.DatabaseError as exc:
            logger.error("Database error while creating conversation: %s", exc, exc_info=True)
            self._activate_fallback_mode()
            self._record_conversation_fallback(conversation, set_active=active)
            return conversation
        except Exception as exc:
            logger.error("Unexpected error while creating conversation: %s", exc, exc_info=True)
            return conversation

    def mark_conversation_active(self, conversation_id: str) -> None:
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            return
        project_name = conversation["project_name"]

        if self._fallback_mode:
            for convo in self._fallback_conversations.values():
                if convo["project_name"] == project_name:
                    convo["is_active"] = 1 if convo["id"] == conversation_id else 0
            return

        try:
            with self._lock:
                if not self._connection:
                    raise RuntimeError("Conversation database connection is not available.")
                self._connection.execute(
                    "UPDATE conversations SET is_active = CASE WHEN id = ? THEN 1 ELSE 0 END WHERE project_name = ?;",
                    (conversation_id, project_name),
                )
                self._connection.commit()
        except sqlite3.DatabaseError as exc:
            logger.error("Failed to mark conversation %s active: %s", conversation_id, exc, exc_info=True)
            self._activate_fallback_mode()
            self.mark_conversation_active(conversation_id)

    def update_conversation_title(self, conversation_id: str, title: str) -> None:
        if not title:
            return
        if self._fallback_mode:
            convo = self._fallback_conversations.get(conversation_id)
            if convo and not convo.get("title"):
                convo["title"] = title
            return

        try:
            with self._lock:
                if not self._connection:
                    raise RuntimeError("Conversation database connection is not available.")
                self._connection.execute(
                    "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ? AND (title IS NULL OR title = '');",
                    (title, self._now(), conversation_id),
                )
                self._connection.commit()
        except sqlite3.DatabaseError as exc:
            logger.error("Failed to update conversation title: %s", exc, exc_info=True)
            self._activate_fallback_mode()
            self.update_conversation_title(conversation_id, title)

    def update_conversation_timestamp(self, conversation_id: str) -> None:
        if self._fallback_mode:
            convo = self._fallback_conversations.get(conversation_id)
            if convo:
                convo["updated_at"] = self._now()
            return

        try:
            with self._lock:
                if not self._connection:
                    raise RuntimeError("Conversation database connection is not available.")
                self._connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?;",
                    (self._now(), conversation_id),
                )
                self._connection.commit()
        except sqlite3.DatabaseError as exc:
            logger.error("Failed to bump conversation timestamp: %s", exc, exc_info=True)
            self._activate_fallback_mode()
            self.update_conversation_timestamp(conversation_id)

    # --------------------------------------------------------------------- #
    # Message helpers
    # --------------------------------------------------------------------- #
    def save_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        metadata_json = json.dumps(metadata) if metadata else None
        timestamp = self._now()

        if self._fallback_mode:
            self._store_message_fallback(conversation_id, role, content, metadata, timestamp)
            return

        try:
            with self._lock:
                if not self._connection:
                    raise RuntimeError("Conversation database connection is not available.")
                self._connection.execute(
                    """
                    INSERT INTO messages (conversation_id, role, content, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    (
                        conversation_id,
                        role,
                        content,
                        metadata_json,
                        timestamp,
                    ),
                )
                self._connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?;",
                    (timestamp, conversation_id),
                )
                self._connection.commit()
        except sqlite3.DatabaseError as exc:
            logger.error("Database error while saving message: %s", exc, exc_info=True)
            self._activate_fallback_mode()
            self._store_message_fallback(conversation_id, role, content, metadata, timestamp)
        except Exception:
            logger.debug("Unexpected error while saving message", exc_info=True)

    def load_messages(self, conversation_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        if self._fallback_mode:
            messages = list(self._fallback_messages.get(conversation_id, []))
            if limit:
                messages = messages[-limit:]
            return messages

        try:
            with self._lock:
                if not self._connection:
                    raise RuntimeError("Conversation database connection is not available.")
                query = (
                    """
                    SELECT role, content, metadata, created_at
                    FROM messages
                    WHERE conversation_id = ?
                    ORDER BY datetime(created_at) DESC
                    """
                )
                if limit:
                    query += " LIMIT ?"
                    cursor = self._connection.execute(query + ";", (conversation_id, limit))
                else:
                    cursor = self._connection.execute(query + ";", (conversation_id,))
                rows = cursor.fetchall()
                messages = [
                    self._row_to_message(row)
                    for row in rows
                ]
                messages.reverse()  # Ensure chronological order
                return messages
        except sqlite3.DatabaseError as exc:
            logger.error("Database error while loading messages: %s", exc, exc_info=True)
            self._activate_fallback_mode()
            return self.load_messages(conversation_id, limit)
        except Exception:
            logger.debug("Unexpected error while loading messages", exc_info=True)
            return []

    def search_messages(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        if not query:
            return []

        if self._fallback_mode:
            return self._search_messages_fallback(query, limit)

        like_term = f"%{query}%"
        try:
            with self._lock:
                if not self._connection:
                    raise RuntimeError("Conversation database connection is not available.")
                cursor = self._connection.execute(
                    """
                    SELECT m.conversation_id, c.project_name, m.content, m.created_at
                    FROM messages m
                    JOIN conversations c ON c.id = m.conversation_id
                    WHERE m.content LIKE ?
                    ORDER BY datetime(m.created_at) DESC
                    LIMIT ?;
                    """,
                    (like_term, limit),
                )
                rows = cursor.fetchall()
                results = []
                for convo_id, project_name, content, created_at in rows:
                    snippet = self._build_snippet(content, query)
                    results.append(
                        {
                            "conversation_id": convo_id,
                            "project_name": project_name,
                            "snippet": snippet,
                            "timestamp": created_at,
                        }
                    )
                return results
        except sqlite3.DatabaseError as exc:
            logger.error("Database error while searching messages: %s", exc, exc_info=True)
            self._activate_fallback_mode()
            return self._search_messages_fallback(query, limit)
        except Exception:
            logger.debug("Unexpected error while searching messages", exc_info=True)
            return []

    # --------------------------------------------------------------------- #
    # Internal helpers
    # --------------------------------------------------------------------- #
    def _row_to_conversation(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row[0],
            "project_name": row[1],
            "title": row[2],
            "created_at": row[3],
            "updated_at": row[4],
            "is_active": row[5],
        }

    def _row_to_message(self, row: sqlite3.Row) -> Dict[str, Any]:
        metadata = None
        if row[2]:
            try:
                metadata = json.loads(row[2])
            except json.JSONDecodeError:
                metadata = None
        message = {
            "role": row[0],
            "content": row[1],
            "created_at": row[3],
        }
        if metadata and isinstance(metadata, dict):
            # Rehydrate known metadata fields back onto the message
            images = metadata.get("images")
            if images:
                message["images"] = images
            if metadata:
                message["metadata"] = metadata
        return message

    def _get_recent_conversation_fallback(self, project_name: str) -> Optional[Dict[str, Any]]:
        candidates = [
            convo for convo in self._fallback_conversations.values()
            if convo["project_name"] == project_name
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda c: c.get("updated_at", ""), reverse=True)[0]

    def _record_conversation_fallback(self, conversation: Dict[str, Any], set_active: bool) -> None:
        if set_active:
            for convo in self._fallback_conversations.values():
                if convo["project_name"] == conversation["project_name"]:
                    convo["is_active"] = 0
        self._fallback_conversations[conversation["id"]] = dict(conversation)
        self._fallback_messages.setdefault(conversation["id"], [])

    def _store_message_fallback(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]],
        timestamp: str,
    ) -> None:
        self._fallback_messages.setdefault(conversation_id, [])
        message = {
            "role": role,
            "content": content,
            "created_at": timestamp,
        }
        if metadata:
            if "images" in metadata:
                message["images"] = metadata["images"]
            message["metadata"] = metadata
        self._fallback_messages[conversation_id].append(message)
        convo = self._fallback_conversations.get(conversation_id)
        if convo:
            convo["updated_at"] = timestamp

    def _search_messages_fallback(self, query: str, limit: int) -> List[Dict[str, Any]]:
        lowered = query.lower()
        results: List[Dict[str, Any]] = []
        for convo_id, messages in self._fallback_messages.items():
            convo = self._fallback_conversations.get(convo_id)
            project_name = convo["project_name"] if convo else "unknown"
            for message in messages:
                content = message.get("content") or ""
                if lowered in content.lower():
                    results.append(
                        {
                            "conversation_id": convo_id,
                            "project_name": project_name,
                            "snippet": self._build_snippet(content, query),
                            "timestamp": message.get("created_at"),
                        }
                    )
        results.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
        return results[:limit]

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _build_snippet(content: str, query: str, radius: int = 80) -> str:
        if not content:
            return ""
        lower = content.lower()
        idx = lower.find(query.lower())
        if idx == -1:
            return content[:radius].strip()
        start = max(idx - radius // 2, 0)
        end = min(idx + len(query) + radius // 2, len(content))
        snippet = content[start:end]
        if start > 0:
            snippet = "..." + snippet
        if end < len(content):
            snippet += "..."
        return snippet.strip()

