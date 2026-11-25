"""
SYNAPSE FastAPI REST Backend, Multi-Board DAG Hub, Realtime WebSocket Rooms,
Supabase Auth + Friend RBAC, Persistent Revocable Agent Keys, and Hosted MCP Endpoint.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import Body, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from auth.supabase_auth import (
    AGENT_KEY_PREFIX,
    AuthPrincipal,
    SupabaseAuthClient,
    extract_bearer_token,
    generate_agent_key,
    hash_agent_key,
)
from harness.graph import (
    BoardDAGRegistry,
    CycleError,
    DAGManager,
    GraphError,
    NodeNotFoundError,
    TaskConflictError,
)
from harness.mcp_server import AuthenticatedMCPApp, create_hosted_mcp_server
from harness.models import (
    Board,
    BoardAgentKey,
    BoardInvite,
    BoardMember,
    BoardRole,
    BoardStage,
    ClaimTaskRequest,
    CreateAgentKeyRequest,
    CreateAgentKeyResponse,
    CreateBoardRequest,
    CreateInviteRequest,
    CreateNodeRequest,
    Edge,
    Event,
    GraphSnapshot,
    GraphStateResponse,
    JoinBoardRequest,
    JoinWorkspaceRequest,
    MemberStatus,
    Node,
    NodeStatus,
    NodeType,
    SeedGraphRequest,
    SubgraphResponse,
    SubmitTaskRequest,
    UpdateBoardRequest,
    UpdateMemberRequest,
    UpdateRequirementRequest,
    WorkspaceUser,
)
from harness.projects import (
    find_disk_project_file,
    is_valid_project_slug,
    list_project_links,
    node_project_slug,
    resolve_project,
)
from harness.seeds import get_seed_nodes, list_seed_templates
from harness.store import EventStore
from harness.supabase_store import SupabaseRestStore

logger = logging.getLogger("synapse.server")

EVENT_NODE_CREATED = "EVENT_NODE_CREATED"
EVENT_TASK_CLAIMED = "EVENT_TASK_CLAIMED"
EVENT_TASK_SUBMITTED = "EVENT_TASK_SUBMITTED"
EVENT_CONFLICT_DETECTED = "EVENT_CONFLICT_DETECTED"
EVENT_REQUIREMENT_UPDATED = "EVENT_REQUIREMENT_UPDATED"
EVENT_BLAST_RADIUS_ALERT = "EVENT_BLAST_RADIUS_ALERT"
EVENT_USER_JOINED = "EVENT_USER_JOINED"
EVENT_USER_UPDATED = "EVENT_USER_UPDATED"

SESSION_COOKIE_NAME = "synapse_session"
_IGNORED_USER_ACTORS = {"", "system", "seed-engine", "dag-invalidator"}


class _AuthSignupBody(BaseModel):
    email: str
    password: str
    display_name: Optional[str] = None


class _AuthLoginBody(BaseModel):
    email: str
    password: str


class _AuthOtpSendBody(BaseModel):
    email: str


class _AuthOtpVerifyBody(BaseModel):
    email: str
    token: str


class _AuthRefreshBody(BaseModel):
    refresh_token: str


class _AuthSessionBody(BaseModel):
    access_token: str


class _AuthVerifyHashBody(BaseModel):
    token_hash: str
    type: str = "magiclink"


class ConnectionManager:
    """
    Thread-safe WebSocket connection manager scoped by `board_id`.
    Ensures board mutations and conflict alerts only broadcast to authorized clients on that board.
    """

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._conn_boards: Dict[WebSocket, str] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, board_id: str = "default"):
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
            self._conn_boards[websocket] = board_id
        logger.info("WebSocket connected (board=%s). Active clients: %d", board_id, len(self.active_connections))
        await websocket.send_json({
            "event": "CONNECTED",
            "event_type": "CONNECTED",
            "board_id": board_id,
            "timestamp": time.time(),
        })

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self.active_connections.discard(websocket)
            self._conn_boards.pop(websocket, None)
        logger.info("WebSocket disconnected. Active clients: %d", len(self.active_connections))

    async def broadcast(self, message: Dict[str, Any], board_id: Optional[str] = None):
        """Broadcast message to active WebSocket clients on `board_id` (or all if None)."""
        async with self._lock:
            if board_id is None:
                connections = list(self.active_connections)
            else:
                connections = [
                    conn for conn in self.active_connections
                    if self._conn_boards.get(conn, "default") == board_id
                ]
        if not connections:
            return

        tasks = [conn.send_json(message) for conn in connections]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        dead = [conn for conn, res in zip(connections, results) if isinstance(res, Exception)]
        if dead:
            async with self._lock:
                for d in dead:
                    self.active_connections.discard(d)
                    self._conn_boards.pop(d, None)
            logger.info("Pruned %d dead WebSocket connections", len(dead))


def _project_link_fields(node: Node, request: Request) -> Dict[str, str]:
    slug = node_project_slug(node)
    if not slug:
        return {}
    base_url = str(request.base_url).rstrip("/")
    return {
        "project_slug": slug,
        "project_url": f"{base_url}/{slug}",
    }


def _set_session_cookie(response: Response, access_token: Optional[str], request: Request) -> None:
    if not access_token:
        return
    is_secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=access_token,
        httponly=True,
        samesite="lax",
        secure=is_secure,
        max_age=60 * 60 * 24 * 7,
        path="/",
    )


def _load_local_dotenv() -> None:
    """Load key=value pairs from .env in repo root without overriding real process env vars."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and val:
                os.environ.setdefault(key, val)
    except Exception:
        pass


