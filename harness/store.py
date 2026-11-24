"""
SQLite Event Store with WAL Mode, strict idempotency keys, and crash recovery.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from harness.models import Edge, Event, GraphSnapshot, Node, NodeStatus, NodeType, WorkspaceUser

_UPSERT_NODE_SQL = """
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
"""


def _node_params(node: Node) -> Tuple[Any, ...]:
    return (
        node.id,
        node.title,
        node.type.value if hasattr(node.type, "value") else str(node.type),
        node.status.value if hasattr(node.status, "value") else str(node.status),
        node.assigned_to,
        node.version,
        json.dumps(node.payload),
        node.created_at,
        node.updated_at,
    )


def _row_to_node(row: sqlite3.Row) -> Node:
    return Node(
        id=row["id"],
        title=row["title"],
        type=NodeType(row["type"]),
        status=NodeStatus(row["status"]),
        assigned_to=row["assigned_to"],
        version=row["version"],
        payload=json.loads(row["payload_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        event_id=row["event_id"],
        idempotency_key=row["idempotency_key"],
        event_type=row["event_type"],
        node_id=row["node_id"],
        actor=row["actor"],
        data=json.loads(row["data_json"]),
        timestamp=row["timestamp"],
    )


def _row_to_workspace_user(row: sqlite3.Row) -> WorkspaceUser:
    return WorkspaceUser(
        user_id=row["user_id"],
        display_name=row["display_name"],
        tool_name=row["tool_name"],
        client_host=row["client_host"],
        status=row["status"],
        last_task_id=row["last_task_id"],
        joined_at=row["joined_at"],
        last_seen=row["last_seen"],
    )


_UPSERT_USER_SQL = """
INSERT INTO workspace_users (
    user_id, display_name, tool_name, client_host, status, last_task_id, joined_at, last_seen
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(user_id) DO UPDATE SET
    display_name=excluded.display_name,
    tool_name=excluded.tool_name,
    client_host=excluded.client_host,
    status=excluded.status,
    last_task_id=excluded.last_task_id,
    last_seen=excluded.last_seen
"""


class EventStore:
    def __init__(self, db_path: str = "omniharness.db"):
        self.db_path = db_path
        if db_path != ":memory:" and not db_path.startswith("file:"):
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
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

                CREATE TABLE IF NOT EXISTS workspace_users (
                    user_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    client_host TEXT,
                    status TEXT NOT NULL,
                    last_task_id TEXT,
                    joined_at REAL NOT NULL,
                    last_seen REAL NOT NULL
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
            conn.execute(_UPSERT_NODE_SQL, _node_params(node))

    def get_node(self, node_id: str) -> Optional[Node]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
            return _row_to_node(row) if row else None

    def list_nodes(self) -> List[Node]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM nodes ORDER BY created_at ASC").fetchall()
            return [_row_to_node(row) for row in rows]

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
            return [_row_to_event(row) for row in rows]

    def list_events(self) -> List[Event]:
        """Return all persisted events ordered chronologically by timestamp ASC."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM events ORDER BY timestamp ASC").fetchall()
            return [_row_to_event(row) for row in rows]

    @staticmethod
    def _next_guest_identity(conn: sqlite3.Connection) -> Tuple[str, str]:
        rows = conn.execute("SELECT user_id, display_name FROM workspace_users").fetchall()
        used_nums = set()
        for row in rows:
            for val in (row["user_id"], row["display_name"]):
                m = re.fullmatch(r"user[- ](\d+)", str(val).strip(), flags=re.IGNORECASE)
                if m:
                    used_nums.add(int(m.group(1)))
        idx = 1
        while idx in used_nums:
            idx += 1
        return f"user-{idx}", f"User {idx}"

    def upsert_workspace_user(
        self,
        user_id: Optional[str] = None,
        display_name: Optional[str] = None,
        tool_name: str = "generic_tool",
        client_host: Optional[str] = None,
        status: str = "joined",
        last_task_id: Optional[str] = None,
        preserve_working: bool = False,
    ) -> Tuple[WorkspaceUser, bool]:
        """Insert or update a workspace participant, preserving original joined_at."""
        now = time.time()
        clean_uid = user_id.strip() if isinstance(user_id, str) and user_id.strip() else None
        clean_name = display_name.strip() if isinstance(display_name, str) and display_name.strip() else None
        clean_tool = tool_name.strip() if isinstance(tool_name, str) and tool_name.strip() else "generic_tool"
        clean_host = client_host.strip() if isinstance(client_host, str) and client_host.strip() else None
        clean_task = last_task_id.strip() if isinstance(last_task_id, str) and last_task_id.strip() else None

        with self._get_connection() as conn:
            auto_display: Optional[str] = None
            if not clean_uid:
                clean_uid, auto_display = self._next_guest_identity(conn)

            existing = conn.execute(
                "SELECT * FROM workspace_users WHERE user_id = ?",
                (clean_uid,),
            ).fetchone()

            if existing:
                is_new = False
                joined_at = existing["joined_at"]
                resolved_display = clean_name or existing["display_name"]
                resolved_tool = (
                    clean_tool
                    if clean_tool != "generic_tool"
                    else existing["tool_name"]
                )
                resolved_host = clean_host or existing["client_host"]
                resolved_task = clean_task or existing["last_task_id"]
                resolved_status = (
                    "working"
                    if preserve_working and existing["status"] == "working"
                    else status
                )
            else:
                is_new = True
                joined_at = now
                resolved_display = clean_name or auto_display or clean_uid
                resolved_tool = clean_tool
                resolved_host = clean_host
                resolved_task = clean_task
                resolved_status = status

            conn.execute(
                _UPSERT_USER_SQL,
                (
                    clean_uid,
                    resolved_display,
                    resolved_tool,
                    resolved_host,
                    resolved_status,
                    resolved_task,
                    joined_at,
                    now,
                ),
            )
            conn.commit()

        user = WorkspaceUser(
            user_id=clean_uid,
            display_name=resolved_display,
            tool_name=resolved_tool,
            client_host=resolved_host,
            status=resolved_status,
            last_task_id=resolved_task,
            joined_at=joined_at,
            last_seen=now,
        )
        return user, is_new

    def list_workspace_users(self) -> List[WorkspaceUser]:
        """Return all workspace users ordered by joined_at ASC."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM workspace_users ORDER BY joined_at ASC").fetchall()
            return [_row_to_workspace_user(row) for row in rows]

    def export_snapshot(self) -> GraphSnapshot:
        """Export all nodes, edges, events, and users as a portable GraphSnapshot."""
        with self._get_connection() as conn:
            node_rows = conn.execute("SELECT * FROM nodes ORDER BY created_at ASC").fetchall()
            edge_rows = conn.execute("SELECT * FROM edges").fetchall()
            event_rows = conn.execute("SELECT * FROM events ORDER BY timestamp ASC").fetchall()
            user_rows = conn.execute("SELECT * FROM workspace_users ORDER BY joined_at ASC").fetchall()
            return GraphSnapshot(
                nodes=[_row_to_node(row) for row in node_rows],
                edges=[
                    Edge(source=row["source"], target=row["target"], relation=row["relation"])
                    for row in edge_rows
                ],
                events=[_row_to_event(row) for row in event_rows],
                users=[_row_to_workspace_user(row) for row in user_rows],
            )

    def import_snapshot(
        self,
        nodes: List[Node],
        edges: List[Edge],
        events: List[Event],
        replace: bool = True,
        users: Optional[List[WorkspaceUser]] = None,
    ) -> Dict[str, int]:
        """
        Atomically import nodes, edges, events, and users in a single SQLite transaction.
        Re-imports are idempotent on event_id and idempotency_key via INSERT OR IGNORE.
        """
        with self._get_connection() as conn:
            if replace:
                conn.execute("DELETE FROM edges;")
                conn.execute("DELETE FROM events;")
                conn.execute("DELETE FROM nodes;")
                conn.execute("DELETE FROM workspace_users;")

            for node in nodes:
                conn.execute(_UPSERT_NODE_SQL, _node_params(node))

            for edge in edges:
                conn.execute(
                    "INSERT OR IGNORE INTO edges (source, target, relation) VALUES (?, ?, ?)",
                    (edge.source, edge.target, edge.relation),
                )

            inserted_events = 0
            for event in events:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO events (event_id, idempotency_key, event_type, node_id, actor, data_json, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.idempotency_key,
                        event.event_type,
                        event.node_id,
                        event.actor,
                        json.dumps(event.data),
                        event.timestamp,
                    ),
                )
                if cur.rowcount > 0:
                    inserted_events += cur.rowcount

            for user in (users or []):
                conn.execute(
                    _UPSERT_USER_SQL,
                    (
                        user.user_id,
                        user.display_name,
                        user.tool_name,
                        user.client_host,
                        user.status,
                        user.last_task_id,
                        user.joined_at,
                        user.last_seen,
                    ),
                )

            conn.commit()

        return {
            "nodes_imported": len(nodes),
            "edges_imported": len(edges),
            "events_imported": inserted_events,
        }

    def clear(self) -> None:
        """Clear all stored nodes, edges, events, and workspace users for clean test/demo runs."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM edges;")
            conn.execute("DELETE FROM events;")
            conn.execute("DELETE FROM nodes;")
            conn.execute("DELETE FROM workspace_users;")
            conn.commit()

