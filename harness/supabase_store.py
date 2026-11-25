"""
Supabase PostgREST Store backend for OmniHarness.
Mirrors the EventStore contract over Supabase REST (/rest/v1) using SUPABASE_SERVICE_ROLE_KEY,
giving Render deployments persistent multi-board Postgres storage across container restarts.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx

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


class SupabaseRestStore:
    """
    Synchronous PostgREST store implementing the same interface as EventStore.
    Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_SECRET_KEY).
    """

    def __init__(
        self,
        supabase_url: Optional[str] = None,
        service_key: Optional[str] = None,
        client: Optional[httpx.Client] = None,
    ):
        self.supabase_url = (supabase_url or os.environ.get("SUPABASE_URL") or "").rstrip("/")
        self.service_key = (
            service_key
            or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
            or os.environ.get("SUPABASE_SECRET_KEY")
            or ""
        )
        if not self.supabase_url or not self.service_key:
            raise ValueError("SupabaseRestStore requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")

        self._rest_url = f"{self.supabase_url}/rest/v1"
        self._client = client or httpx.Client(
            base_url=self._rest_url,
            timeout=15.0,
            headers={
                "apikey": self.service_key,
                "Authorization": f"Bearer {self.service_key}",
                "Content-Type": "application/json",
            },
        )

    def _get(self, table: str, params: Dict[str, str]) -> List[Dict[str, Any]]:
        res = self._client.get(f"/{table}", params=params)
        res.raise_for_status()
        data = res.json()
        return data if isinstance(data, list) else []

    def _upsert(
        self,
        table: str,
        rows: Any,
        on_conflict: Optional[str] = None,
        ignore_duplicates: bool = False,
        returning: bool = False,
    ) -> List[Dict[str, Any]]:
        prefer_parts = []
        if ignore_duplicates:
            prefer_parts.append("resolution=ignore-duplicates")
        else:
            prefer_parts.append("resolution=merge-duplicates")
        prefer_parts.append("return=representation" if returning else "return=minimal")
        headers = {"Prefer": ",".join(prefer_parts)}
        params = {"on_conflict": on_conflict} if on_conflict else None
        res = self._client.post(f"/{table}", params=params, json=rows, headers=headers)
        res.raise_for_status()
        if returning and res.text:
            data = res.json()
            return data if isinstance(data, list) else []
        return []

    def _patch(self, table: str, params: Dict[str, str], payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        res = self._client.patch(
            f"/{table}",
            params=params,
            json=payload,
            headers={"Prefer": "return=representation"},
        )
        res.raise_for_status()
        if res.text:
            data = res.json()
            return data if isinstance(data, list) else []
        return []

    def _delete(self, table: str, params: Dict[str, str]) -> List[Dict[str, Any]]:
        res = self._client.delete(
            f"/{table}",
            params=params,
            headers={"Prefer": "return=representation"},
        )
        res.raise_for_status()
        if res.text:
            data = res.json()
            return data if isinstance(data, list) else []
        return []

    @staticmethod
    def _to_node(row: Dict[str, Any]) -> Node:
        return Node(
            id=row["id"],
            title=row["title"],
            type=NodeType(row["type"]),
            status=NodeStatus(row["status"]),
            assigned_to=row.get("assigned_to"),
            version=int(row.get("version", 1)),
            payload=row.get("payload") if isinstance(row.get("payload"), dict) else {},
            board_id=row.get("board_id", "default"),
            created_at=float(row.get("created_at", time.time())),
            updated_at=float(row.get("updated_at", time.time())),
        )

    @staticmethod
    def _to_event(row: Dict[str, Any]) -> Event:
        return Event(
            event_id=row["event_id"],
            idempotency_key=row.get("idempotency_key") or "",
            event_type=row["event_type"],
            node_id=row["node_id"],
            actor=row["actor"],
            data=row.get("data") if isinstance(row.get("data"), dict) else {},
            board_id=row.get("board_id", "default"),
            timestamp=float(row.get("timestamp", time.time())),
        )

    @staticmethod
    def _to_user(row: Dict[str, Any]) -> WorkspaceUser:
        return WorkspaceUser(
            user_id=row["user_id"],
            display_name=row["display_name"],
            tool_name=row.get("tool_name", "generic_tool"),
            client_host=row.get("client_host"),
            status=row.get("status", "joined"),
            last_task_id=row.get("last_task_id"),
            board_id=row.get("board_id", "default"),
            joined_at=float(row.get("joined_at", time.time())),
            last_seen=float(row.get("last_seen", time.time())),
        )

    @staticmethod
    def _to_board(row: Dict[str, Any], role: Optional[BoardRole] = None, node_count: int = 0) -> Board:
        return Board(
            id=row["id"],
            slug=row["slug"],
            title=row["title"],
            description=row.get("description") or "",
            stage=BoardStage(row.get("stage", "building")),
            owner_id=row["owner_id"],
            owner_email=row.get("owner_email"),
            role=role,
            node_count=node_count,
            created_at=float(row.get("created_at", time.time())),
            updated_at=float(row.get("updated_at", time.time())),
        )

    @staticmethod
    def _to_member(row: Dict[str, Any]) -> BoardMember:
        return BoardMember(
            board_id=row["board_id"],
            user_id=row["user_id"],
            email=row.get("email"),
            display_name=row.get("display_name"),
            role=BoardRole(row["role"]),
            status=MemberStatus(row["status"]),
            invited_by=row.get("invited_by"),
            created_at=float(row.get("created_at", time.time())),
            updated_at=float(row.get("updated_at", time.time())),
        )

    @staticmethod
    def _to_invite(row: Dict[str, Any]) -> BoardInvite:
        return BoardInvite(
            code=row["code"],
            board_id=row["board_id"],
            role=BoardRole(row["role"]),
            require_approval=bool(row.get("require_approval", True)),
            created_by=row["created_by"],
            created_at=float(row.get("created_at", time.time())),
            revoked_at=row.get("revoked_at"),
        )

    @staticmethod
    def _to_agent_key(row: Dict[str, Any]) -> BoardAgentKey:
        return BoardAgentKey(
            id=row["id"],
            board_id=row["board_id"],
            name=row["name"],
            key_prefix=row["key_prefix"],
            created_by=row["created_by"],
            created_by_email=row.get("created_by_email"),
            scopes=row.get("scopes") if isinstance(row.get("scopes"), list) else [],
            revoked_at=row.get("revoked_at"),
            last_used_at=row.get("last_used_at"),
            created_at=float(row.get("created_at", time.time())),
        )

    # -----------------------------------------------------------------------
    # Boards, Members, Invites & Agent Keys
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
        self._upsert(
            "boards",
            {
                "id": resolved_id,
                "slug": raw_slug,
                "title": clean_title,
                "description": description,
                "stage": resolved_stage,
                "owner_id": owner_id,
                "owner_email": owner_email,
                "created_at": now,
                "updated_at": now,
            },
            on_conflict="id",
        )
        self._upsert(
            "board_members",
            {
                "board_id": resolved_id,
                "user_id": owner_id,
                "email": owner_email,
                "display_name": owner_display_name or owner_email or owner_id,
                "role": "owner",
                "status": "approved",
                "invited_by": owner_id,
                "created_at": now,
                "updated_at": now,
            },
            on_conflict="board_id,user_id",
        )
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
        rows = self._get("boards", {"id": f"eq.{board_id}", "select": "*"})
        if not rows:
            return None
        nodes = self._get("dag_nodes", {"board_id": f"eq.{board_id}", "select": "id"})
        return self._to_board(rows[0], node_count=len(nodes))

    def list_boards_for_user(self, user_id: str) -> List[Board]:
        memberships = self._get(
            "board_members",
            {"user_id": f"eq.{user_id}", "status": "eq.approved", "select": "board_id,role"},
        )
        if not memberships:
            return []
        role_map = {m["board_id"]: BoardRole(m["role"]) for m in memberships}
        board_ids = ",".join(role_map.keys())
        boards = self._get("boards", {"id": f"in.({board_ids})", "order": "updated_at.desc", "select": "*"})
        nodes = self._get("dag_nodes", {"board_id": f"in.({board_ids})", "select": "board_id"})
        counts: Dict[str, int] = {}
        for n in nodes:
            counts[n["board_id"]] = counts.get(n["board_id"], 0) + 1
        return [self._to_board(b, role=role_map.get(b["id"]), node_count=counts.get(b["id"], 0)) for b in boards]

    def list_all_boards(self) -> List[Board]:
        boards = self._get("boards", {"order": "updated_at.desc", "select": "*"})
        return [self._to_board(b) for b in boards]

    def update_board(
        self,
        board_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        stage: Optional[BoardStage] = None,
    ) -> Optional[Board]:
        payload: Dict[str, Any] = {"updated_at": time.time()}
        if isinstance(title, str) and title.strip():
            payload["title"] = title.strip()
        if description is not None:
            payload["description"] = description
        if stage is not None:
            payload["stage"] = stage.value if isinstance(stage, BoardStage) else str(stage)
        rows = self._patch("boards", {"id": f"eq.{board_id}"}, payload)
        if not rows:
            return None
        return self.get_board(board_id)

    def delete_board(self, board_id: str) -> bool:
        rows = self._delete("boards", {"id": f"eq.{board_id}"})
        return len(rows) > 0

    def get_board_member(self, board_id: str, user_id: str) -> Optional[BoardMember]:
        rows = self._get(
            "board_members",
            {"board_id": f"eq.{board_id}", "user_id": f"eq.{user_id}", "select": "*"},
        )
        return self._to_member(rows[0]) if rows else None

    def list_board_members(self, board_id: str) -> List[BoardMember]:
        rows = self._get(
            "board_members",
            {"board_id": f"eq.{board_id}", "order": "created_at.asc", "select": "*"},
        )
        return [self._to_member(r) for r in rows]

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
        existing = self.get_board_member(board_id, user_id)
        role_val = role.value if isinstance(role, BoardRole) else str(role)
        status_val = status.value if isinstance(status, MemberStatus) else str(status)
        rows = self._upsert(
            "board_members",
            {
                "board_id": board_id,
                "user_id": user_id,
                "email": email or (existing.email if existing else None),
                "display_name": display_name or (existing.display_name if existing else None),
                "role": role_val,
                "status": status_val,
                "invited_by": invited_by or (existing.invited_by if existing else None),
                "created_at": existing.created_at if existing else now,
                "updated_at": now,
            },
            on_conflict="board_id,user_id",
            returning=True,
        )
        if status_val == MemberStatus.REVOKED.value or role_val == BoardRole.VIEWER.value:
            self._patch(
                "board_agent_keys",
                {"board_id": f"eq.{board_id}", "created_by": f"eq.{user_id}", "revoked_at": "is.null"},
                {"revoked_at": now},
            )
        return self._to_member(rows[0])

    def remove_board_member(self, board_id: str, user_id: str) -> Tuple[bool, int]:
        now = time.time()
        deleted = self._delete("board_members", {"board_id": f"eq.{board_id}", "user_id": f"eq.{user_id}"})
        revoked = self._patch(
            "board_agent_keys",
            {"board_id": f"eq.{board_id}", "created_by": f"eq.{user_id}", "revoked_at": "is.null"},
            {"revoked_at": now},
        )
        return len(deleted) > 0, len(revoked)

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
        self._upsert(
            "board_invites",
            {
                "code": code,
                "board_id": board_id,
                "role": role_val,
                "require_approval": require_approval,
                "created_by": created_by,
                "created_at": now,
                "revoked_at": None,
            },
            on_conflict="code",
        )
        return BoardInvite(
            code=code,
            board_id=board_id,
            role=BoardRole(role_val),
            require_approval=require_approval,
            created_by=created_by,
            created_at=now,
        )

    def get_invite(self, code: str) -> Optional[BoardInvite]:
        rows = self._get("board_invites", {"code": f"eq.{code.strip()}", "select": "*"})
        return self._to_invite(rows[0]) if rows else None

    def list_invites(self, board_id: str) -> List[BoardInvite]:
        rows = self._get(
            "board_invites",
            {"board_id": f"eq.{board_id}", "revoked_at": "is.null", "order": "created_at.desc", "select": "*"},
        )
        return [self._to_invite(r) for r in rows]

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
        self._upsert(
            "board_agent_keys",
            {
                "id": key_id,
                "board_id": board_id,
                "name": name.strip() or "synapse-agent",
                "key_prefix": key_prefix,
                "key_hash": key_hash,
                "created_by": created_by,
                "created_by_email": created_by_email,
                "scopes": resolved_scopes,
                "revoked_at": None,
                "last_used_at": None,
                "created_at": now,
            },
            on_conflict="id",
        )
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
        rows = self._get(
            "board_agent_keys",
            {"board_id": f"eq.{board_id}", "order": "created_at.desc", "select": "*"},
        )
        return [self._to_agent_key(r) for r in rows]

    def lookup_active_agent_key(self, key_hash: str) -> Optional[BoardAgentKey]:
        rows = self._get(
            "board_agent_keys",
            {"key_hash": f"eq.{key_hash}", "revoked_at": "is.null", "select": "*"},
        )
        if not rows:
            return None
        key_obj = self._to_agent_key(rows[0])
        member = self.get_board_member(key_obj.board_id, key_obj.created_by)
        if not member or member.status != MemberStatus.APPROVED or member.role not in (BoardRole.OWNER, BoardRole.EDITOR):
            return None
        self._patch("board_agent_keys", {"id": f"eq.{key_obj.id}"}, {"last_used_at": time.time()})
        return key_obj

    def revoke_agent_key(self, board_id: str, key_id: str) -> bool:
        rows = self._patch(
            "board_agent_keys",
            {"board_id": f"eq.{board_id}", "id": f"eq.{key_id}"},
            {"revoked_at": time.time()},
        )
        return len(rows) > 0

    # -----------------------------------------------------------------------
    # DAG Nodes, Edges, Events & Workspace Users
    # -----------------------------------------------------------------------

    def check_idempotency(self, idempotency_key: str, board_id: Optional[str] = None) -> Optional[Event]:
        if not idempotency_key:
            return None
        params: Dict[str, str] = {"idempotency_key": f"eq.{idempotency_key}", "select": "*"}
        if board_id is not None:
            params["board_id"] = f"eq.{board_id}"
        rows = self._get("dag_events", params)
        return self._to_event(rows[0]) if rows else None

    def record_event(
        self,
        event_type: str,
        node_id: str,
        actor: str,
        data: Dict[str, Any],
        idempotency_key: Optional[str] = None,
        board_id: str = "default",
    ) -> Tuple[Event, bool]:
        if idempotency_key:
            existing = self.check_idempotency(idempotency_key, board_id=board_id)
            if existing:
                return existing, False

        event_id = str(uuid.uuid4())
        key = idempotency_key or str(uuid.uuid4())
        ts = time.time()
        try:
            self._upsert(
                "dag_events",
                {
                    "event_id": event_id,
                    "board_id": board_id,
                    "idempotency_key": key,
                    "event_type": event_type,
                    "node_id": node_id,
                    "actor": actor,
                    "data": data,
                    "timestamp": ts,
                },
                on_conflict="idempotency_key",
                ignore_duplicates=True,
            )
        except Exception:
            existing = self.check_idempotency(key, board_id=board_id)
            if existing:
                return existing, False
            raise
        existing_after = self.check_idempotency(key, board_id=board_id)
        if existing_after and existing_after.event_id != event_id:
            return existing_after, False
        return (
            Event(
                event_id=event_id,
                idempotency_key=key,
                event_type=event_type,
                node_id=node_id,
                actor=actor,
                data=data,
                board_id=board_id,
                timestamp=ts,
            ),
            True,
        )

    def upsert_node(self, node: Node, board_id: Optional[str] = None) -> None:
        resolved_board = board_id or node.board_id or "default"
        self._upsert(
            "dag_nodes",
            {
                "board_id": resolved_board,
                "id": node.id,
                "title": node.title,
                "type": node.type.value if hasattr(node.type, "value") else str(node.type),
                "status": node.status.value if hasattr(node.status, "value") else str(node.status),
                "assigned_to": node.assigned_to,
                "version": node.version,
                "payload": node.payload,
                "created_at": node.created_at,
                "updated_at": node.updated_at,
            },
            on_conflict="board_id,id",
        )

    def get_node(self, node_id: str, board_id: str = "default") -> Optional[Node]:
        rows = self._get("dag_nodes", {"board_id": f"eq.{board_id}", "id": f"eq.{node_id}", "select": "*"})
        return self._to_node(rows[0]) if rows else None

    def list_nodes(self, board_id: str = "default") -> List[Node]:
        rows = self._get("dag_nodes", {"board_id": f"eq.{board_id}", "order": "created_at.asc", "select": "*"})
        return [self._to_node(r) for r in rows]

    def add_edge(self, source: str, target: str, relation: str = "relies_on", board_id: str = "default") -> None:
        self._upsert(
            "dag_edges",
            {"board_id": board_id, "source": source, "target": target, "relation": relation},
            on_conflict="board_id,source,target",
            ignore_duplicates=True,
        )

    def list_edges(self, board_id: str = "default") -> List[Edge]:
        rows = self._get("dag_edges", {"board_id": f"eq.{board_id}", "select": "*"})
        return [Edge(source=r["source"], target=r["target"], relation=r["relation"], board_id=r["board_id"]) for r in rows]

    def get_events_for_node(self, node_id: str, board_id: str = "default") -> List[Event]:
        rows = self._get(
            "dag_events",
            {"board_id": f"eq.{board_id}", "node_id": f"eq.{node_id}", "order": "timestamp.asc", "select": "*"},
        )
        return [self._to_event(r) for r in rows]

    def list_events(self, board_id: str = "default") -> List[Event]:
        rows = self._get("dag_events", {"board_id": f"eq.{board_id}", "order": "timestamp.asc", "select": "*"})
        return [self._to_event(r) for r in rows]

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
        now = time.time()
        existing_users = self.list_workspace_users(board_id=board_id)
        clean_uid = user_id.strip() if isinstance(user_id, str) and user_id.strip() else None
        auto_display = None
        if not clean_uid:
            used_nums = set()
            for u in existing_users:
                for val in (u.user_id, u.display_name):
                    m = re.fullmatch(r"user[- ](\d+)", str(val).strip(), flags=re.IGNORECASE)
                    if m:
                        used_nums.add(int(m.group(1)))
            idx = 1
            while idx in used_nums:
                idx += 1
            clean_uid = f"user-{idx}"
            auto_display = f"User {idx}"

        existing = next((u for u in existing_users if u.user_id == clean_uid), None)
        clean_name = display_name.strip() if isinstance(display_name, str) and display_name.strip() else None
        clean_tool = tool_name.strip() if isinstance(tool_name, str) and tool_name.strip() else "generic_tool"
        clean_host = client_host.strip() if isinstance(client_host, str) and client_host.strip() else None
        clean_task = last_task_id.strip() if isinstance(last_task_id, str) and last_task_id.strip() else None

        if existing:
            is_new = False
            joined_at = existing.joined_at
            resolved_display = clean_name or existing.display_name
            resolved_tool = clean_tool if clean_tool != "generic_tool" else existing.tool_name
            resolved_host = clean_host or existing.client_host
            resolved_task = clean_task or existing.last_task_id
            resolved_status = "working" if preserve_working and existing.status == "working" else status
        else:
            is_new = True
            joined_at = now
            resolved_display = clean_name or auto_display or clean_uid
            resolved_tool = clean_tool
            resolved_host = clean_host
            resolved_task = clean_task
            resolved_status = status

        self._upsert(
            "dag_workspace_users",
            {
                "board_id": board_id,
                "user_id": clean_uid,
                "display_name": resolved_display,
                "tool_name": resolved_tool,
                "client_host": resolved_host,
                "status": resolved_status,
                "last_task_id": resolved_task,
                "joined_at": joined_at,
                "last_seen": now,
            },
            on_conflict="board_id,user_id",
        )
        return (
            WorkspaceUser(
                user_id=clean_uid,
                display_name=resolved_display,
                tool_name=resolved_tool,
                client_host=resolved_host,
                status=resolved_status,
                last_task_id=resolved_task,
                board_id=board_id,
                joined_at=joined_at,
                last_seen=now,
            ),
            is_new,
        )

    def list_workspace_users(self, board_id: str = "default") -> List[WorkspaceUser]:
        rows = self._get(
            "dag_workspace_users",
            {"board_id": f"eq.{board_id}", "order": "joined_at.asc", "select": "*"},
        )
        return [self._to_user(r) for r in rows]

    def export_snapshot(self, board_id: str = "default") -> GraphSnapshot:
        return GraphSnapshot(
            board_id=board_id,
            nodes=self.list_nodes(board_id=board_id),
            edges=self.list_edges(board_id=board_id),
            events=self.list_events(board_id=board_id),
            users=self.list_workspace_users(board_id=board_id),
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
        if replace:
            self.clear(board_id=board_id)
        for node in nodes:
            self.upsert_node(node, board_id=board_id)
        for edge in edges:
            self.add_edge(edge.source, edge.target, edge.relation, board_id=board_id)
        inserted_events = 0
        for ev in events:
            _, is_new = self.record_event(
                event_type=ev.event_type,
                node_id=ev.node_id,
                actor=ev.actor,
                data=ev.data,
                idempotency_key=ev.idempotency_key,
                board_id=board_id,
            )
            if is_new:
                inserted_events += 1
        for u in (users or []):
            self.upsert_workspace_user(
                user_id=u.user_id,
                display_name=u.display_name,
                tool_name=u.tool_name,
                client_host=u.client_host,
                status=u.status,
                last_task_id=u.last_task_id,
                board_id=board_id,
            )
        return {
            "nodes_imported": len(nodes),
            "edges_imported": len(edges),
            "events_imported": inserted_events,
        }

    def clear(self, board_id: Optional[str] = "default") -> None:
        if board_id is None:
            return
        for tbl in ("dag_edges", "dag_events", "dag_nodes", "dag_workspace_users"):
            self._delete(tbl, {"board_id": f"eq.{board_id}"})
