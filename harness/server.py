"""
OmniHarness FastAPI REST Backend and Realtime WebSocket Hub.
Provides real-time multi-agent synchronization (<50ms latency),
strict idempotency replay, scoped subgraphs, cycle prevention,
and downstream blast-radius invalidation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from harness.graph import (
    CycleError,
    DAGManager,
    GraphError,
    NodeNotFoundError,
    TaskConflictError,
)
from harness.models import (
    ClaimTaskRequest,
    CreateNodeRequest,
    Edge,
    Event,
    GraphSnapshot,
    GraphStateResponse,
    Node,
    NodeStatus,
    NodeType,
    SeedGraphRequest,
    SubgraphResponse,
    SubmitTaskRequest,
    UpdateRequirementRequest,
)
from harness.seeds import get_seed_nodes, list_seed_templates
from harness.store import EventStore
from todo.router import router as todo_router
from todo.store import TodoStore

logger = logging.getLogger("omniharness.server")

# Standard Event Type Constants
EVENT_NODE_CREATED = "EVENT_NODE_CREATED"
EVENT_TASK_CLAIMED = "EVENT_TASK_CLAIMED"
EVENT_TASK_SUBMITTED = "EVENT_TASK_SUBMITTED"
EVENT_CONFLICT_DETECTED = "EVENT_CONFLICT_DETECTED"
EVENT_REQUIREMENT_UPDATED = "EVENT_REQUIREMENT_UPDATED"
EVENT_BLAST_RADIUS_ALERT = "EVENT_BLAST_RADIUS_ALERT"


class ConnectionManager:
    """
    Thread-safe WebSocket connection manager with async lock.
    Manages active connections, sends handshake on connect,
    and prunes dead connections cleanly upon disconnect or broadcast errors.
    """

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
        logger.info("WebSocket connected. Active clients: %d", len(self.active_connections))
        # Initial handshake frame
        await websocket.send_json({
            "event": "CONNECTED",
            "event_type": "CONNECTED",
            "timestamp": time.time(),
        })

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self.active_connections.discard(websocket)
        logger.info("WebSocket disconnected. Active clients: %d", len(self.active_connections))

    async def broadcast(self, message: Dict[str, Any]):
        """Broadcast message to all active WebSocket connections in parallel, pruning dead sockets."""
        async with self._lock:
            connections = list(self.active_connections)
        if not connections:
            return

        tasks = [conn.send_json(message) for conn in connections]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        dead = [conn for conn, res in zip(connections, results) if isinstance(res, Exception)]
        if dead:
            async with self._lock:
                for d in dead:
                    self.active_connections.discard(d)
            logger.info("Pruned %d dead WebSocket connections", len(dead))


def create_app(
    db_path: Optional[str] = None,
    dag_manager: Optional[DAGManager] = None,
    store: Optional[EventStore] = None,
    snapshot_file: Optional[str] = None,
    todo_store: Optional[TodoStore] = None,
) -> FastAPI:
    """
    Factory function to initialize and configure the OmniHarness FastAPI server.
    Hydrates in-memory DAG from SQLite store on startup lifespan, optionally
    seeding from a GraphSnapshot file when the store is empty.
    """
    resolved_db_path = db_path if db_path is not None else os.environ.get("OMNIHARNESS_DB_PATH", "omniharness.db")
    st = store or (dag_manager.store if dag_manager and dag_manager.store else EventStore(db_path=resolved_db_path))
    dag = dag_manager or DAGManager(store=st)
    ws_manager = ConnectionManager()
    mutation_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: hydrate DAG from SQLite store
        if dag.store is not None:
            try:
                dag.hydrate_from_store()
            except Exception as e:
                logger.warning("DAG hydration error on startup: %s", e)

        resolved_snapshot = (
            snapshot_file
            if snapshot_file is not None
            else os.environ.get("OMNIHARNESS_SNAPSHOT_FILE")
        )
        if resolved_snapshot:
            snap_path = Path(resolved_snapshot)
            if snap_path.is_file() and len(dag._nodes) == 0:
                try:
                    snapshot = GraphSnapshot.model_validate_json(
                        snap_path.read_text(encoding="utf-8")
                    )
                    counts = dag.import_snapshot(snapshot, replace=True)
                    logger.info("Hydrated initial snapshot from %s: %s", snap_path, counts)
                except Exception as e:
                    logger.warning("Snapshot startup import error from %s: %s", snap_path, e)
        yield
        # Shutdown cleanup if needed

    app = FastAPI(
        title="SYNAPSE API",
        description="Multi-Agent DAG Knowledge Hub with real-time WebSocket sync and strict idempotency.",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.store = st
    app.state.dag = dag
    app.state.dag_manager = dag
    app.state.ws_manager = ws_manager
    app.state.manager = ws_manager
    app.state.mutation_lock = mutation_lock

    # Mount To-Do app store & router
    if todo_store is not None:
        app.state.todo_store = todo_store
    elif db_path is not None and db_path != "omniharness.db":
        app.state.todo_store = TodoStore(db_path=os.path.join(os.path.dirname(db_path) or ".", "todos.db"))
    else:
        app.state.todo_store = TodoStore(db_path=os.environ.get("TODO_DB_PATH", "todos.db"))

    app.include_router(todo_router)

    # ---------------------------------------------------------
    # WebSocket Realtime Synchronization Endpoints
    # ---------------------------------------------------------

    @app.websocket("/ws")
    @app.websocket("/ws/graph")
    async def websocket_endpoint(websocket: WebSocket):
        await ws_manager.connect(websocket)
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
    # REST API Endpoints
    # ---------------------------------------------------------

    @app.get("/health")
    async def health_check():
        return {
            "status": "healthy",
            "timestamp": time.time(),
            "nodes_count": len(dag._nodes),
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
            content="""<!DOCTYPE html>
