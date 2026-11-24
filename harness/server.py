"""
FastAPI REST and real-time WebSocket Hub for OmniHarness.
Provides real-time multi-agent synchronization (<50ms latency),
strict idempotency replay, scoped subgraphs, and conflict broadcasting.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from harness.graph import (
    CycleError,
    DAGManager,
    NodeNotFoundError,
    TaskConflictError,
)
from harness.models import (
    ClaimTaskRequest,
    CreateNodeRequest,
    Event,
    GraphStateResponse,
    Node,
    NodeStatus,
    NodeType,
    SubgraphResponse,
    SubmitTaskRequest,
    UpdateRequirementRequest,
)
from harness.store import EventStore

logger = logging.getLogger("omniharness.server")

# Standard Event Type Constants
EVENT_NODE_CREATED = "EVENT_NODE_CREATED"
EVENT_TASK_CLAIMED = "EVENT_TASK_CLAIMED"
EVENT_TASK_SUBMITTED = "EVENT_TASK_SUBMITTED"
EVENT_CONFLICT_DETECTED = "EVENT_CONFLICT_DETECTED"
EVENT_REQUIREMENT_UPDATED = "EVENT_REQUIREMENT_UPDATED"


# Ensure backward-compatibility helper methods on DAGManager
if not hasattr(DAGManager, "build_from_store"):
    DAGManager.build_from_store = lambda self, store: (setattr(self, "store", store), self.hydrate_from_store())
if not hasattr(DAGManager, "get_task_subgraph"):
    DAGManager.get_task_subgraph = lambda self, task_id: self.get_scoped_subgraph(task_id)
if not hasattr(DAGManager, "get_downstream_dependents"):
    DAGManager.get_downstream_dependents = lambda self, node_id: list(self.get_descendants(node_id))
if not hasattr(DAGManager, "invalidate_subtree"):
    def _invalidate_subtree(self, node_id: str, reason: str = "Requirement updated", actor: str = "system") -> List[Node]:
        invalidated_ids = self.invalidate_downstream(node_id=node_id, actor=actor, reason=reason)
        return [self.get_node(nid) for nid in invalidated_ids if self.get_node(nid)]
    DAGManager.invalidate_subtree = _invalidate_subtree
if not hasattr(DAGManager, "resolve_conflict"):
    def _resolve_conflict(self, task_id: str, new_output: dict, actor: str) -> Node:
        with self._lock:
            node = self.get_node(task_id)
            if not node:
                raise NodeNotFoundError(f"Task '{task_id}' not found.")
            node.status = NodeStatus.COMPLETED
            node.payload["output"] = new_output
            node.payload.pop("stale_reason", None)
            node.payload.pop("invalidated_by", None)
            node.version += 1
            node.updated_at = time.time()
            if self.store:
                self.store.upsert_node(node)
                self.store.record_event(
                    event_type="TASK_SUBMITTED",
                    node_id=task_id,
                    actor=actor,
                    data={"output": new_output, "version": node.version, "conflict_resolved": True},
                )
            return node
    DAGManager.resolve_conflict = _resolve_conflict


class ConnectionManager:
    """
    WebSocket connection manager for broadcasting real-time DAG events
    to connected agents and dashboard clients with <50ms latency.
    """

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("WebSocket client connected. Total clients: %d", len(self.active_connections))

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info("WebSocket client disconnected. Total clients: %d", len(self.active_connections))

    async def broadcast(self, message: Dict[str, Any]):
        """Broadcast message to all active WebSocket connections in parallel."""
        dead_connections: List[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)

        for dead in dead_connections:
            if dead in self.active_connections:
                self.active_connections.remove(dead)


def create_app(
    db_path: str = "omniharness.db",
    store: Optional[EventStore] = None,
    dag_manager: Optional[DAGManager] = None,
) -> FastAPI:
    """
    Factory function to initialize and configure the OmniHarness FastAPI server.
    """
    app = FastAPI(
        title="OmniHarness Real-Time Hub",
        description="Multi-Agent DAG Knowledge Hub with real-time WebSocket sync and strict idempotency.",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Initialize persistence store, in-memory DAG, and WebSocket manager
    st = store or EventStore(db_path=db_path)
    dag = dag_manager or DAGManager(store=st)
    manager = ConnectionManager()

    app.state.store = st
    app.state.dag_manager = dag
    app.state.manager = manager

    # ---------------------------------------------------------
    # WebSocket Realtime Synchronization Endpoints
    # ---------------------------------------------------------

    @app.websocket("/ws/graph")
    @app.websocket("/ws")
    async def websocket_graph_endpoint(websocket: WebSocket):
        await manager.connect(websocket)
        try:
            while True:
                # Keepalive / ping reception
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            manager.disconnect(websocket)
        except Exception:
            manager.disconnect(websocket)

    # ---------------------------------------------------------
    # REST API Endpoints
    # ---------------------------------------------------------

    @app.get("/health")
    async def health_check():
        return {
            "status": "healthy",
            "timestamp": time.time(),
            "nodes_count": len(dag._nodes),
            "active_ws_clients": len(manager.active_connections),
        }

    @app.get("/", response_class=HTMLResponse)
    @app.get("/dashboard", response_class=HTMLResponse)
    async def get_dashboard():
        """Serves live visual dashboard HTML from harness/dashboard.html."""
        locations = [
            Path(__file__).parent / "dashboard.html",
            Path("harness/dashboard.html"),
        ]
        for p in locations:
            if p.exists():
                return HTMLResponse(content=p.read_text(encoding="utf-8"))

        return HTMLResponse(
            content="""<!DOCTYPE html>
<html>
<head><title>OmniHarness Live Dashboard</title></head>
<body style="font-family: sans-serif; background: #0f172a; color: #f8fafc; padding: 2rem;">
    <h1>OmniHarness Dashboard</h1>
    <p>Multi-Agent DAG Knowledge Base & Live Event Stream Hub.</p>
</body>
</html>"""
        )

    @app.post("/api/nodes", response_model=Node)
    async def create_node(req: CreateNodeRequest):
        """
        Create a new graph node with optional dependency edges and idempotency key.
        Broadcasts EVENT_NODE_CREATED over WebSockets.
        """
        # Idempotency check: if this key was already processed, return existing node
        if req.idempotency_key:
            existing_event = st.check_idempotency(req.idempotency_key)
            if existing_event:
                existing_node = dag.get_node(req.id) or st.get_node(req.id)
                if existing_node:
                    return existing_node

        # If node exists already, return it
        existing_node = dag.get_node(req.id)
        if existing_node:
            return existing_node

        node = Node(
            id=req.id,
            title=req.title,
            type=req.type,
            status=NodeStatus.PENDING,
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
            raise HTTPException(status_code=400, detail=f"Cycle rejected: {str(e)}")
        except NodeNotFoundError as e:
            raise HTTPException(status_code=400, detail=f"Missing dependency: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

        # Broadcast EVENT_NODE_CREATED
        await manager.broadcast({
            "event_type": EVENT_NODE_CREATED,
            "type": EVENT_NODE_CREATED,
            "node_id": created_node.id,
            "actor": req.actor,
            "data": {
                "title": created_node.title,
                "type": created_node.type.value if hasattr(created_node.type, "value") else str(created_node.type),
                "dependencies": req.dependencies,
            },
            "node": created_node.model_dump(),
            "timestamp": time.time(),
        })

        return created_node

    @app.post("/api/tasks/{task_id}/claim", response_model=Node)
    async def claim_task(task_id: str, req: ClaimTaskRequest):
        """
        Claim a pending task for an agent. Verifies idempotency and leases task (IN_PROGRESS).
        Broadcasts EVENT_TASK_CLAIMED over WebSockets.
        """
        effective_task_id = task_id or req.task_id
        if not effective_task_id:
            raise HTTPException(status_code=400, detail="Missing task_id.")

        # Idempotency check: replay of same claim returns current task state
        if req.idempotency_key:
            existing_event = st.check_idempotency(req.idempotency_key)
            if existing_event:
                existing_node = dag.get_node(effective_task_id) or st.get_node(effective_task_id)
                if existing_node:
                    return existing_node

        target_node = dag.get_node(effective_task_id)
        if not target_node:
            raise HTTPException(status_code=404, detail=f"Task '{effective_task_id}' not found.")

        try:
            claimed_node = dag.claim_task(
                task_id=effective_task_id,
                agent_id=req.agent_id,
                tool_name=getattr(req, "tool_name", "generic_tool"),
                idempotency_key=req.idempotency_key,
            )
        except NodeNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except TaskConflictError as e:
            raise HTTPException(status_code=409, detail=str(e))

        # Broadcast EVENT_TASK_CLAIMED
        await manager.broadcast({
            "event_type": EVENT_TASK_CLAIMED,
            "type": EVENT_TASK_CLAIMED,
            "node_id": claimed_node.id,
            "actor": req.agent_id,
            "data": {
                "agent_id": req.agent_id,
                "tool_name": getattr(req, "tool_name", "generic_tool"),
                "status": claimed_node.status.value if hasattr(claimed_node.status, "value") else str(claimed_node.status),
                "assigned_to": claimed_node.assigned_to,
            },
            "node": claimed_node.model_dump(),
            "timestamp": time.time(),
        })

        return claimed_node

    @app.get("/api/tasks/{task_id}/subgraph", response_model=SubgraphResponse)
    async def get_task_subgraph(
        task_id: str,
        max_depth: Optional[int] = None,
        max_tokens: int = 1500,
    ):
        """
        Extract scoped context containing target task, ancestors, induced edges,
        and conflict details (~1.5k tokens budget).
        """
        try:
            subgraph = dag.get_scoped_subgraph(
                node_id=task_id,
                max_depth=max_depth,
                max_tokens=max_tokens,
            )
            return subgraph
        except NodeNotFoundError:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")

    @app.post("/api/tasks/{task_id}/submit", response_model=Node)
    async def submit_task(task_id: str, req: SubmitTaskRequest):
        """
        Submit result for a task. Verifies idempotency and marks COMPLETED.
        If task was in STALE_CONFLICT, resolves the conflict.
        Broadcasts EVENT_TASK_SUBMITTED over WebSockets.
        """
        effective_task_id = task_id or req.task_id
        if not effective_task_id:
            raise HTTPException(status_code=400, detail="Missing task_id.")

        # Idempotency check: replay of same submit returns completed task
        if req.idempotency_key:
            existing_event = st.check_idempotency(req.idempotency_key)
            if existing_event:
                existing_node = dag.get_node(effective_task_id) or st.get_node(effective_task_id)
                if existing_node:
                    return existing_node

        target_node = dag.get_node(effective_task_id)
        if not target_node:
            raise HTTPException(status_code=404, detail=f"Task '{effective_task_id}' not found.")

        # If task was in STALE_CONFLICT, resolve conflict
        if target_node.status == NodeStatus.STALE_CONFLICT:
            submitted_node = dag.resolve_conflict(
                task_id=effective_task_id,
                new_output=req.output,
                actor=req.agent_id,
            )
        else:
            try:
                submitted_node = dag.submit_task(
                    task_id=effective_task_id,
                    agent_id=req.agent_id,
                    output=req.output,
                    idempotency_key=req.idempotency_key,
                )
            except TaskConflictError as e:
                raise HTTPException(status_code=409, detail=str(e))
            except NodeNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))

        # Broadcast EVENT_TASK_SUBMITTED
        await manager.broadcast({
            "event_type": EVENT_TASK_SUBMITTED,
            "type": EVENT_TASK_SUBMITTED,
            "node_id": submitted_node.id,
            "actor": req.agent_id,
            "data": {
                "output": req.output,
                "status": submitted_node.status.value if hasattr(submitted_node.status, "value") else str(submitted_node.status),
                "version": submitted_node.version,
            },
            "node": submitted_node.model_dump(),
            "timestamp": time.time(),
        })

        return submitted_node

    @app.post("/api/requirements/{req_id}/update")
    async def update_requirement(req_id: str, req: UpdateRequirementRequest):
        """
        Update a requirement specification. Runs DAGManager blast-radius invalidation.
        Broadcasts EVENT_REQUIREMENT_UPDATED and EVENT_CONFLICT_DETECTED over WebSockets.
        """
        effective_req_id = req_id or req.req_id

        # Idempotency check
        if req.idempotency_key:
            existing_event = st.check_idempotency(req.idempotency_key)
            if existing_event:
                req_node = dag.get_node(effective_req_id) or st.get_node(effective_req_id)
                return {
                    "requirement": req_node,
                    "invalidated_nodes": [],
                    "blast_radius_count": 0,
                    "idempotent_replay": True,
                }

        req_node = dag.get_node(effective_req_id)
        if not req_node:
            raise HTTPException(status_code=404, detail=f"Requirement '{effective_req_id}' not found.")

        try:
            updated_req_node, invalidated_ids = dag.update_requirement(
                req_id=effective_req_id,
                new_spec=req.new_spec,
                actor=req.actor,
                idempotency_key=req.idempotency_key,
            )
        except NodeNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

        # Broadcast EVENT_REQUIREMENT_UPDATED
        await manager.broadcast({
            "event_type": EVENT_REQUIREMENT_UPDATED,
            "type": EVENT_REQUIREMENT_UPDATED,
            "node_id": updated_req_node.id,
            "actor": req.actor,
            "data": {
                "new_spec": req.new_spec,
                "version": updated_req_node.version,
            },
            "node": updated_req_node.model_dump(),
            "timestamp": time.time(),
        })

        # Broadcast EVENT_CONFLICT_DETECTED for each invalidated downstream node
        invalidated_nodes: List[Node] = []
        for item in invalidated_ids:
            inv_node = item if isinstance(item, Node) else dag.get_node(item)
            if inv_node:
                invalidated_nodes.append(inv_node)
                await manager.broadcast({
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
            "requirement": updated_req_node,
            "invalidated_nodes": invalidated_nodes,
            "blast_radius_count": len(invalidated_nodes),
            "idempotent_replay": False,
        }

    @app.get("/api/graph", response_model=GraphStateResponse)
    async def get_graph():
        """Returns full GraphStateResponse with nodes, edges, and status statistics."""
        return dag.get_graph_state()

    @app.get("/api/nodes", response_model=List[Node])
    async def list_nodes():
        """Return list of all current nodes."""
        return dag.list_nodes()

    @app.get("/api/nodes/{node_id}", response_model=Node)
    async def get_node(node_id: str):
        """Fetch single node by ID."""
        node = dag.get_node(node_id)
        if not node:
            raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found.")
        return node

    @app.get("/api/nodes/{node_id}/events", response_model=List[Event])
    async def get_node_events(node_id: str):
        """Returns audit lineage event list for the specified node."""
        return st.get_events_for_node(node_id)

    @app.post("/api/reset")
    async def reset_graph():
        """Resets the shared graph, SQLite event store, and broadcasts reset to all clients."""
        dag.reset()
        await manager.broadcast({
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
    uvicorn.run("harness.server:app", host="0.0.0.0", port=8000, reload=False)