def create_app(
    db_path: Optional[str] = None,
    dag_manager: Optional[DAGManager] = None,
    store: Optional[Any] = None,
    snapshot_file: Optional[str] = None,
    projects_root: Optional[Path] = None,
    auth_enabled: Optional[bool] = None,
    auth_client: Optional[SupabaseAuthClient] = None,
) -> FastAPI:
    """
    Factory function to initialize and configure the SYNAPSE FastAPI server.
    Supports Supabase Auth + Postgres persistence (via SUPABASE_SERVICE_ROLE_KEY) or SQLite fallback,
    multi-board DAG isolation, friend approval RBAC, persistent revocable agent keys, and hosted /mcp.
    """
    _load_local_dotenv()
    if store is not None:
        st = store
    elif (
        db_path is None
        and dag_manager is None
        and os.environ.get("SUPABASE_URL")
        and (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SECRET_KEY"))
    ):
        st = SupabaseRestStore()
    else:
        resolved_db_path = db_path if db_path is not None else os.environ.get("SYNAPSE_DB_PATH", "synapse.db")
        st = (dag_manager.store if dag_manager and dag_manager.store else EventStore(db_path=resolved_db_path))

    default_dag = dag_manager or DAGManager(store=st, board_id="default")
    registry = BoardDAGRegistry(store=st, default_dag=default_dag)

    resolved_auth_enabled = (
        auth_enabled
        if auth_enabled is not None
        else os.environ.get("SYNAPSE_AUTH_ENABLED", "true").lower() in ("1", "true", "yes")
    )
    auth = auth_client or SupabaseAuthClient()

    ws_manager = ConnectionManager()
    mutation_lock = asyncio.Lock()
    resolved_projects_root = Path(projects_root) if projects_root is not None else Path(__file__).parent.parent

    mcp_server = create_hosted_mcp_server(
        store=st,
        registry=registry,
        ws_manager=ws_manager,
        mutation_lock=mutation_lock,
    )
    mcp_streamable_app = mcp_server.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if default_dag.store is not None:
            try:
                default_dag.hydrate_from_store()
            except Exception as e:
                logger.warning("DAG hydration error on startup: %s", e)

        resolved_snapshot = (
            snapshot_file
            if snapshot_file is not None
            else os.environ.get("SYNAPSE_SNAPSHOT_FILE")
        )
        if resolved_snapshot:
            snap_path = Path(resolved_snapshot)
            if snap_path.is_file() and len(default_dag._nodes) == 0:
                try:
                    snapshot = GraphSnapshot.model_validate_json(snap_path.read_text(encoding="utf-8"))
                    counts = default_dag.import_snapshot(snapshot, replace=True)
                    logger.info("Hydrated initial snapshot from %s: %s", snap_path, counts)
                except Exception as e:
                    logger.warning("Snapshot startup import error from %s: %s", snap_path, e)

        async with mcp_server.session_manager.run():
            yield

    app = FastAPI(
        title="SYNAPSE API",
        description="Multi-Board Multi-Agent DAG Knowledge Hub with Supabase Auth, Friend RBAC, Revocable Agent Keys, and MCP.",
        version="2.0.0",
        lifespan=lifespan,
    )

    raw_cors = os.environ.get("SYNAPSE_CORS_ORIGINS", "").strip()
    allowed_origins = (
        [o.strip() for o in raw_cors.split(",") if o.strip()]
        if raw_cors
        else [
            "https://multiagentharness.onrender.com",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Api-Key", "X-Board-Id", "X-Agent-Id", "X-Tool-Name", "X-Client-Host"],
    )

    app.state.store = st
    app.state.dag = default_dag
    app.state.dag_manager = default_dag
    app.state.registry = registry
    app.state.ws_manager = ws_manager
    app.state.manager = ws_manager
    app.state.mutation_lock = mutation_lock
    app.state.auth_client = auth
    app.state.auth_enabled = resolved_auth_enabled

    # ---------------------------------------------------------
    # Auth & Board RBAC Helpers
    # ---------------------------------------------------------

    async def _resolve_principal(raw_token: Optional[str]) -> Optional[AuthPrincipal]:
        if not raw_token:
            return None
        if raw_token.startswith(AGENT_KEY_PREFIX):
            key_record = st.lookup_active_agent_key(hash_agent_key(raw_token))
            if not key_record:
                return None
            return AuthPrincipal(
                kind="agent",
                user_id=key_record.created_by,
                email=key_record.created_by_email,
                display_name=key_record.name,
                board_id=key_record.board_id,
                agent_key_id=key_record.id,
                agent_key_name=key_record.name,
                scopes=tuple(key_record.scopes),
            )
        return await auth.verify_human_jwt(raw_token)

    def _extract_token_from_request(request: Request) -> Optional[str]:
        header_token = request.headers.get("Authorization") or request.headers.get("X-Api-Key")
        query_token = request.query_params.get("token")
        cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
        return extract_bearer_token(header_token, query_token) or cookie_token

    async def _require_principal(request: Request) -> AuthPrincipal:
        if not resolved_auth_enabled:
            return AuthPrincipal(
                kind="anonymous",
                user_id="local-admin",
                email="local@synapse",
                display_name="Local Admin",
                scopes=("board:read", "board:write", "board:admin", "tasks:read", "tasks:claim", "tasks:submit", "nodes:create"),
            )
        raw_token = _extract_token_from_request(request)
        if not raw_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required. Log in on the dashboard or provide a Board Agent Key.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        principal = await _resolve_principal(raw_token)
        if not principal:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid, expired, or revoked credentials.",
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
        return principal

    def _ensure_human_default_board(principal: AuthPrincipal) -> Board:
        boards = st.list_boards_for_user(principal.user_id)
        if boards:
            return boards[0]
        return st.create_board(
            title="My First Knowledge Board",
            owner_id=principal.user_id,
            owner_email=principal.email,
            owner_display_name=principal.display_name,
            stage=BoardStage.BUILDING,
        )

    async def _authorize_board(
        request: Request,
        requested_board_id: Optional[str] = None,
        permission: str = "read",
        required_agent_scope: Optional[str] = None,
    ) -> Tuple[AuthPrincipal, str, DAGManager]:
        principal = await _require_principal(request)
        explicit_board = (
            requested_board_id
            or request.query_params.get("board_id")
            or request.headers.get("X-Board-Id")
        )

        if not resolved_auth_enabled:
            target_board = explicit_board or "default"
            return principal, target_board, registry.get_dag(target_board)

        if principal.kind == "agent":
            bound_board = principal.board_id
            if not bound_board:
                raise HTTPException(status_code=403, detail="Agent key is not bound to any board.")
            if explicit_board and explicit_board != bound_board:
                raise HTTPException(
                    status_code=403,
                    detail=f"Agent key is scoped strictly to board '{bound_board}', not '{explicit_board}'.",
                )
            if permission == "admin":
                raise HTTPException(
                    status_code=403,
                    detail="AI agent keys cannot perform board administration or destructive reset actions.",
                )
            if required_agent_scope and required_agent_scope not in principal.scopes:
                raise HTTPException(
                    status_code=403,
                    detail=f"Agent key lacks required scope '{required_agent_scope}'.",
                )
            return principal, bound_board, registry.get_dag(bound_board)

        # Human caller
        if explicit_board:
            board_id = explicit_board
        else:
            boards = st.list_boards_for_user(principal.user_id)
            if not boards:
                raise HTTPException(
                    status_code=404,
                    detail="No Knowledge Boards found for this account. Create or join a board first.",
                )
            board_id = boards[0].id

        board = st.get_board(board_id)
        if not board:
            raise HTTPException(status_code=404, detail=f"Board '{board_id}' not found.")

        member = st.get_board_member(board_id, principal.user_id)
        if not member or member.status != MemberStatus.APPROVED:
            raise HTTPException(
                status_code=403,
                detail="Access denied: you must be approved by the board owner.",
            )

        if permission == "admin" and member.role != BoardRole.OWNER:
            raise HTTPException(status_code=403, detail="Only the board owner can perform this action.")
        if permission == "write" and member.role not in (BoardRole.OWNER, BoardRole.EDITOR):
            raise HTTPException(status_code=403, detail="Read-only viewer access cannot modify this board.")

        return principal, board_id, registry.get_dag(board_id)

    async def _authenticate_mcp(raw_token: Optional[str], requested_board_id: Optional[str]) -> Tuple[AuthPrincipal, str]:
        if not resolved_auth_enabled:
            target = requested_board_id or "default"
            return (
                AuthPrincipal(
                    kind="anonymous",
                    user_id="local-admin",
                    display_name="Local Admin",
                    board_id=target,
                    scopes=("tasks:read", "tasks:claim", "tasks:submit", "nodes:create"),
                ),
                target,
            )
        if not raw_token:
            raise PermissionError("Missing Authorization Bearer token or X-Api-Key.")
        principal = await _resolve_principal(raw_token)
        if not principal:
            raise PermissionError("Invalid or revoked MCP credentials.")

        if principal.kind == "agent":
            bound_board = principal.board_id or ""
            if not bound_board:
                raise PermissionError("Forbidden: agent key has no board binding.")
            if requested_board_id and requested_board_id != bound_board:
                raise PermissionError(f"Forbidden: agent key cannot access board '{requested_board_id}'.")
            return principal, bound_board

        # Human JWT over MCP
        if requested_board_id:
            board_id = requested_board_id
        else:
            boards = st.list_boards_for_user(principal.user_id)
            if not boards:
                raise PermissionError("Forbidden: user has no approved boards.")
            board_id = boards[0].id
        member = st.get_board_member(board_id, principal.user_id)
        if not member or member.status != MemberStatus.APPROVED or member.role not in (BoardRole.OWNER, BoardRole.EDITOR):
            raise PermissionError("Forbidden: editor or owner membership required for MCP access.")
        return principal, board_id

    authenticated_mcp_app = AuthenticatedMCPApp(mcp_streamable_app, _authenticate_mcp)

    @app.api_route("/mcp", methods=["GET", "POST", "DELETE"])
    async def mcp_root_dispatch(request: Request):
        forwarded_scope = dict(request.scope)
        forwarded_scope["path"] = "/"
        forwarded_scope["raw_path"] = b"/"
        sent_messages: List[Dict[str, Any]] = []

        async def _capture_send(message: Dict[str, Any]) -> None:
            sent_messages.append(message)

        await authenticated_mcp_app(forwarded_scope, request.receive, _capture_send)
        start_msg = next((m for m in sent_messages if m["type"] == "http.response.start"), None)
        body_bytes = b"".join(m.get("body", b"") for m in sent_messages if m["type"] == "http.response.body")
        headers = {
            k.decode("latin-1"): v.decode("latin-1")
            for k, v in (start_msg.get("headers", []) if start_msg else [])
            if k.decode("latin-1").lower() != "content-length"
        }
        return Response(
            content=body_bytes,
            status_code=start_msg["status"] if start_msg else 500,
            headers=headers,
        )

    app.mount("/mcp", authenticated_mcp_app)

    def _sync_workspace_user(
        board_id: str,
        user_id: Optional[str] = None,
        display_name: Optional[str] = None,
        tool_name: str = "generic_tool",
        client_host: Optional[str] = None,
        status_val: str = "joined",
        last_task_id: Optional[str] = None,
        preserve_working: bool = False,
    ) -> Tuple[Optional[WorkspaceUser], bool]:
        if user_id is not None and user_id.strip().lower() in _IGNORED_USER_ACTORS:
            return None, False
        user, is_new = st.upsert_workspace_user(
            user_id=user_id,
            display_name=display_name,
            tool_name=tool_name,
            client_host=client_host,
            status=status_val,
            last_task_id=last_task_id,
            preserve_working=preserve_working,
            board_id=board_id,
        )
        dag = registry.get_dag(board_id)
        for enriched in dag.list_workspace_users():
            if enriched.user_id == user.user_id:
                return enriched, is_new
        return user, is_new

    def _extract_request_user(
        request: Request,
        principal: AuthPrincipal,
        explicit_agent_id: Optional[str] = None,
    ) -> Tuple[Optional[str], str, Optional[str]]:
        raw_uid = (
            explicit_agent_id
            or request.headers.get("X-Agent-Id")
            or request.query_params.get("agent_id")
            or (principal.agent_key_name if principal.kind == "agent" else None)
        )
        uid = raw_uid.strip() if isinstance(raw_uid, str) and raw_uid.strip() else None
        tool = (request.headers.get("X-Tool-Name") or "generic_tool").strip() or "generic_tool"
        host = (request.headers.get("X-Client-Host") or "").strip() or None
        return uid, tool, host

    async def _broadcast_user_event(user: WorkspaceUser, is_new: bool, board_id: str) -> None:
        event_const = EVENT_USER_JOINED if is_new else EVENT_USER_UPDATED
        short_name = "USER_JOINED" if is_new else "USER_UPDATED"
        await ws_manager.broadcast(
            {
                "event": short_name,
                "event_type": event_const,
                "type": event_const,
                "board_id": board_id,
                "timestamp": time.time(),
                "node_id": user.last_task_id or "--",
                "actor": user.user_id,
                "user": user.model_dump(),
                "data": user.model_dump(),
            },
            board_id=board_id,
        )

    # ---------------------------------------------------------
    # Public Health, UI Shell & Auth Endpoints
    # ---------------------------------------------------------

    @app.get("/health")
    async def health_check():
        return {
            "status": "healthy",
            "auth_enabled": resolved_auth_enabled,
            "timestamp": time.time(),
            "nodes_count": len(default_dag._nodes),
            "active_ws_clients": len(ws_manager.active_connections),
        }

    @app.get("/assets/synapse-logo.svg")
    async def get_synapse_logo():
        logo_path = Path(__file__).parent / "assets" / "synapse-logo.svg"
        if logo_path.exists():
            return FileResponse(logo_path, media_type="image/svg+xml")
        raise HTTPException(status_code=404, detail="Logo asset not found")

    @app.get("/", response_class=HTMLResponse)
    @app.get("/dashboard", response_class=HTMLResponse)
    async def get_dashboard():
        locations = [
            Path(__file__).parent / "dashboard.html",
            Path("harness/dashboard.html"),
            Path("dashboard/index.html"),
        ]
        for p in locations:
            if p.exists():
                return HTMLResponse(content=p.read_text(encoding="utf-8"))
        return HTMLResponse(
            content="<!DOCTYPE html><html><body><h1>SYNAPSE Dashboard</h1></body></html>"
        )

    @app.get("/api/auth/config")
    async def get_auth_config():
        return {
            "auth_enabled": resolved_auth_enabled,
            "supabase_url": auth.supabase_url,
            "supabase_publishable_key": auth.publishable_key,
        }

    @app.post("/api/auth/signup")
    async def auth_signup(body: _AuthSignupBody, request: Request, response: Response):
        redirect_to = f"{str(request.base_url).rstrip('/')}/dashboard"
        status_code, data = await auth.sign_up(body.email, body.password, body.display_name, redirect_to=redirect_to)
        if status_code >= 400:
            raise HTTPException(
                status_code=status_code,
                detail=data.get("msg") or data.get("error_description") or data.get("error") or "Signup failed",
            )
        access_token = data.get("access_token")
        if access_token:
            _set_session_cookie(response, access_token, request)
            principal = await auth.verify_human_jwt(access_token)
            if principal:
                _ensure_human_default_board(principal)
        return data

    @app.post("/api/auth/login")
    async def auth_login(body: _AuthLoginBody, request: Request, response: Response):
        status_code, data = await auth.sign_in_password(body.email, body.password)
        if status_code >= 400:
            raise HTTPException(
                status_code=status_code,
                detail=data.get("error_description") or data.get("msg") or data.get("error") or "Invalid credentials",
            )
        access_token = data.get("access_token")
        if access_token:
            _set_session_cookie(response, access_token, request)
            principal = await auth.verify_human_jwt(access_token)
            if principal:
                _ensure_human_default_board(principal)
        return data

    @app.post("/api/auth/otp/send")
    async def auth_otp_send(body: _AuthOtpSendBody, request: Request):
        redirect_to = f"{str(request.base_url).rstrip('/')}/dashboard"
        status_code, data = await auth.send_email_otp(body.email, redirect_to=redirect_to)
        if status_code >= 400:
            raise HTTPException(
                status_code=status_code,
                detail=data.get("msg") or data.get("error_description") or "Failed to send OTP",
            )
        return {"sent": True, "message": "Check your email for the login link or 6-digit code."}

    @app.post("/api/auth/session")
    async def auth_establish_session(body: _AuthSessionBody, request: Request, response: Response):
        principal = await auth.verify_human_jwt(body.access_token)
        if not principal:
            raise HTTPException(status_code=401, detail="Invalid access token")
        _set_session_cookie(response, body.access_token, request)
        _ensure_human_default_board(principal)
        return {"ok": True, "user_id": principal.user_id, "email": principal.email}

    @app.post("/api/auth/verify-hash")
    async def auth_verify_hash(body: _AuthVerifyHashBody, request: Request, response: Response):
        status_code, data = await auth.verify_token_hash(body.token_hash, body.type)
        if status_code >= 400:
            raise HTTPException(
                status_code=status_code,
                detail=data.get("error_description") or data.get("msg") or "Invalid or expired verification link",
            )
        access_token = data.get("access_token")
        if access_token:
            _set_session_cookie(response, access_token, request)
            principal = await auth.verify_human_jwt(access_token)
            if principal:
                _ensure_human_default_board(principal)
        return data

    @app.post("/api/auth/otp/verify")
    async def auth_otp_verify(body: _AuthOtpVerifyBody, request: Request, response: Response):
        status_code, data = await auth.verify_email_otp(body.email, body.token)
        if status_code >= 400:
            raise HTTPException(
                status_code=status_code,
                detail=data.get("error_description") or data.get("msg") or "Invalid OTP code",
            )
        access_token = data.get("access_token")
        if access_token:
            _set_session_cookie(response, access_token, request)
            principal = await auth.verify_human_jwt(access_token)
            if principal:
                _ensure_human_default_board(principal)
        return data

    @app.post("/api/auth/refresh")
    async def auth_refresh(body: _AuthRefreshBody, request: Request, response: Response):
        status_code, data = await auth.refresh_session(body.refresh_token)
        if status_code >= 400:
            raise HTTPException(status_code=401, detail="Session refresh failed")
        _set_session_cookie(response, data.get("access_token"), request)
        return data

    @app.post("/api/auth/logout")
    async def auth_logout(response: Response):
        response.delete_cookie(SESSION_COOKIE_NAME, path="/")
        return {"logged_out": True}

    @app.get("/api/auth/me")
    async def auth_me(request: Request):
        principal = await _require_principal(request)
        if principal.kind == "human":
            _ensure_human_default_board(principal)
        return {
            "kind": principal.kind,
            "user_id": principal.user_id,
            "email": principal.email,
            "display_name": principal.display_name,
            "board_id": principal.board_id,
            "scopes": list(principal.scopes),
        }

    # ---------------------------------------------------------
    # Multi-Board, Friend Access Control & Agent Key Endpoints
    # ---------------------------------------------------------

    @app.get("/api/boards", response_model=List[Board])
    async def list_boards(request: Request):
        principal = await _require_principal(request)
        if not resolved_auth_enabled:
            boards = st.list_all_boards()
            if not boards:
                boards = [st.create_board(title="Default Board", owner_id="local-admin", board_id="default")]
            return boards
        if principal.kind == "agent":
            b = st.get_board(principal.board_id or "")
            return [b] if b else []
        boards = st.list_boards_for_user(principal.user_id)
        if not boards:
            boards = [_ensure_human_default_board(principal)]
        return boards

    @app.post("/api/boards", response_model=Board, status_code=status.HTTP_201_CREATED)
    async def create_board(req: CreateBoardRequest, request: Request):
        principal = await _require_principal(request)
        if principal.kind == "agent":
            raise HTTPException(status_code=403, detail="AI agents cannot create new Knowledge Boards.")
        board = st.create_board(
            title=req.title,
            owner_id=principal.user_id,
            owner_email=principal.email,
            owner_display_name=principal.display_name,
            slug=req.slug,
            description=req.description,
            stage=req.stage,
        )
        if req.seed_template:
            await _apply_seed_template(board.id, req.seed_template, reset_first=True)
            board = st.get_board(board.id) or board
            board.role = BoardRole.OWNER
        return board

    @app.patch("/api/boards/{board_id}", response_model=Board)
    async def update_board(board_id: str, req: UpdateBoardRequest, request: Request):
        _, resolved_id, _ = await _authorize_board(request, board_id, permission="admin")
        updated = st.update_board(resolved_id, title=req.title, description=req.description, stage=req.stage)
        if not updated:
            raise HTTPException(status_code=404, detail=f"Board '{resolved_id}' not found.")
        updated.role = BoardRole.OWNER
        return updated

    @app.delete("/api/boards/{board_id}")
    async def delete_board(board_id: str, request: Request):
        _, resolved_id, _ = await _authorize_board(request, board_id, permission="admin")
        deleted = st.delete_board(resolved_id)
        registry.remove_dag(resolved_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Board '{resolved_id}' not found.")
        return {"deleted": True, "board_id": resolved_id}

    @app.get("/api/boards/{board_id}/members", response_model=List[BoardMember])
    async def list_board_members(board_id: str, request: Request):
        principal, resolved_id, _ = await _authorize_board(request, board_id, permission="read")
        members = st.list_board_members(resolved_id)
        board = st.get_board(resolved_id)
        if board and principal.user_id != board.owner_id:
            members = [m for m in members if m.status == MemberStatus.APPROVED or m.user_id == principal.user_id]
        return members

    @app.post("/api/boards/{board_id}/invites", response_model=BoardInvite, status_code=status.HTTP_201_CREATED)
    async def create_board_invite(board_id: str, req: CreateInviteRequest, request: Request):
        principal, resolved_id, _ = await _authorize_board(request, board_id, permission="admin")
        if req.role == BoardRole.OWNER:
            raise HTTPException(status_code=400, detail="Invites can only grant 'editor' or 'viewer' roles.")
        return st.create_invite(
            board_id=resolved_id,
            created_by=principal.user_id,
            role=req.role,
            require_approval=req.require_approval,
        )

    @app.get("/api/boards/{board_id}/invites", response_model=List[BoardInvite])
    async def list_board_invites(board_id: str, request: Request):
        _, resolved_id, _ = await _authorize_board(request, board_id, permission="admin")
        return st.list_invites(resolved_id)

    @app.post("/api/boards/join")
    async def join_board_via_invite(req: JoinBoardRequest, request: Request):
        principal = await _require_principal(request)
        if principal.kind == "agent":
            raise HTTPException(status_code=403, detail="AI agents cannot join boards via user invites.")
        invite = st.get_invite(req.invite_code)
        if not invite or invite.revoked_at is not None:
            raise HTTPException(status_code=404, detail="Invalid or revoked invite code.")
        existing = st.get_board_member(invite.board_id, principal.user_id)
        if existing and existing.status == MemberStatus.APPROVED:
            return {"member": existing, "board": st.get_board(invite.board_id), "pending_approval": False}
        member_status = MemberStatus.PENDING if invite.require_approval else MemberStatus.APPROVED
        member = st.upsert_board_member(
            board_id=invite.board_id,
            user_id=principal.user_id,
            email=principal.email,
            display_name=req.display_name or principal.display_name,
            role=invite.role,
            status=member_status,
            invited_by=invite.created_by,
        )
        return {
            "member": member,
            "board": st.get_board(invite.board_id) if member_status == MemberStatus.APPROVED else None,
            "pending_approval": member_status == MemberStatus.PENDING,
        }

    @app.post("/api/boards/{board_id}/members/{user_id}/approve", response_model=BoardMember)
    async def approve_board_member(board_id: str, user_id: str, request: Request):
        _, resolved_id, _ = await _authorize_board(request, board_id, permission="admin")
        member = st.get_board_member(resolved_id, user_id)
        if not member:
            raise HTTPException(status_code=404, detail="Member request not found.")
        return st.upsert_board_member(
            board_id=resolved_id,
            user_id=user_id,
            email=member.email,
            display_name=member.display_name,
            role=member.role,
            status=MemberStatus.APPROVED,
            invited_by=member.invited_by,
        )

    @app.patch("/api/boards/{board_id}/members/{user_id}", response_model=BoardMember)
    async def update_board_member(board_id: str, user_id: str, req: UpdateMemberRequest, request: Request):
        _, resolved_id, _ = await _authorize_board(request, board_id, permission="admin")
        member = st.get_board_member(resolved_id, user_id)
        if not member:
            raise HTTPException(status_code=404, detail="Member not found.")
        board = st.get_board(resolved_id)
        if board and user_id == board.owner_id:
            raise HTTPException(status_code=400, detail="Cannot modify the board owner's membership.")
        return st.upsert_board_member(
            board_id=resolved_id,
            user_id=user_id,
            email=member.email,
            display_name=member.display_name,
            role=req.role or member.role,
            status=req.status or member.status,
            invited_by=member.invited_by,
        )

    @app.delete("/api/boards/{board_id}/members/{user_id}")
    async def remove_board_member(board_id: str, user_id: str, request: Request):
        _, resolved_id, _ = await _authorize_board(request, board_id, permission="admin")
        board = st.get_board(resolved_id)
        if board and user_id == board.owner_id:
            raise HTTPException(status_code=400, detail="Cannot remove the board owner.")
        removed, keys_revoked = st.remove_board_member(resolved_id, user_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Member not found.")
        return {"removed": True, "user_id": user_id, "agent_keys_revoked": keys_revoked}

    @app.get("/api/boards/{board_id}/agent-keys", response_model=List[BoardAgentKey])
    async def list_board_agent_keys(board_id: str, request: Request):
        principal, resolved_id, _ = await _authorize_board(request, board_id, permission="write")
        keys = st.list_agent_keys(resolved_id)
        board = st.get_board(resolved_id)
        if board and principal.user_id != board.owner_id:
            keys = [k for k in keys if k.created_by == principal.user_id]
        return keys

    @app.post("/api/boards/{board_id}/agent-keys", response_model=CreateAgentKeyResponse, status_code=status.HTTP_201_CREATED)
    async def create_board_agent_key(board_id: str, req: CreateAgentKeyRequest, request: Request):
        principal, resolved_id, _ = await _authorize_board(request, board_id, permission="write")
        if principal.kind == "agent":
            raise HTTPException(status_code=403, detail="AI agents cannot mint new Agent Keys.")
        raw_key, key_prefix, key_hash = generate_agent_key()
        key_meta = st.create_agent_key(
            board_id=resolved_id,
            name=req.name,
            key_prefix=key_prefix,
            key_hash=key_hash,
            created_by=principal.user_id,
            created_by_email=principal.email,
            scopes=req.scopes,
        )
        return CreateAgentKeyResponse(key=key_meta, api_key=raw_key)

    @app.delete("/api/boards/{board_id}/agent-keys/{key_id}")
    async def revoke_board_agent_key(board_id: str, key_id: str, request: Request):
        principal, resolved_id, _ = await _authorize_board(request, board_id, permission="write")
        board = st.get_board(resolved_id)
        keys = st.list_agent_keys(resolved_id)
        target_key = next((k for k in keys if k.id == key_id), None)
        if not target_key:
            raise HTTPException(status_code=404, detail="Agent key not found.")
        if board and principal.user_id != board.owner_id and target_key.created_by != principal.user_id:
            raise HTTPException(status_code=403, detail="Only the board owner or key creator can revoke this key.")
        st.revoke_agent_key(resolved_id, key_id)
        return {"revoked": True, "key_id": key_id, "board_id": resolved_id}

    # ---------------------------------------------------------
    # WebSocket Realtime Synchronization Rooms (Board-Scoped)
    # ---------------------------------------------------------

    @app.websocket("/ws")
    @app.websocket("/ws/graph")
    async def websocket_endpoint(websocket: WebSocket):
        requested_board = websocket.query_params.get("board_id")
        if resolved_auth_enabled:
            raw_token = extract_bearer_token(
                websocket.headers.get("authorization") or websocket.headers.get("x-api-key"),
                websocket.query_params.get("token"),
            ) or websocket.cookies.get(SESSION_COOKIE_NAME)
            principal = await _resolve_principal(raw_token) if raw_token else None
            if not principal:
                await websocket.close(code=1008, reason="Unauthorized")
                return
            if principal.kind == "agent":
                board_id = principal.board_id or ""
                if requested_board and requested_board != board_id:
                    await websocket.close(code=1008, reason="Forbidden board")
                    return
            else:
                if requested_board:
                    board_id = requested_board
                else:
                    boards = st.list_boards_for_user(principal.user_id)
                    if not boards:
                        await websocket.close(code=1008, reason="No accessible boards")
                        return
                    board_id = boards[0].id
                member = st.get_board_member(board_id, principal.user_id)
                if not member or member.status != MemberStatus.APPROVED:
                    await websocket.close(code=1008, reason="Forbidden board")
                    return
        else:
            board_id = requested_board or "default"

        await ws_manager.connect(websocket, board_id=board_id)
        try:
            while True:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            await ws_manager.disconnect(websocket)
        except Exception:
            await ws_manager.disconnect(websocket)

    # ---------------------------------------------------------
    # Agent Protocol Discovery (/llms.txt, /agent)
    # ---------------------------------------------------------

    @app.get("/llms.txt", response_class=PlainTextResponse)
    @app.get("/agent", response_class=PlainTextResponse)
    async def get_agent_protocol(request: Request, board_id: Optional[str] = None):
        _, resolved_board, dag = await _authorize_board(request, board_id, permission="read")
        base_url = str(request.base_url).rstrip("/")
        ready = dag.get_ready_tasks()
        ready_lines = "\n".join(f"  - {t.id}: {t.title}" for t in ready) or "  (none)"
        state = dag.get_graph_state()
        project_links = list_project_links(state.nodes, resolved_projects_root)
        project_lines = "\n".join(
            f"  - /{slug}: {base_url}{path}" for slug, path in project_links.items()
        ) or "  (none)"
        user_lines = "\n".join(
            f"  - {u.display_name} ({u.user_id}, {u.tool_name}): {u.status}"
            for u in state.users
        ) or "  (none)"
        return PlainTextResponse(
            content=(
                "# SYNAPSE Agent Protocol\n"
                f"Server: {base_url}\n"
                f"Board: {resolved_board}\n"
                f"MCP Endpoint: {base_url}/mcp (Authorization: Bearer <BOARD_AGENT_KEY>)\n"
                f"Dashboard: {base_url}/dashboard\n"
                f"Nodes: {len(state.nodes)} | Ready Tasks: {len(ready)} | Users: {len(state.users)}\n\n"
                "## Workspace Users\n"
                f"{user_lines}\n\n"
                "## Currently Ready Tasks\n"
                f"{ready_lines}\n\n"
                "## Project Preview Links\n"
                f"{project_lines}\n\n"
                "## REST Workflow (authenticated via Bearer Board Agent Key)\n"
                f"1. List ready tasks:  GET  {base_url}/api/tasks/ready  (or /api/boards/{resolved_board}/tasks/ready)\n"
                f"2. Claim task lease:  POST {base_url}/api/tasks/{{TASK_ID}}/claim\n"
                f"3. Get scoped spec:   GET  {base_url}/api/tasks/{{TASK_ID}}/subgraph\n"
                f"4. Submit result:     POST {base_url}/api/tasks/{{TASK_ID}}/submit\n"
            )
        )

    # ---------------------------------------------------------
    # Shared Board-Scoped Handlers (used by both /api/* and /api/boards/{id}/*)
    # ---------------------------------------------------------

    async def _handle_create_node(board_id: str, dag: DAGManager, req: CreateNodeRequest, request: Request):
        async with mutation_lock:
            if req.idempotency_key and st:
                existing_event = st.check_idempotency(req.idempotency_key, board_id=board_id)
                if existing_event:
                    existing_node = dag.get_node(req.id) or st.get_node(req.id, board_id=board_id)
                    if existing_node:
                        return {
                            "node": existing_node,
                            "message": "Node created successfully (idempotent replay)",
                            **existing_node.model_dump(),
                            **_project_link_fields(existing_node, request),
                        }

            if dag.has_node(req.id):
                raise HTTPException(status_code=409, detail=f"Node '{req.id}' already exists.")

            node = Node(
                id=req.id,
                title=req.title,
                type=req.type,
                status=req.status or NodeStatus.PENDING,
                payload=req.payload,
                board_id=board_id,
                created_at=time.time(),
                updated_at=time.time(),
            )

            try:
                created_node = dag.add_node(
                    node=node,
                    dependencies=req.dependencies,
                    actor=req.actor,
                    idempotency_key=req.idempotency_key,
                )
            except CycleError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except NodeNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=409, detail=str(e))

            _sync_workspace_user(
                board_id=board_id,
                user_id=req.actor,
                tool_name=request.headers.get("X-Tool-Name") or "generic_tool",
                client_host=request.headers.get("X-Client-Host"),
                status_val="joined",
                last_task_id=created_node.id,
                preserve_working=True,
            )

        await ws_manager.broadcast(
            {
                "event": "NODE_CREATED",
                "event_type": EVENT_NODE_CREATED,
                "type": EVENT_NODE_CREATED,
                "board_id": board_id,
                "timestamp": time.time(),
                "node_id": created_node.id,
                "node": created_node.model_dump(),
                "dependencies": req.dependencies,
                "actor": req.actor,
                "data": {
                    "title": created_node.title,
                    "type": created_node.type.value if hasattr(created_node.type, "value") else str(created_node.type),
                    "dependencies": req.dependencies,
                },
            },
            board_id=board_id,
        )

        return {
            "node": created_node,
            "message": "Node created successfully",
            **created_node.model_dump(),
            **_project_link_fields(created_node, request),
        }

    async def _handle_claim_task(board_id: str, dag: DAGManager, task_id: str, req: ClaimTaskRequest, request: Request):
        effective_task_id = task_id or req.task_id
        if not effective_task_id:
            raise HTTPException(status_code=400, detail="Missing task_id.")

        claimed_user: Optional[WorkspaceUser] = None
        async with mutation_lock:
            if req.idempotency_key and st:
                existing_event = st.check_idempotency(req.idempotency_key, board_id=board_id)
                if existing_event:
                    existing_node = dag.get_node(effective_task_id) or st.get_node(effective_task_id, board_id=board_id)
                    if existing_node:
                        return {
                            "task": existing_node,
                            "message": "Task claimed successfully (idempotent replay)",
                            **existing_node.model_dump(),
                        }

            try:
                node = dag.claim_task(
                    task_id=effective_task_id,
                    agent_id=req.agent_id,
                    tool_name=getattr(req, "tool_name", "generic_tool"),
                    idempotency_key=req.idempotency_key,
                )
            except NodeNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))
            except TaskConflictError as e:
                raise HTTPException(status_code=409, detail=str(e))

            claimed_user, _ = _sync_workspace_user(
                board_id=board_id,
                user_id=req.agent_id,
                tool_name=getattr(req, "tool_name", "generic_tool"),
                client_host=request.headers.get("X-Client-Host"),
                status_val="working",
                last_task_id=effective_task_id,
                preserve_working=False,
            )

        await ws_manager.broadcast(
            {
                "event": "TASK_CLAIMED",
                "event_type": EVENT_TASK_CLAIMED,
                "type": EVENT_TASK_CLAIMED,
                "board_id": board_id,
                "timestamp": time.time(),
                "node_id": effective_task_id,
                "task_id": effective_task_id,
                "agent_id": req.agent_id,
                "tool_name": getattr(req, "tool_name", "generic_tool"),
                "node": node.model_dump(),
                "user": claimed_user.model_dump() if claimed_user else None,
                "actor": req.agent_id,
                "data": {
                    "agent_id": req.agent_id,
                    "tool_name": getattr(req, "tool_name", "generic_tool"),
                    "status": node.status.value if hasattr(node.status, "value") else str(node.status),
                    "assigned_to": node.assigned_to,
                },
            },
            board_id=board_id,
        )

        return {
            "task": node,
            "message": "Task claimed successfully",
            **node.model_dump(),
        }

    async def _handle_submit_task(board_id: str, dag: DAGManager, task_id: str, req: SubmitTaskRequest, request: Request):
        effective_task_id = task_id or req.task_id
        if not effective_task_id:
            raise HTTPException(status_code=400, detail="Missing task_id.")

        async with mutation_lock:
            if req.idempotency_key and st:
                existing_event = st.check_idempotency(req.idempotency_key, board_id=board_id)
                if existing_event:
                    existing_node = dag.get_node(effective_task_id) or st.get_node(effective_task_id, board_id=board_id)
                    if existing_node:
                        return {
                            "task": existing_node,
                            "message": "Task submitted successfully (idempotent replay)",
                            **existing_node.model_dump(),
                            **_project_link_fields(existing_node, request),
                        }

            target_node = dag.get_node(effective_task_id)
            if not target_node:
                raise HTTPException(status_code=404, detail=f"Task '{effective_task_id}' not found.")

            if target_node.status == NodeStatus.STALE_CONFLICT:
                node = dag.resolve_conflict(
                    task_id=effective_task_id,
                    new_output=req.output,
                    actor=req.agent_id,
                    idempotency_key=req.idempotency_key,
                )
            else:
                try:
                    node = dag.submit_task(
                        task_id=effective_task_id,
                        agent_id=req.agent_id,
                        output=req.output,
                        idempotency_key=req.idempotency_key,
                    )
                except NodeNotFoundError as e:
                    raise HTTPException(status_code=404, detail=str(e))
                except TaskConflictError as e:
                    raise HTTPException(status_code=409, detail=str(e))

            has_other_active = any(
                n.status == NodeStatus.IN_PROGRESS and dag._matches_assignee(n.assigned_to, req.agent_id)
                for n in dag.list_nodes()
            )
            submitted_user, _ = _sync_workspace_user(
                board_id=board_id,
                user_id=req.agent_id,
                tool_name=request.headers.get("X-Tool-Name") or "generic_tool",
                client_host=request.headers.get("X-Client-Host"),
                status_val="working" if has_other_active else "joined",
                last_task_id=effective_task_id,
                preserve_working=False,
            )

        await ws_manager.broadcast(
            {
                "event": "TASK_SUBMITTED",
                "event_type": EVENT_TASK_SUBMITTED,
                "type": EVENT_TASK_SUBMITTED,
                "board_id": board_id,
                "timestamp": time.time(),
                "node_id": effective_task_id,
                "task_id": effective_task_id,
                "agent_id": req.agent_id,
                "node": node.model_dump(),
                "user": submitted_user.model_dump() if submitted_user else None,
                "actor": req.agent_id,
                "data": {
                    "output": req.output,
                    "status": node.status.value if hasattr(node.status, "value") else str(node.status),
                    "version": node.version,
                },
            },
            board_id=board_id,
        )

        return {
            "task": node,
            "message": "Task submitted successfully",
            **node.model_dump(),
            **_project_link_fields(node, request),
        }

    async def _handle_update_requirement(
        board_id: str,
        dag: DAGManager,
        req: UpdateRequirementRequest,
        request: Request,
        req_id: Optional[str] = None,
    ):
        effective_req_id = req_id or req.req_id
        if not effective_req_id:
            raise HTTPException(status_code=400, detail="Missing requirement ID.")

        async with mutation_lock:
            if req.idempotency_key and st:
                existing_event = st.check_idempotency(req.idempotency_key, board_id=board_id)
                if existing_event:
                    req_node = dag.get_node(effective_req_id) or st.get_node(effective_req_id, board_id=board_id)
                    if req_node:
                        return {
                            "requirement": req_node,
                            "invalidated_nodes": [],
                            "blast_radius_count": 0,
                            "message": "Requirement updated (idempotent replay)",
                            "idempotent_replay": True,
                        }

            target_node = dag.get_node(effective_req_id)
            if not target_node:
                raise HTTPException(status_code=404, detail=f"Requirement '{effective_req_id}' not found.")

            try:
                req_node, invalidated_ids = dag.update_requirement(
                    req_id=effective_req_id,
                    new_spec=req.new_spec,
                    actor=req.actor,
                    idempotency_key=req.idempotency_key,
                )
            except NodeNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))

            _sync_workspace_user(
                board_id=board_id,
                user_id=req.actor,
                tool_name=request.headers.get("X-Tool-Name") or "generic_tool",
                client_host=request.headers.get("X-Client-Host"),
                status_val="joined",
                last_task_id=effective_req_id,
                preserve_working=True,
            )

        await ws_manager.broadcast(
            {
                "event": "REQUIREMENT_UPDATED",
                "event_type": EVENT_REQUIREMENT_UPDATED,
                "type": EVENT_REQUIREMENT_UPDATED,
                "board_id": board_id,
                "timestamp": time.time(),
                "node_id": effective_req_id,
                "req_id": effective_req_id,
                "new_spec": req.new_spec,
                "version": req_node.version,
                "actor": req.actor,
                "node": req_node.model_dump(),
                "data": {"new_spec": req.new_spec, "version": req_node.version},
            },
            board_id=board_id,
        )

        if invalidated_ids:
            await ws_manager.broadcast(
                {
                    "event": "BLAST_RADIUS_ALERT",
                    "event_type": EVENT_CONFLICT_DETECTED,
                    "type": EVENT_CONFLICT_DETECTED,
                    "board_id": board_id,
                    "timestamp": time.time(),
                    "root_cause_node": effective_req_id,
                    "invalidated_nodes": invalidated_ids,
                    "severity": "CRITICAL",
                    "message": f"Requirement {effective_req_id} updated. {len(invalidated_ids)} downstream nodes invalidated.",
                    "actor": req.actor,
                    "data": {
                        "reason": f"Requirement '{effective_req_id}' updated",
                        "invalidated_by": effective_req_id,
                        "invalidated_nodes": invalidated_ids,
                    },
                },
                board_id=board_id,
            )

            for item in invalidated_ids:
                inv_node = item if isinstance(item, Node) else dag.get_node(item)
                if inv_node:
                    await ws_manager.broadcast(
                        {
                            "event": "CONFLICT_DETECTED",
                            "event_type": EVENT_CONFLICT_DETECTED,
                            "type": EVENT_CONFLICT_DETECTED,
                            "board_id": board_id,
                            "node_id": inv_node.id,
                            "actor": req.actor,
                            "data": {
                                "reason": inv_node.payload.get("conflict_reason")
                                or inv_node.payload.get("stale_reason")
                                or f"Requirement '{effective_req_id}' updated",
                                "invalidated_by": effective_req_id,
                                "status": inv_node.status.value if hasattr(inv_node.status, "value") else str(inv_node.status),
                                "version": inv_node.version,
                            },
                            "node": inv_node.model_dump(),
                            "timestamp": time.time(),
                        },
                        board_id=board_id,
                    )

        return {
            "requirement": req_node,
            "invalidated_nodes": invalidated_ids,
            "blast_radius_count": len(invalidated_ids),
            "message": f"Requirement updated. {len(invalidated_ids)} nodes marked STALE_CONFLICT.",
            "idempotent_replay": False,
        }

    async def _apply_seed_template(board_id: str, template: str, reset_first: bool = True) -> Dict[str, Any]:
        try:
            seed_nodes = get_seed_nodes(template)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

        dag = registry.get_dag(board_id)
        async with mutation_lock:
            if reset_first:
                dag.reset()

            now = time.time()
            created_count = 0
            for item in seed_nodes:
                if dag.has_node(item["id"]):
                    continue
                node = Node(
                    id=item["id"],
                    title=item["title"],
                    type=NodeType(item["type"]),
                    status=NodeStatus(item.get("status", "PENDING")),
                    payload=item.get("payload", {}),
                    board_id=board_id,
                    created_at=now,
                    updated_at=now,
                )
                dag.add_node(
                    node=node,
                    dependencies=item.get("dependencies", []),
                    actor=item.get("actor", "system"),
                    idempotency_key=f"{board_id}-{item['idempotency_key']}" if item.get("idempotency_key") else None,
                )
                created_count += 1

        await ws_manager.broadcast(
            {
                "event": "GRAPH_SEEDED",
                "event_type": "GRAPH_SEEDED",
                "type": "GRAPH_SEEDED",
                "board_id": board_id,
                "timestamp": time.time(),
                "actor": "seed-engine",
                "data": {"template": template, "nodes_created": created_count},
            },
            board_id=board_id,
        )

        state = dag.get_graph_state()
        ready_ids = [n.id for n in dag.get_ready_tasks()]
        return {
            "status": "success",
            "board_id": board_id,
            "template": template,
            "nodes_created": created_count,
            "total_nodes": len(state.nodes),
            "ready_tasks": ready_ids,
        }

    # ---------------------------------------------------------
    # DAG REST Routes (Global/Query + Explicit /api/boards/{board_id}/...)
    # ---------------------------------------------------------

    @app.post("/tasks", status_code=status.HTTP_201_CREATED)
    @app.post("/api/nodes", status_code=status.HTTP_201_CREATED)
    @app.post("/api/tasks", status_code=status.HTTP_201_CREATED)
    async def create_task(req: CreateNodeRequest, request: Request, board_id: Optional[str] = None):
        _, resolved_id, dag = await _authorize_board(request, board_id, permission="write", required_agent_scope="nodes:create")
        return await _handle_create_node(resolved_id, dag, req, request)

    @app.post("/api/boards/{board_id}/nodes", status_code=status.HTTP_201_CREATED)
    @app.post("/api/boards/{board_id}/tasks", status_code=status.HTTP_201_CREATED)
    async def create_board_task(board_id: str, req: CreateNodeRequest, request: Request):
        _, resolved_id, dag = await _authorize_board(request, board_id, permission="write", required_agent_scope="nodes:create")
        return await _handle_create_node(resolved_id, dag, req, request)

    @app.post("/tasks/{task_id}/claim", status_code=status.HTTP_200_OK)
    @app.post("/api/tasks/{task_id}/claim", status_code=status.HTTP_200_OK)
    async def claim_task(task_id: str, req: ClaimTaskRequest, request: Request, board_id: Optional[str] = None):
        _, resolved_id, dag = await _authorize_board(request, board_id, permission="write", required_agent_scope="tasks:claim")
        return await _handle_claim_task(resolved_id, dag, task_id, req, request)

    @app.post("/api/boards/{board_id}/tasks/{task_id}/claim", status_code=status.HTTP_200_OK)
    async def claim_board_task(board_id: str, task_id: str, req: ClaimTaskRequest, request: Request):
        _, resolved_id, dag = await _authorize_board(request, board_id, permission="write", required_agent_scope="tasks:claim")
        return await _handle_claim_task(resolved_id, dag, task_id, req, request)

    @app.get("/tasks/{task_id}/subgraph", response_model=SubgraphResponse, status_code=status.HTTP_200_OK)
    @app.get("/api/tasks/{task_id}/subgraph", response_model=SubgraphResponse, status_code=status.HTTP_200_OK)
    @app.get("/api/boards/{board_id}/tasks/{task_id}/subgraph", response_model=SubgraphResponse, status_code=status.HTTP_200_OK)
    async def get_task_subgraph(
        task_id: str,
        request: Request,
        board_id: Optional[str] = None,
        max_depth: Optional[int] = None,
        max_tokens: int = 1500,
        agent_id: Optional[str] = None,
    ):
        principal, resolved_id, dag = await _authorize_board(request, board_id, permission="read", required_agent_scope="tasks:read")
        try:
            subgraph = dag.get_scoped_subgraph(node_id=task_id, max_depth=max_depth, max_tokens=max_tokens)
        except NodeNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

        uid, tool, host = _extract_request_user(request, principal, agent_id)
        if uid:
            async with mutation_lock:
                user, is_new = _sync_workspace_user(
                    board_id=resolved_id,
                    user_id=uid,
                    tool_name=tool,
                    client_host=host,
                    status_val="viewing",
                    last_task_id=task_id,
                    preserve_working=True,
                )
            if user is not None:
                await _broadcast_user_event(user, is_new, board_id=resolved_id)
        return subgraph

    @app.post("/tasks/{task_id}/submit", status_code=status.HTTP_200_OK)
    @app.post("/api/tasks/{task_id}/submit", status_code=status.HTTP_200_OK)
    async def submit_task(task_id: str, req: SubmitTaskRequest, request: Request, board_id: Optional[str] = None):
        _, resolved_id, dag = await _authorize_board(request, board_id, permission="write", required_agent_scope="tasks:submit")
        return await _handle_submit_task(resolved_id, dag, task_id, req, request)

    @app.post("/api/boards/{board_id}/tasks/{task_id}/submit", status_code=status.HTTP_200_OK)
    async def submit_board_task(board_id: str, task_id: str, req: SubmitTaskRequest, request: Request):
        _, resolved_id, dag = await _authorize_board(request, board_id, permission="write", required_agent_scope="tasks:submit")
        return await _handle_submit_task(resolved_id, dag, task_id, req, request)

    @app.post("/requirements/update", status_code=status.HTTP_200_OK)
    @app.post("/requirements/{req_id}/update", status_code=status.HTTP_200_OK)
    @app.post("/api/requirements/{req_id}/update", status_code=status.HTTP_200_OK)
    async def update_requirement(
        req: UpdateRequirementRequest,
        request: Request,
        req_id: Optional[str] = None,
        board_id: Optional[str] = None,
    ):
        _, resolved_id, dag = await _authorize_board(request, board_id, permission="write", required_agent_scope="nodes:create")
        return await _handle_update_requirement(resolved_id, dag, req, request, req_id=req_id)

    @app.post("/api/boards/{board_id}/requirements/{req_id}/update", status_code=status.HTTP_200_OK)
    async def update_board_requirement(board_id: str, req_id: str, req: UpdateRequirementRequest, request: Request):
        _, resolved_id, dag = await _authorize_board(request, board_id, permission="write", required_agent_scope="nodes:create")
        return await _handle_update_requirement(resolved_id, dag, req, request, req_id=req_id)

    @app.get("/graph", response_model=GraphStateResponse, status_code=status.HTTP_200_OK)
    @app.get("/api/graph", response_model=GraphStateResponse, status_code=status.HTTP_200_OK)
    @app.get("/api/boards/{board_id}/graph", response_model=GraphStateResponse, status_code=status.HTTP_200_OK)
    async def get_graph(request: Request, board_id: Optional[str] = None):
        _, _, dag = await _authorize_board(request, board_id, permission="read", required_agent_scope="tasks:read")
        return dag.get_graph_state()

    @app.get("/export", response_model=GraphSnapshot, status_code=status.HTTP_200_OK)
    @app.get("/api/export", response_model=GraphSnapshot, status_code=status.HTTP_200_OK)
    @app.get("/api/boards/{board_id}/export", response_model=GraphSnapshot, status_code=status.HTTP_200_OK)
    async def export_snapshot(request: Request, board_id: Optional[str] = None):
        _, _, dag = await _authorize_board(request, board_id, permission="read", required_agent_scope="tasks:read")
        return dag.export_snapshot()

    @app.post("/import", status_code=status.HTTP_200_OK)
    @app.post("/api/import", status_code=status.HTTP_200_OK)
    @app.post("/api/boards/{board_id}/import", status_code=status.HTTP_200_OK)
    async def import_snapshot(
        snapshot: GraphSnapshot,
        request: Request,
        replace: bool = True,
        board_id: Optional[str] = None,
    ):
        _, resolved_id, dag = await _authorize_board(request, board_id, permission="admin")
        async with mutation_lock:
            try:
                counts = dag.import_snapshot(snapshot, replace=replace)
            except CycleError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except NodeNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))

        await ws_manager.broadcast(
            {
                "event": "GRAPH_IMPORTED",
                "event_type": "GRAPH_IMPORTED",
                "type": "GRAPH_IMPORTED",
                "board_id": resolved_id,
                "timestamp": time.time(),
                "data": {"replace": replace, **counts},
            },
            board_id=resolved_id,
        )
        return {"status": "success", "board_id": resolved_id, **counts}

    @app.get("/nodes", response_model=List[Node])
    @app.get("/api/nodes", response_model=List[Node])
    @app.get("/api/boards/{board_id}/nodes", response_model=List[Node])
    async def list_nodes(request: Request, board_id: Optional[str] = None):
        _, _, dag = await _authorize_board(request, board_id, permission="read", required_agent_scope="tasks:read")
        return dag.list_nodes()

    @app.get("/nodes/{node_id}", response_model=Node)
    @app.get("/api/nodes/{node_id}", response_model=Node)
    @app.get("/api/boards/{board_id}/nodes/{node_id}", response_model=Node)
    async def get_node(node_id: str, request: Request, board_id: Optional[str] = None):
        _, _, dag = await _authorize_board(request, board_id, permission="read", required_agent_scope="tasks:read")
        node = dag.get_node(node_id)
        if not node:
            raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found.")
        return node

    @app.get("/nodes/{node_id}/events", response_model=List[Event])
    @app.get("/api/nodes/{node_id}/events", response_model=List[Event])
    @app.get("/api/boards/{board_id}/nodes/{node_id}/events", response_model=List[Event])
    async def get_node_events(node_id: str, request: Request, board_id: Optional[str] = None):
        _, resolved_id, _ = await _authorize_board(request, board_id, permission="read", required_agent_scope="tasks:read")
        return st.get_events_for_node(node_id, board_id=resolved_id)

    @app.get("/tasks/ready", response_model=List[Node])
    @app.get("/api/tasks/ready", response_model=List[Node])
    @app.get("/api/boards/{board_id}/tasks/ready", response_model=List[Node])
    async def get_ready_tasks(request: Request, board_id: Optional[str] = None, agent_id: Optional[str] = None):
        principal, resolved_id, dag = await _authorize_board(request, board_id, permission="read", required_agent_scope="tasks:read")
        uid, tool, host = _extract_request_user(request, principal, agent_id)
        if uid:
            async with mutation_lock:
                user, is_new = _sync_workspace_user(
                    board_id=resolved_id,
                    user_id=uid,
                    tool_name=tool,
                    client_host=host,
                    status_val="viewing",
                    preserve_working=True,
                )
            if user is not None:
                await _broadcast_user_event(user, is_new, board_id=resolved_id)
        return dag.get_ready_tasks()

    @app.post("/users/join", status_code=status.HTTP_200_OK)
    @app.post("/api/users/join", status_code=status.HTTP_200_OK)
    @app.post("/api/boards/{board_id}/users/join", status_code=status.HTTP_200_OK)
    async def join_workspace(
        request: Request,
        board_id: Optional[str] = None,
        req: JoinWorkspaceRequest = Body(default_factory=JoinWorkspaceRequest),
    ):
        principal, resolved_id, dag = await _authorize_board(request, board_id, permission="read")
        raw_uid = req.user_id or req.agent_id or request.headers.get("X-Agent-Id") or (
            principal.user_id if principal.kind == "human" else None
        )
        resolved_display = req.display_name or (principal.display_name if principal.kind == "human" else None)
        tool = (
            req.tool_name
            if req.tool_name != "generic_tool"
            else (request.headers.get("X-Tool-Name") or "generic_tool")
        )
        host = req.client_host or request.headers.get("X-Client-Host")
        async with mutation_lock:
            user, is_new = _sync_workspace_user(
                board_id=resolved_id,
                user_id=raw_uid,
                display_name=resolved_display,
                tool_name=tool,
                client_host=host,
                status_val="joined",
                preserve_working=True,
            )
        if user is not None:
            await _broadcast_user_event(user, is_new, board_id=resolved_id)
        return {
            "user": user,
            "is_new": is_new,
            "users": dag.list_workspace_users(),
        }

    @app.get("/users", response_model=List[WorkspaceUser], status_code=status.HTTP_200_OK)
    @app.get("/api/users", response_model=List[WorkspaceUser], status_code=status.HTTP_200_OK)
    @app.get("/api/boards/{board_id}/users", response_model=List[WorkspaceUser], status_code=status.HTTP_200_OK)
    async def list_workspace_users(request: Request, board_id: Optional[str] = None):
        _, _, dag = await _authorize_board(request, board_id, permission="read", required_agent_scope="tasks:read")
        return dag.list_workspace_users()

    @app.get("/api/seed/templates")
    async def get_seed_templates():
        return {"templates": list_seed_templates()}

    @app.post("/api/seed")
    async def seed_graph_body(
        request: Request,
        req: SeedGraphRequest = SeedGraphRequest(),
        board_id: Optional[str] = None,
    ):
        perm = "admin" if req.reset else "write"
        _, resolved_id, _ = await _authorize_board(request, board_id, permission=perm, required_agent_scope="nodes:create")
        return await _apply_seed_template(resolved_id, req.template, reset_first=req.reset)

    @app.post("/api/seed/{template}")
    @app.post("/api/boards/{board_id}/seed/{template}")
    async def seed_graph_template(
        template: str,
        request: Request,
        board_id: Optional[str] = None,
        reset: bool = True,
    ):
        perm = "admin" if reset else "write"
        _, resolved_id, _ = await _authorize_board(request, board_id, permission=perm, required_agent_scope="nodes:create")
        return await _apply_seed_template(resolved_id, template, reset_first=reset)

    @app.post("/reset")
    @app.post("/api/reset")
    @app.post("/api/boards/{board_id}/reset")
    async def reset_graph(request: Request, board_id: Optional[str] = None):
        _, resolved_id, dag = await _authorize_board(request, board_id, permission="admin")
        async with mutation_lock:
            dag.reset()
        await ws_manager.broadcast(
            {
                "event": "GRAPH_RESET",
                "event_type": "GRAPH_RESET",
                "type": "GRAPH_RESET",
                "board_id": resolved_id,
                "timestamp": time.time(),
            },
            board_id=resolved_id,
        )
        return {"status": "success", "board_id": resolved_id, "message": "Graph and event store reset."}

    # 8. Dynamic 4-letter project preview routes (protected when auth_enabled=True)
    @app.get("/{project_slug}", response_class=HTMLResponse)
    async def serve_project_preview(project_slug: str, request: Request, board_id: Optional[str] = None):
        _, _, dag = await _authorize_board(request, board_id, permission="read", required_agent_scope="tasks:read")
        resolution = resolve_project(
            project_slug.lower(),
            dag.list_nodes(),
            resolved_projects_root,
        )
        if resolution is None:
            raise HTTPException(status_code=404, detail=f"Project '{project_slug}' not found.")
        return HTMLResponse(content=resolution.html)

    @app.get("/{project_slug}/{asset_path:path}")
    async def serve_project_asset(project_slug: str, asset_path: str, request: Request, board_id: Optional[str] = None):
        await _authorize_board(request, board_id, permission="read", required_agent_scope="tasks:read")
        slug = project_slug.lower()
        if not is_valid_project_slug(slug):
            raise HTTPException(status_code=404, detail=f"Project '{project_slug}' not found.")
        file_path = find_disk_project_file(resolved_projects_root, slug, asset_path)
        if file_path is None:
            raise HTTPException(status_code=404, detail=f"Asset '{asset_path}' not found.")
        return FileResponse(file_path)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "harness.server:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )
