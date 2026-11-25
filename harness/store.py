"""
SQLite Event Store with WAL Mode, multi-board isolation, strict idempotency keys,
friend membership RBAC, and revocable board-scoped AI agent keys.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from harness.models import (
    Board,
    BoardAgentKey,
    BoardInvite,
    BoardMember,
    BoardRole,
    BoardStage,
    Edge,
    Event,
    GraphSnapshot,
    MemberStatus,
    Node,
    NodeStatus,
    NodeType,
    WorkspaceUser,
)

_UPSERT_NODE_SQL = """
INSERT INTO nodes (board_id, id, title, type, status, assigned_to, version, payload_json, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(board_id, id) DO UPDATE SET
    title=excluded.title,
    type=excluded.type,
    status=excluded.status,
    assigned_to=excluded.assigned_to,
    version=excluded.version,
    payload_json=excluded.payload_json,
    updated_at=excluded.updated_at
"""

_UPSERT_USER_SQL = """
INSERT INTO workspace_users (
    board_id, user_id, display_name, tool_name, client_host, status, last_task_id, joined_at, last_seen
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(board_id, user_id) DO UPDATE SET
    display_name=excluded.display_name,
    tool_name=excluded.tool_name,
    client_host=excluded.client_host,
    status=excluded.status,
    last_task_id=excluded.last_task_id,
    last_seen=excluded.last_seen
"""


def _node_params(node: Node, board_id: Optional[str] = None) -> Tuple[Any, ...]:
    resolved_board = board_id or getattr(node, "board_id", "default") or "default"
    return (
        resolved_board,
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
        board_id=row["board_id"],
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
        board_id=row["board_id"],
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
        board_id=row["board_id"],
        joined_at=row["joined_at"],
        last_seen=row["last_seen"],
    )


def _row_to_board(row: sqlite3.Row, role: Optional[BoardRole] = None, node_count: int = 0) -> Board:
    return Board(
        id=row["id"],
        slug=row["slug"],
        title=row["title"],
        description=row["description"] or "",
        stage=BoardStage(row["stage"]),
        owner_id=row["owner_id"],
        owner_email=row["owner_email"],
        role=role,
        node_count=node_count,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_member(row: sqlite3.Row) -> BoardMember:
    return BoardMember(
        board_id=row["board_id"],
        user_id=row["user_id"],
        email=row["email"],
        display_name=row["display_name"],
        role=BoardRole(row["role"]),
        status=MemberStatus(row["status"]),
        invited_by=row["invited_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_invite(row: sqlite3.Row) -> BoardInvite:
    return BoardInvite(
        code=row["code"],
        board_id=row["board_id"],
        role=BoardRole(row["role"]),
        require_approval=bool(row["require_approval"]),
        created_by=row["created_by"],
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
    )


def _row_to_agent_key(row: sqlite3.Row) -> BoardAgentKey:
    return BoardAgentKey(
        id=row["id"],
        board_id=row["board_id"],
        name=row["name"],
        key_prefix=row["key_prefix"],
        created_by=row["created_by"],
        created_by_email=row["created_by_email"],
        scopes=json.loads(row["scopes_json"]),
        revoked_at=row["revoked_at"],
        last_used_at=row["last_used_at"],
        created_at=row["created_at"],
    )


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

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table_name: str) -> List[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return [str(r["name"]) for r in rows]

    def _migrate_legacy_tables_if_needed(self, conn: sqlite3.Connection) -> None:
        """Upgrade legacy single-board SQLite tables to multi-board composite keys cleanly."""
        node_cols = self._table_columns(conn, "nodes")
        if node_cols and "board_id" not in node_cols:
            conn.executescript("""
                ALTER TABLE nodes RENAME TO nodes_legacy;
                ALTER TABLE edges RENAME TO edges_legacy;
                ALTER TABLE events RENAME TO events_legacy;
                ALTER TABLE workspace_users RENAME TO workspace_users_legacy;

                CREATE TABLE nodes (
                    board_id TEXT NOT NULL DEFAULT 'default',
                    id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    assigned_to TEXT,
                    version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (board_id, id)
                );

                CREATE TABLE edges (
                    board_id TEXT NOT NULL DEFAULT 'default',
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    PRIMARY KEY (board_id, source, target)
                );

                CREATE TABLE events (
                    event_id TEXT PRIMARY KEY,
                    board_id TEXT NOT NULL DEFAULT 'default',
                    idempotency_key TEXT UNIQUE,
                    event_type TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    timestamp REAL NOT NULL
                );

                CREATE TABLE workspace_users (
                    board_id TEXT NOT NULL DEFAULT 'default',
                    user_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    client_host TEXT,
                    status TEXT NOT NULL,
                    last_task_id TEXT,
                    joined_at REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    PRIMARY KEY (board_id, user_id)
                );

                INSERT INTO nodes (board_id, id, title, type, status, assigned_to, version, payload_json, created_at, updated_at)
                SELECT 'default', id, title, type, status, assigned_to, version, payload_json, created_at, updated_at FROM nodes_legacy;

                INSERT INTO edges (board_id, source, target, relation)
                SELECT 'default', source, target, relation FROM edges_legacy;

                INSERT INTO events (event_id, board_id, idempotency_key, event_type, node_id, actor, data_json, timestamp)
                SELECT event_id, 'default', idempotency_key, event_type, node_id, actor, data_json, timestamp FROM events_legacy;

                INSERT INTO workspace_users (board_id, user_id, display_name, tool_name, client_host, status, last_task_id, joined_at, last_seen)
                SELECT 'default', user_id, display_name, tool_name, client_host, status, last_task_id, joined_at, last_seen FROM workspace_users_legacy;

                DROP TABLE nodes_legacy;
                DROP TABLE edges_legacy;
                DROP TABLE events_legacy;
                DROP TABLE workspace_users_legacy;
            """)

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            self._migrate_legacy_tables_if_needed(conn)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS boards (
                    id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL DEFAULT 'building',
                    owner_id TEXT NOT NULL,
                    owner_email TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS board_members (
                    board_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    email TEXT,
                    display_name TEXT,
                    role TEXT NOT NULL DEFAULT 'editor',
                    status TEXT NOT NULL DEFAULT 'pending',
                    invited_by TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (board_id, user_id),
                    FOREIGN KEY (board_id) REFERENCES boards(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS board_invites (
                    code TEXT PRIMARY KEY,
                    board_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'editor',
                    require_approval INTEGER NOT NULL DEFAULT 1,
                    created_by TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    revoked_at REAL,
                    FOREIGN KEY (board_id) REFERENCES boards(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS board_agent_keys (
                    id TEXT PRIMARY KEY,
                    board_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    key_prefix TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    created_by TEXT NOT NULL,
                    created_by_email TEXT,
                    scopes_json TEXT NOT NULL,
                    revoked_at REAL,
                    last_used_at REAL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY (board_id) REFERENCES boards(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    board_id TEXT NOT NULL DEFAULT 'default',
                    idempotency_key TEXT UNIQUE,
                    event_type TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    timestamp REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS nodes (
                    board_id TEXT NOT NULL DEFAULT 'default',
                    id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    assigned_to TEXT,
                    version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (board_id, id)
                );

                CREATE TABLE IF NOT EXISTS edges (
                    board_id TEXT NOT NULL DEFAULT 'default',
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    PRIMARY KEY (board_id, source, target)
                );

                CREATE TABLE IF NOT EXISTS workspace_users (
                    board_id TEXT NOT NULL DEFAULT 'default',
                    user_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    client_host TEXT,
                    status TEXT NOT NULL,
                    last_task_id TEXT,
                    joined_at REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    PRIMARY KEY (board_id, user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_events_board_node ON events(board_id, node_id);
                CREATE INDEX IF NOT EXISTS idx_events_idempotency ON events(idempotency_key);
                CREATE INDEX IF NOT EXISTS idx_agent_keys_hash ON board_agent_keys(key_hash);
                CREATE INDEX IF NOT EXISTS idx_members_user ON board_members(user_id, status);
            """)

    # -----------------------------------------------------------------------
    # Boards, Friend Memberships, Invites & Persistent Agent Keys
    # -----------------------------------------------------------------------

    def create_board(
        self,
        title: str,
        owner_id: str,
        owner_email: Optional[str] = None,
        owner_display_name: Optional[str] = None,
        slug: Optional[str] = None,
        description: str = "",
        stage: BoardStage = BoardStage.BUILDING,
        board_id: Optional[str] = None,
    ) -> Board:
        now = time.time()
        clean_title = title.strip() or "Untitled Board"
        resolved_id = board_id or f"brd_{uuid.uuid4().hex[:8]}"
        raw_slug = slug or re.sub(r"[^a-z0-9]+", "-", clean_title.lower()).strip("-")[:24] or "proj"
        resolved_stage = stage.value if isinstance(stage, BoardStage) else str(stage)
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO boards (id, slug, title, description, stage, owner_id, owner_email, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (resolved_id, raw_slug, clean_title, description, resolved_stage, owner_id, owner_email, now, now),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO board_members (
                    board_id, user_id, email, display_name, role, status, invited_by, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'owner', 'approved', ?, ?, ?)
                """,
                (resolved_id, owner_id, owner_email, owner_display_name or owner_email or owner_id, owner_id, now, now),
            )
            conn.commit()
        return Board(
            id=resolved_id,
            slug=raw_slug,
            title=clean_title,
            description=description,
            stage=BoardStage(resolved_stage),
            owner_id=owner_id,
            owner_email=owner_email,
            role=BoardRole.OWNER,
            node_count=0,
            created_at=now,
            updated_at=now,
        )

    def get_board(self, board_id: str) -> Optional[Board]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM boards WHERE id = ?", (board_id,)).fetchone()
            if not row:
                return None
            cnt = conn.execute("SELECT COUNT(*) AS c FROM nodes WHERE board_id = ?", (board_id,)).fetchone()["c"]
            return _row_to_board(row, node_count=cnt)

    def list_boards_for_user(self, user_id: str) -> List[Board]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT b.*, m.role AS member_role,
                       (SELECT COUNT(*) FROM nodes n WHERE n.board_id = b.id) AS node_count
                FROM boards b
                JOIN board_members m ON m.board_id = b.id
                WHERE m.user_id = ? AND m.status = 'approved'
                ORDER BY b.updated_at DESC, b.created_at DESC
                """,
                (user_id,),
            ).fetchall()
            return [
                _row_to_board(row, role=BoardRole(row["member_role"]), node_count=row["node_count"])
                for row in rows
            ]

    def list_all_boards(self) -> List[Board]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT b.*, (SELECT COUNT(*) FROM nodes n WHERE n.board_id = b.id) AS node_count
                FROM boards b
                ORDER BY b.updated_at DESC, b.created_at DESC
                """
            ).fetchall()
            return [_row_to_board(row, node_count=row["node_count"]) for row in rows]

    def update_board(
        self,
        board_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        stage: Optional[BoardStage] = None,
    ) -> Optional[Board]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM boards WHERE id = ?", (board_id,)).fetchone()
            if not row:
                return None
            next_title = title.strip() if isinstance(title, str) and title.strip() else row["title"]
            next_desc = description if description is not None else row["description"]
            next_stage = (
                stage.value if isinstance(stage, BoardStage) else (stage or row["stage"])
            )
            now = time.time()
            conn.execute(
                "UPDATE boards SET title = ?, description = ?, stage = ?, updated_at = ? WHERE id = ?",
                (next_title, next_desc, next_stage, now, board_id),
            )
            conn.commit()
        return self.get_board(board_id)

    def delete_board(self, board_id: str) -> bool:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM edges WHERE board_id = ?", (board_id,))
            conn.execute("DELETE FROM events WHERE board_id = ?", (board_id,))
            conn.execute("DELETE FROM nodes WHERE board_id = ?", (board_id,))
            conn.execute("DELETE FROM workspace_users WHERE board_id = ?", (board_id,))
            cur = conn.execute("DELETE FROM boards WHERE id = ?", (board_id,))
            conn.commit()
            return cur.rowcount > 0

    def get_board_member(self, board_id: str, user_id: str) -> Optional[BoardMember]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM board_members WHERE board_id = ? AND user_id = ?",
                (board_id, user_id),
            ).fetchone()
            return _row_to_member(row) if row else None

    def list_board_members(self, board_id: str) -> List[BoardMember]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM board_members WHERE board_id = ? ORDER BY created_at ASC",
                (board_id,),
            ).fetchall()
            return [_row_to_member(row) for row in rows]

    def upsert_board_member(
        self,
        board_id: str,
        user_id: str,
        email: Optional[str] = None,
        display_name: Optional[str] = None,
        role: BoardRole = BoardRole.EDITOR,
        status: MemberStatus = MemberStatus.PENDING,
        invited_by: Optional[str] = None,
    ) -> BoardMember:
        now = time.time()
        role_val = role.value if isinstance(role, BoardRole) else str(role)
        status_val = status.value if isinstance(status, MemberStatus) else str(status)
        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT created_at FROM board_members WHERE board_id = ? AND user_id = ?",
                (board_id, user_id),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO board_members (board_id, user_id, email, display_name, role, status, invited_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(board_id, user_id) DO UPDATE SET
                    email=COALESCE(excluded.email, board_members.email),
                    display_name=COALESCE(excluded.display_name, board_members.display_name),
                    role=excluded.role,
                    status=excluded.status,
                    invited_by=COALESCE(excluded.invited_by, board_members.invited_by),
                    updated_at=excluded.updated_at
                """,
                (board_id, user_id, email, display_name, role_val, status_val, invited_by, created_at, now),
            )
            if status_val == MemberStatus.REVOKED.value or role_val == BoardRole.VIEWER.value:
                conn.execute(
                    "UPDATE board_agent_keys SET revoked_at = COALESCE(revoked_at, ?) WHERE board_id = ? AND created_by = ?",
                    (now, board_id, user_id),
                )
            conn.commit()
        member = self.get_board_member(board_id, user_id)
        assert member is not None
        return member

    def remove_board_member(self, board_id: str, user_id: str) -> Tuple[bool, int]:
        """Remove a member from a board and cascade-revoke all AI agent keys they created on it."""
        now = time.time()
        with self._get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM board_members WHERE board_id = ? AND user_id = ?",
                (board_id, user_id),
            )
            revoked_cur = conn.execute(
                "UPDATE board_agent_keys SET revoked_at = COALESCE(revoked_at, ?) WHERE board_id = ? AND created_by = ? AND revoked_at IS NULL",
                (now, board_id, user_id),
            )
            conn.commit()
            return cur.rowcount > 0, revoked_cur.rowcount

    def create_invite(
        self,
        board_id: str,
        created_by: str,
        role: BoardRole = BoardRole.EDITOR,
        require_approval: bool = True,
    ) -> BoardInvite:
        now = time.time()
        code = f"inv-{uuid.uuid4().hex[:10]}"
        role_val = role.value if isinstance(role, BoardRole) else str(role)
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO board_invites (code, board_id, role, require_approval, created_by, created_at, revoked_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (code, board_id, role_val, 1 if require_approval else 0, created_by, now),
            )
            conn.commit()
        return BoardInvite(
            code=code,
            board_id=board_id,
            role=BoardRole(role_val),
            require_approval=require_approval,
            created_by=created_by,
            created_at=now,
        )

    def get_invite(self, code: str) -> Optional[BoardInvite]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM board_invites WHERE code = ?", (code.strip(),)).fetchone()
            return _row_to_invite(row) if row else None

    def list_invites(self, board_id: str) -> List[BoardInvite]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM board_invites WHERE board_id = ? AND revoked_at IS NULL ORDER BY created_at DESC",
                (board_id,),
            ).fetchall()
            return [_row_to_invite(row) for row in rows]

    def create_agent_key(
        self,
        board_id: str,
        name: str,
        key_prefix: str,
        key_hash: str,
        created_by: str,
        created_by_email: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> BoardAgentKey:
        now = time.time()
        key_id = f"key_{uuid.uuid4().hex[:8]}"
        resolved_scopes = scopes or ["tasks:read", "tasks:claim", "tasks:submit", "nodes:create"]
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO board_agent_keys (
                    id, board_id, name, key_prefix, key_hash, created_by, created_by_email, scopes_json, revoked_at, last_used_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (
                    key_id,
                    board_id,
                    name.strip() or "synapse-agent",
                    key_prefix,
                    key_hash,
                    created_by,
                    created_by_email,
                    json.dumps(resolved_scopes),
                    now,
                ),
            )
            conn.commit()
        return BoardAgentKey(
            id=key_id,
            board_id=board_id,
            name=name.strip() or "synapse-agent",
            key_prefix=key_prefix,
            created_by=created_by,
            created_by_email=created_by_email,
            scopes=resolved_scopes,
            created_at=now,
        )

    def list_agent_keys(self, board_id: str) -> List[BoardAgentKey]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM board_agent_keys WHERE board_id = ? ORDER BY created_at DESC",
                (board_id,),
            ).fetchall()
            return [_row_to_agent_key(row) for row in rows]

    def lookup_active_agent_key(self, key_hash: str) -> Optional[BoardAgentKey]:
        """Return agent key ONLY if unrevoked AND its creator is still an approved editor/owner on the board."""
        now = time.time()
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT k.*
                FROM board_agent_keys k
                JOIN board_members m
                  ON m.board_id = k.board_id AND m.user_id = k.created_by
                WHERE k.key_hash = ?
                  AND k.revoked_at IS NULL
                  AND m.status = 'approved'
                  AND m.role IN ('owner', 'editor')
                """,
                (key_hash,),
            ).fetchone()
            if not row:
                return None
            conn.execute("UPDATE board_agent_keys SET last_used_at = ? WHERE id = ?", (now, row["id"]))
            conn.commit()
            return _row_to_agent_key(row)

    def revoke_agent_key(self, board_id: str, key_id: str) -> bool:
        now = time.time()
        with self._get_connection() as conn:
            cur = conn.execute(
                "UPDATE board_agent_keys SET revoked_at = COALESCE(revoked_at, ?) WHERE board_id = ? AND id = ?",
                (now, board_id, key_id),
            )
            conn.commit()
            return cur.rowcount > 0

    # -----------------------------------------------------------------------
    # Board-Scoped DAG Events, Nodes, Edges & Workspace Users
    # -----------------------------------------------------------------------

    def check_idempotency(self, idempotency_key: str, board_id: Optional[str] = None) -> Optional[Event]:
        """Check if an operation with this idempotency key was already executed."""
        if not idempotency_key:
            return None
        with self._get_connection() as conn:
            if board_id is not None:
                row = conn.execute(
                    "SELECT * FROM events WHERE idempotency_key = ? AND board_id = ?",
                    (idempotency_key, board_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM events WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
            if row:
                return _row_to_event(row)
        return None

    def record_event(
        self,
        event_type: str,
        node_id: str,
        actor: str,
        data: Dict[str, Any],
        idempotency_key: Optional[str] = None,
        board_id: str = "default",
    ) -> Tuple[Event, bool]:
        """
        Record an event idempotently.
        Returns (Event, is_new).
        If idempotency_key exists, returns (existing_event, False).
        """
        if idempotency_key:
            existing = self.check_idempotency(idempotency_key, board_id=board_id)
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
                    INSERT INTO events (event_id, board_id, idempotency_key, event_type, node_id, actor, data_json, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (event_id, board_id, key, event_type, node_id, actor, data_json, ts),
                )
            except sqlite3.IntegrityError:
                row = conn.execute("SELECT * FROM events WHERE idempotency_key = ?", (key,)).fetchone()
                if row:
                    return _row_to_event(row), False
                raise

        event = Event(
            event_id=event_id,
            idempotency_key=key,
            event_type=event_type,
            node_id=node_id,
            actor=actor,
            data=data,
            board_id=board_id,
            timestamp=ts,
        )
        return event, True

    def upsert_node(self, node: Node, board_id: Optional[str] = None) -> None:
        resolved_board = board_id or node.board_id or "default"
        with self._get_connection() as conn:
            conn.execute(_UPSERT_NODE_SQL, _node_params(node, board_id=resolved_board))

    def get_node(self, node_id: str, board_id: str = "default") -> Optional[Node]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM nodes WHERE board_id = ? AND id = ?",
                (board_id, node_id),
            ).fetchone()
            return _row_to_node(row) if row else None

    def list_nodes(self, board_id: str = "default") -> List[Node]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM nodes WHERE board_id = ? ORDER BY created_at ASC",
                (board_id,),
            ).fetchall()
            return [_row_to_node(row) for row in rows]

    def add_edge(self, source: str, target: str, relation: str = "relies_on", board_id: str = "default") -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO edges (board_id, source, target, relation)
                VALUES (?, ?, ?, ?)
                """,
                (board_id, source, target, relation),
            )

    def list_edges(self, board_id: str = "default") -> List[Edge]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM edges WHERE board_id = ?", (board_id,)).fetchall()
            return [
                Edge(source=row["source"], target=row["target"], relation=row["relation"], board_id=row["board_id"])
                for row in rows
            ]

    def get_events_for_node(self, node_id: str, board_id: str = "default") -> List[Event]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE board_id = ? AND node_id = ? ORDER BY timestamp ASC",
                (board_id, node_id),
            ).fetchall()
            return [_row_to_event(row) for row in rows]

    def list_events(self, board_id: str = "default") -> List[Event]:
        """Return all persisted events for a board ordered chronologically by timestamp ASC."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE board_id = ? ORDER BY timestamp ASC",
                (board_id,),
            ).fetchall()
            return [_row_to_event(row) for row in rows]

    @staticmethod
    def _next_guest_identity(conn: sqlite3.Connection, board_id: str = "default") -> Tuple[str, str]:
        rows = conn.execute(
            "SELECT user_id, display_name FROM workspace_users WHERE board_id = ?",
            (board_id,),
        ).fetchall()
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
        board_id: str = "default",
    ) -> Tuple[WorkspaceUser, bool]:
        """Insert or update a workspace participant scoped to a board, preserving joined_at."""
        now = time.time()
        clean_uid = user_id.strip() if isinstance(user_id, str) and user_id.strip() else None
        clean_name = display_name.strip() if isinstance(display_name, str) and display_name.strip() else None
        clean_tool = tool_name.strip() if isinstance(tool_name, str) and tool_name.strip() else "generic_tool"
        clean_host = client_host.strip() if isinstance(client_host, str) and client_host.strip() else None
        clean_task = last_task_id.strip() if isinstance(last_task_id, str) and last_task_id.strip() else None

        with self._get_connection() as conn:
            auto_display: Optional[str] = None
            if not clean_uid:
                clean_uid, auto_display = self._next_guest_identity(conn, board_id=board_id)

            existing = conn.execute(
                "SELECT * FROM workspace_users WHERE board_id = ? AND user_id = ?",
                (board_id, clean_uid),
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
                    board_id,
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
            board_id=board_id,
            joined_at=joined_at,
            last_seen=now,
        )
        return user, is_new

    def list_workspace_users(self, board_id: str = "default") -> List[WorkspaceUser]:
        """Return all workspace users for a board ordered by joined_at ASC."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM workspace_users WHERE board_id = ? ORDER BY joined_at ASC",
                (board_id,),
            ).fetchall()
            return [_row_to_workspace_user(row) for row in rows]

    def export_snapshot(self, board_id: str = "default") -> GraphSnapshot:
        """Export nodes, edges, events, and users for a board as a portable GraphSnapshot."""
        with self._get_connection() as conn:
            node_rows = conn.execute(
                "SELECT * FROM nodes WHERE board_id = ? ORDER BY created_at ASC",
                (board_id,),
            ).fetchall()
            edge_rows = conn.execute("SELECT * FROM edges WHERE board_id = ?", (board_id,)).fetchall()
            event_rows = conn.execute(
                "SELECT * FROM events WHERE board_id = ? ORDER BY timestamp ASC",
                (board_id,),
            ).fetchall()
            user_rows = conn.execute(
                "SELECT * FROM workspace_users WHERE board_id = ? ORDER BY joined_at ASC",
                (board_id,),
            ).fetchall()
            return GraphSnapshot(
                board_id=board_id,
                nodes=[_row_to_node(row) for row in node_rows],
                edges=[
                    Edge(source=row["source"], target=row["target"], relation=row["relation"], board_id=row["board_id"])
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
        board_id: str = "default",
    ) -> Dict[str, int]:
        """
        Atomically import nodes, edges, events, and users for a board in a single SQLite transaction.
        Re-imports are idempotent on event_id and idempotency_key via INSERT OR IGNORE.
        """
        with self._get_connection() as conn:
            if replace:
                conn.execute("DELETE FROM edges WHERE board_id = ?", (board_id,))
                conn.execute("DELETE FROM events WHERE board_id = ?", (board_id,))
                conn.execute("DELETE FROM nodes WHERE board_id = ?", (board_id,))
                conn.execute("DELETE FROM workspace_users WHERE board_id = ?", (board_id,))

            for node in nodes:
                conn.execute(_UPSERT_NODE_SQL, _node_params(node, board_id=board_id))

            for edge in edges:
                conn.execute(
                    "INSERT OR IGNORE INTO edges (board_id, source, target, relation) VALUES (?, ?, ?, ?)",
                    (board_id, edge.source, edge.target, edge.relation),
                )

            inserted_events = 0
            for event in events:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO events (event_id, board_id, idempotency_key, event_type, node_id, actor, data_json, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        board_id,
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
                        board_id,
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

    def clear(self, board_id: Optional[str] = "default") -> None:
        """Clear stored nodes, edges, events, and workspace users for a board (or all if None)."""
        with self._get_connection() as conn:
            if board_id is None:
                conn.execute("DELETE FROM edges;")
                conn.execute("DELETE FROM events;")
                conn.execute("DELETE FROM nodes;")
                conn.execute("DELETE FROM workspace_users;")
            else:
                conn.execute("DELETE FROM edges WHERE board_id = ?", (board_id,))
                conn.execute("DELETE FROM events WHERE board_id = ?", (board_id,))
                conn.execute("DELETE FROM nodes WHERE board_id = ?", (board_id,))
                conn.execute("DELETE FROM workspace_users WHERE board_id = ?", (board_id,))
            conn.commit()