<html>
<head><title>SYNAPSE Live Dashboard</title></head>
<body style="font-family: sans-serif; background: #0f172a; color: #f8fafc; padding: 2rem;">
    <h1>SYNAPSE Dashboard</h1>
    <p>Multi-Agent DAG Knowledge Base & Live Event Stream Hub.</p>
</body>
</html>"""
        )

    @app.get("/llms.txt", response_class=PlainTextResponse)
    @app.get("/agent", response_class=PlainTextResponse)
    async def get_agent_protocol(request: Request):
        base_url = str(request.base_url).rstrip("/")
        ready = dag.get_ready_tasks()
        ready_lines = "\n".join(f"  - {t.id}: {t.title}" for t in ready) or "  (none)"
        state = dag.get_graph_state()
        return PlainTextResponse(
            content=(
                "# OmniHarness Agent Protocol\n"
                f"Server: {base_url}\n"
                f"Dashboard: {base_url}/dashboard\n"
                f"Nodes: {len(state.nodes)} | Ready Tasks: {len(ready)}\n\n"
                "## Currently Ready Tasks\n"
                f"{ready_lines}\n\n"
                "## REST Workflow (any coding agent)\n"
                f"1. List ready tasks:  GET  {base_url}/api/tasks/ready\n"
                f"2. Claim task lease:  POST {base_url}/api/tasks/{{TASK_ID}}/claim\n"
                "   Body: {\"task_id\":\"{TASK_ID}\",\"agent_id\":\"<agent>\",\"tool_name\":\"<tool>\",\"idempotency_key\":\"claim-{TASK_ID}-<rand>\"}\n"
                f"3. Get scoped spec:   GET  {base_url}/api/tasks/{{TASK_ID}}/subgraph\n"
                f"4. Submit result:     POST {base_url}/api/tasks/{{TASK_ID}}/submit\n"
                "   Body: {\"task_id\":\"{TASK_ID}\",\"agent_id\":\"<agent>\",\"idempotency_key\":\"submit-{TASK_ID}-<rand>\",\"output\":{\"summary\":\"...\"}}\n\n"
                "## Short CLI (inside MultiagentHarness repo)\n"
                f"  python -m adapters.codex_adapter connect {base_url} --agent-id <agent>\n"
                "  python -m adapters.codex_adapter start <TASK_ID>\n"
                "  python -m adapters.codex_adapter done  <TASK_ID> \"<summary>\"\n"
                "  (Or use adapters.claude_adapter / adapters.agy_adapter identically.)\n"
            )
        )

    @app.get("/todo", response_class=HTMLResponse)
    async def get_todo_app():
        locations = [
            Path(__file__).parent.parent / "todo" / "index.html",
            Path("todo/index.html"),
        ]
        for p in locations:
            if p.exists():
                return HTMLResponse(content=p.read_text(encoding="utf-8"))
        return HTMLResponse(content="<h1>To-Do App Not Found</h1>", status_code=404)

    # 1. POST /tasks (and /api/nodes, /api/tasks)
    @app.post("/tasks", status_code=status.HTTP_201_CREATED)
    @app.post("/api/nodes", status_code=status.HTTP_201_CREATED)
    @app.post("/api/tasks", status_code=status.HTTP_201_CREATED)
    async def create_task(req: CreateNodeRequest):
        async with mutation_lock:
            # Idempotency check: replay of same key returns existing node
            if req.idempotency_key and st:
                existing_event = st.check_idempotency(req.idempotency_key)
                if existing_event:
                    existing_node = dag.get_node(req.id) or st.get_node(req.id)
                    if existing_node:
                        return {
                            "node": existing_node,
                            "message": "Node created successfully (idempotent replay)",
                            **existing_node.model_dump(),
                        }

            # Conflict check: node ID already exists without matching idempotency key
            if dag.has_node(req.id):
                raise HTTPException(status_code=409, detail=f"Node '{req.id}' already exists.")

            node = Node(
                id=req.id,
                title=req.title,
                type=req.type,
                status=req.status or NodeStatus.PENDING,
                payload=req.payload,
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

        # Broadcast NODE_CREATED
        await ws_manager.broadcast({
            "event": "NODE_CREATED",
            "event_type": EVENT_NODE_CREATED,
            "type": EVENT_NODE_CREATED,
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
        })

        return {
            "node": created_node,
            "message": "Node created successfully",
            **created_node.model_dump(),
        }

    # 2. POST /tasks/{task_id}/claim (and /api/tasks/{task_id}/claim)
    @app.post("/tasks/{task_id}/claim", status_code=status.HTTP_200_OK)
    @app.post("/api/tasks/{task_id}/claim", status_code=status.HTTP_200_OK)
    async def claim_task(task_id: str, req: ClaimTaskRequest):
        effective_task_id = task_id or req.task_id
        if not effective_task_id:
            raise HTTPException(status_code=400, detail="Missing task_id.")

        async with mutation_lock:
            # Idempotency check: replay of same claim returns current task state
            if req.idempotency_key and st:
                existing_event = st.check_idempotency(req.idempotency_key)
                if existing_event:
                    existing_node = dag.get_node(effective_task_id) or st.get_node(effective_task_id)
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

        # Broadcast TASK_CLAIMED
        await ws_manager.broadcast({
            "event": "TASK_CLAIMED",
            "event_type": EVENT_TASK_CLAIMED,
            "type": EVENT_TASK_CLAIMED,
            "timestamp": time.time(),
            "node_id": effective_task_id,
            "task_id": effective_task_id,
            "agent_id": req.agent_id,
            "tool_name": getattr(req, "tool_name", "generic_tool"),
            "node": node.model_dump(),
            "actor": req.agent_id,
            "data": {
                "agent_id": req.agent_id,
                "tool_name": getattr(req, "tool_name", "generic_tool"),
                "status": node.status.value if hasattr(node.status, "value") else str(node.status),
                "assigned_to": node.assigned_to,
            },
        })

        return {
            "task": node,
            "message": "Task claimed successfully",
            **node.model_dump(),
        }

    # 3. GET /tasks/{task_id}/subgraph (and /api/tasks/{task_id}/subgraph)
    @app.get("/tasks/{task_id}/subgraph", response_model=SubgraphResponse, status_code=status.HTTP_200_OK)
    @app.get("/api/tasks/{task_id}/subgraph", response_model=SubgraphResponse, status_code=status.HTTP_200_OK)
    async def get_task_subgraph(
        task_id: str,
        max_depth: Optional[int] = None,
        max_tokens: int = 1500,
    ):
        try:
            subgraph = dag.get_scoped_subgraph(
                node_id=task_id,
                max_depth=max_depth,
                max_tokens=max_tokens,
            )
            return subgraph
        except NodeNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # 4. POST /tasks/{task_id}/submit (and /api/tasks/{task_id}/submit)
    @app.post("/tasks/{task_id}/submit", status_code=status.HTTP_200_OK)
    @app.post("/api/tasks/{task_id}/submit", status_code=status.HTTP_200_OK)
    async def submit_task(task_id: str, req: SubmitTaskRequest):
        effective_task_id = task_id or req.task_id
        if not effective_task_id:
            raise HTTPException(status_code=400, detail="Missing task_id.")

        async with mutation_lock:
            # Idempotency check: replay of same submit returns completed task
            if req.idempotency_key and st:
                existing_event = st.check_idempotency(req.idempotency_key)
                if existing_event:
                    existing_node = dag.get_node(effective_task_id) or st.get_node(effective_task_id)
                    if existing_node:
                        return {
                            "task": existing_node,
                            "message": "Task submitted successfully (idempotent replay)",
                            **existing_node.model_dump(),
                        }

            target_node = dag.get_node(effective_task_id)
            if not target_node:
                raise HTTPException(status_code=404, detail=f"Task '{effective_task_id}' not found.")

            # If task was in STALE_CONFLICT and new output is provided, resolve conflict
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

        # Broadcast TASK_SUBMITTED
        await ws_manager.broadcast({
            "event": "TASK_SUBMITTED",
            "event_type": EVENT_TASK_SUBMITTED,
            "type": EVENT_TASK_SUBMITTED,
            "timestamp": time.time(),
            "node_id": effective_task_id,
            "task_id": effective_task_id,
            "agent_id": req.agent_id,
            "node": node.model_dump(),
            "actor": req.agent_id,
            "data": {
                "output": req.output,
                "status": node.status.value if hasattr(node.status, "value") else str(node.status),
                "version": node.version,
            },
        })

        return {
            "task": node,
            "message": "Task submitted successfully",
            **node.model_dump(),
        }

    # 5. POST /requirements/update (and /api/requirements/{req_id}/update, /requirements/{req_id}/update)
    @app.post("/requirements/update", status_code=status.HTTP_200_OK)
    @app.post("/requirements/{req_id}/update", status_code=status.HTTP_200_OK)
    @app.post("/api/requirements/{req_id}/update", status_code=status.HTTP_200_OK)
    async def update_requirement(
        req: UpdateRequirementRequest,
        req_id: Optional[str] = None,
    ):
        effective_req_id = req_id or req.req_id
        if not effective_req_id:
            raise HTTPException(status_code=400, detail="Missing requirement ID.")

        async with mutation_lock:
            # Idempotency check
            if req.idempotency_key and st:
                existing_event = st.check_idempotency(req.idempotency_key)
                if existing_event:
                    req_node = dag.get_node(effective_req_id) or st.get_node(effective_req_id)
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

        # Broadcast REQUIREMENT_UPDATED
        await ws_manager.broadcast({
            "event": "REQUIREMENT_UPDATED",
            "event_type": EVENT_REQUIREMENT_UPDATED,
            "type": EVENT_REQUIREMENT_UPDATED,
            "timestamp": time.time(),
            "node_id": effective_req_id,
            "req_id": effective_req_id,
            "new_spec": req.new_spec,
            "version": req_node.version,
            "actor": req.actor,
            "node": req_node.model_dump(),
            "data": {
                "new_spec": req.new_spec,
                "version": req_node.version,
            },
        })

        # Broadcast BLAST_RADIUS_ALERT if invalidated_nodes non-empty
        if invalidated_ids:
            await ws_manager.broadcast({
                "event": "BLAST_RADIUS_ALERT",
                "event_type": EVENT_CONFLICT_DETECTED,
                "type": EVENT_CONFLICT_DETECTED,
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
            })

            # Also broadcast individual CONFLICT_DETECTED event for each invalidated node
            for item in invalidated_ids:
                inv_node = item if isinstance(item, Node) else dag.get_node(item)
                if inv_node:
                    await ws_manager.broadcast({
                        "event": "CONFLICT_DETECTED",
                        "event_type": EVENT_CONFLICT_DETECTED,
                        "type": EVENT_CONFLICT_DETECTED,
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
                    })

        return {
            "requirement": req_node,
            "invalidated_nodes": invalidated_ids,
            "blast_radius_count": len(invalidated_ids),
            "message": f"Requirement updated. {len(invalidated_ids)} nodes marked STALE_CONFLICT.",
            "idempotent_replay": False,
        }

    # 6. GET /graph (and /api/graph), GET /export, POST /import
    @app.get("/graph", response_model=GraphStateResponse, status_code=status.HTTP_200_OK)
    @app.get("/api/graph", response_model=GraphStateResponse, status_code=status.HTTP_200_OK)
    async def get_graph():
        return dag.get_graph_state()

    @app.get("/export", response_model=GraphSnapshot, status_code=status.HTTP_200_OK)
    @app.get("/api/export", response_model=GraphSnapshot, status_code=status.HTTP_200_OK)
    async def export_snapshot():
        return dag.export_snapshot()

    @app.post("/import", status_code=status.HTTP_200_OK)
    @app.post("/api/import", status_code=status.HTTP_200_OK)
    async def import_snapshot(snapshot: GraphSnapshot, replace: bool = True):
        async with mutation_lock:
            try:
                counts = dag.import_snapshot(snapshot, replace=replace)
            except CycleError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except NodeNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))

        await ws_manager.broadcast({
            "event": "GRAPH_IMPORTED",
            "event_type": "GRAPH_IMPORTED",
            "type": "GRAPH_IMPORTED",
            "timestamp": time.time(),
            "data": {"replace": replace, **counts},
        })
        return {"status": "success", **counts}

    # 7. Supporting endpoints for node listing, single node, and events lineage
    @app.get("/nodes", response_model=List[Node])
    @app.get("/api/nodes", response_model=List[Node])
    async def list_nodes():
        return dag.list_nodes()

    @app.get("/nodes/{node_id}", response_model=Node)
    @app.get("/api/nodes/{node_id}", response_model=Node)
    async def get_node(node_id: str):
        node = dag.get_node(node_id)
        if not node:
            raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found.")
        return node

    @app.get("/nodes/{node_id}/events", response_model=List[Event])
    @app.get("/api/nodes/{node_id}/events", response_model=List[Event])
    async def get_node_events(node_id: str):
        return st.get_events_for_node(node_id)

    @app.get("/tasks/ready", response_model=List[Node])
    @app.get("/api/tasks/ready", response_model=List[Node])
    async def get_ready_tasks():
        return dag.get_ready_tasks()

    @app.get("/api/seed/templates")
    async def get_seed_templates():
        return {"templates": list_seed_templates()}

    async def _apply_seed_template(template: str, reset_first: bool = True) -> Dict[str, Any]:
        try:
            seed_nodes = get_seed_nodes(template)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

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
                    created_at=now,
                    updated_at=now,
                )
                dag.add_node(
                    node=node,
                    dependencies=item.get("dependencies", []),
                    actor=item.get("actor", "system"),
                    idempotency_key=item.get("idempotency_key"),
                )
                created_count += 1

        await ws_manager.broadcast({
            "event": "GRAPH_SEEDED",
            "event_type": "GRAPH_SEEDED",
            "type": "GRAPH_SEEDED",
            "timestamp": time.time(),
            "actor": "seed-engine",
            "data": {"template": template, "nodes_created": created_count},
        })

        state = dag.get_graph_state()
        ready_ids = [n.id for n in dag.get_ready_tasks()]
        return {
            "status": "success",
            "template": template,
            "nodes_created": created_count,
            "total_nodes": len(state.nodes),
            "ready_tasks": ready_ids,
        }

    @app.post("/api/seed")
    async def seed_graph_body(req: SeedGraphRequest = SeedGraphRequest()):
        return await _apply_seed_template(req.template, reset_first=req.reset)

    @app.post("/api/seed/{template}")
    async def seed_graph_template(template: str, reset: bool = True):
        return await _apply_seed_template(template, reset_first=reset)

    @app.post("/reset")
    @app.post("/api/reset")
    async def reset_graph():
        async with mutation_lock:
            dag.reset()
        await ws_manager.broadcast({
            "event": "GRAPH_RESET",
            "event_type": "GRAPH_RESET",
            "type": "GRAPH_RESET",
            "timestamp": time.time(),
        })
        return {"status": "success", "message": "Graph and event store reset."}

    return app


# Default module-level application instance
app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "harness.server:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )
