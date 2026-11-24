"""
SQLite Event Store with WAL Mode, strict idempotency keys, and crash recovery.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from harness.models import Edge, Event, Node, NodeStatus, NodeType


class EventStore:
    def __init__(self, db_path: str = "omniharness.db"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    idempotency_key TEXT UNIQUE,
                    event_type TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    timestamp REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    assigned_to TEXT,
                    version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS edges (
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    PRIMARY KEY (source, target)
                );

                CREATE INDEX IF NOT EXISTS idx_events_node_id ON events(node_id);
                CREATE INDEX IF NOT EXISTS idx_events_idempotency ON events(idempotency_key);
            """)

    def check_idempotency(self, idempotency_key: str) -> Optional[Event]:
        """Check if an operation with this idempotency key was already executed."""
        if not idempotency_key:
            return None
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM events WHERE idempotency_key = ?",
                (idempotency_key,)
            ).fetchone()
            if row:
                return Event(
                    event_id=row["event_id"],
                    idempotency_key=row["idempotency_key"],
                    event_type=row["event_type"],
                    node_id=row["node_id"],
                    actor=row["actor"],
                    data=json.loads(row["data_json"]),
                    timestamp=row["timestamp"]
                )
        return None

    def record_event(
        self,
        event_type: str,
        node_id: str,
        actor: str,
        data: Dict[str, Any],
        idempotency_key: Optional[str] = None
    ) -> Tuple[Event, bool]:
        """
        Record an event idempotently.
        Returns (Event, is_new).
        If idempotency_key exists, returns (existing_event, False).
        """
        if idempotency_key:
            existing = self.check_idempotency(idempotency_key)
            if existing:
                return existing, False

        event_id = str(uuid.uuid4())
        key = idempotency_key or str(uuid.uuid4())
        ts = time.time()
        data_json = json.dumps(data)

        with self._get_connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO events (event_id, idempotency_key, event_type, node_id, actor, data_json, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (event_id, key, event_type, node_id, actor, data_json, ts)
                )
            except sqlite3.IntegrityError:
                # Race condition catch: another worker inserted this idempotency key
                row = conn.execute("SELECT * FROM events WHERE idempotency_key = ?", (key,)).fetchone()
                return Event(
                    event_id=row["event_id"],
                    idempotency_key=row["idempotency_key"],
                    event_type=row["event_type"],
                    node_id=row["node_id"],
                    actor=row["actor"],
                    data=json.loads(row["data_json"]),
                    timestamp=row["timestamp"]
                ), False

        event = Event(
            event_id=event_id,
            idempotency_key=key,
            event_type=event_type,
            node_id=node_id,
            actor=actor,
            data=data,
            timestamp=ts
        )
        return event, True

    def upsert_node(self, node: Node) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO nodes (id, title, type, status, assigned_to, version, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    type=excluded.type,
                    status=excluded.status,
                    assigned_to=excluded.assigned_to,
                    version=excluded.version,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    node.id,
                    node.title,
                    node.type.value if hasattr(node.type, "value") else str(node.type),
                    node.status.value if hasattr(node.status, "value") else str(node.status),
                    node.assigned_to,
                    node.version,
                    json.dumps(node.payload),
                    node.created_at,
                    node.updated_at
                )
            )

    def get_node(self, node_id: str) -> Optional[Node]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
            if not row:
                return None
            return Node(
                id=row["id"],
                title=row["title"],
                type=NodeType(row["type"]),
                status=NodeStatus(row["status"]),
                assigned_to=row["assigned_to"],
                version=row["version"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"]
            )

    def list_nodes(self) -> List[Node]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM nodes ORDER BY created_at ASC").fetchall()
            return [
                Node(
                    id=row["id"],
                    title=row["title"],
                    type=NodeType(row["type"]),
                    status=NodeStatus(row["status"]),
                    assigned_to=row["assigned_to"],
                    version=row["version"],
                    payload=json.loads(row["payload_json"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"]
                )
                for row in rows
            ]

    def add_edge(self, source: str, target: str, relation: str = "relies_on") -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO edges (source, target, relation)
                VALUES (?, ?, ?)
                """,
                (source, target, relation)
            )

    def list_edges(self) -> List[Edge]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM edges").fetchall()
            return [
                Edge(source=row["source"], target=row["target"], relation=row["relation"])
                for row in rows
            ]

    def get_events_for_node(self, node_id: str) -> List[Event]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE node_id = ? ORDER BY timestamp ASC",
                (node_id,)
            ).fetchall()
            return [
                Event(
                    event_id=row["event_id"],
                    idempotency_key=row["idempotency_key"],
                    event_type=row["event_type"],
                    node_id=row["node_id"],
                    actor=row["actor"],
                    data=json.loads(row["data_json"]),
                    timestamp=row["timestamp"]
                )
                for row in rows
            ]
