from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import sqlite3
import threading
from typing import List, Optional
import uuid

from src.domain.entities import (
    ConversationMessage,
    RunRecord,
    TerminalState,
)
from src.persistence.interface import PersistenceStore


class SQLitePersistenceStore(PersistenceStore):
    """SQLite-backed dual-boundary persistence store.
    
    Supports file-based or in-memory databases with strict commit boundaries.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
            isolation_level="DEFERRED",
        )
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversation_messages (
                        id TEXT PRIMARY KEY,
                        conversation_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS run_records (
                        run_id TEXT PRIMARY KEY,
                        turn_id TEXT NOT NULL,
                        conversation_id TEXT NOT NULL,
                        user_input TEXT NOT NULL,
                        terminal_state TEXT NOT NULL,
                        partial_output TEXT NOT NULL,
                        chunk_count INTEGER NOT NULL,
                        error_message TEXT,
                        created_at TEXT NOT NULL,
                        completed_at TEXT NOT NULL
                    )
                    """
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_conv_id ON conversation_messages(conversation_id)"
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_run_turn ON run_records(turn_id)"
                )

    async def save_user_message(
        self,
        conversation_id: str,
        turn_id: str,
        content: str,
    ) -> ConversationMessage:
        msg_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO conversation_messages (id, conversation_id, turn_id, role, content, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (msg_id, conversation_id, turn_id, "user", content, now_iso),
                )
        return ConversationMessage(
            id=msg_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            role="user",
            content=content,
            created_at=now_iso,
        )

    async def commit_assistant_message(
        self,
        conversation_id: str,
        turn_id: str,
        content: str,
    ) -> ConversationMessage:
        msg_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO conversation_messages (id, conversation_id, turn_id, role, content, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (msg_id, conversation_id, turn_id, "assistant", content, now_iso),
                )
        return ConversationMessage(
            id=msg_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            role="assistant",
            content=content,
            created_at=now_iso,
        )

    async def save_run_record(self, record: RunRecord) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO run_records (
                        run_id, turn_id, conversation_id, user_input, terminal_state,
                        partial_output, chunk_count, error_message, created_at, completed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.run_id,
                        record.turn_id,
                        record.conversation_id,
                        record.user_input,
                        record.terminal_state.value,
                        record.partial_output,
                        record.chunk_count,
                        record.error_message,
                        record.created_at,
                        record.completed_at,
                    ),
                )

    async def get_conversation_messages(
        self,
        conversation_id: str,
    ) -> List[ConversationMessage]:
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT id, conversation_id, turn_id, role, content, created_at
                FROM conversation_messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC
                """,
                (conversation_id,),
            )
            rows = cursor.fetchall()
            return [
                ConversationMessage(
                    id=row["id"],
                    conversation_id=row["conversation_id"],
                    turn_id=row["turn_id"],
                    role=row["role"],
                    content=row["content"],
                    created_at=row["created_at"],
                )
                for row in rows
            ]

    async def get_run_record(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT run_id, turn_id, conversation_id, user_input, terminal_state,
                       partial_output, chunk_count, error_message, created_at, completed_at
                FROM run_records
                WHERE run_id = ?
                """,
                (run_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return RunRecord(
                run_id=row["run_id"],
                turn_id=row["turn_id"],
                conversation_id=row["conversation_id"],
                user_input=row["user_input"],
                terminal_state=TerminalState(row["terminal_state"]),
                partial_output=row["partial_output"],
                chunk_count=row["chunk_count"],
                error_message=row["error_message"],
                created_at=row["created_at"],
                completed_at=row["completed_at"],
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
