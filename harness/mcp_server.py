"""
Synapse MCP Server for OmniHarness.
Exposes board-scoped Knowledge Graph tools over:
  1. Hosted Streamable HTTP at `/mcp` (mounted on FastAPI with Bearer auth + immediate revocation).
  2. Local stdio transport (`python -m harness.mcp_server`) proxying via BaseAgentAdapter.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from auth.supabase_auth import AuthPrincipal, extract_bearer_token
from harness.models import Node, NodeStatus, NodeType

logger = logging.getLogger("omniharness.mcp")

# Per-request auth context populated by AuthenticatedMCPApp for hosted /mcp requests
_mcp_auth_ctx: contextvars.ContextVar[Optional[Tuple[AuthPrincipal, str]]] = contextvars.ContextVar(
    "omniharness_mcp_auth",
    default=None,
)


def _require_hosted_ctx() -> Tuple[AuthPrincipal, str]:
    ctx = _mcp_auth_ctx.get()
    if ctx is None:
        raise PermissionError("Unauthenticated MCP request: missing valid Board Agent Key or user token.")
    return ctx


class AuthenticatedMCPApp:
    """ASGI wrapper around FastMCP Streamable HTTP app enforcing Bearer auth & board scoping."""

    def __init__(
        self,
        inner_app: Any,
        authenticate_fn: Callable[[Optional[str], Optional[str]], Awaitable[Tuple[AuthPrincipal, str]]],
    ):
        self.inner_app = inner_app
        self.authenticate_fn = authenticate_fn

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.inner_app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        query_string = scope.get("query_string", b"").decode("latin-1")
        query_params: Dict[str, str] = {}
        for part in query_string.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                query_params[k] = v

        raw_token = extract_bearer_token(
            headers.get("authorization") or headers.get("x-api-key"),
            query_params.get("token"),
        )
        requested_board_id = headers.get("x-board-id") or query_params.get("board_id")

        try:
            principal, board_id = await self.authenticate_fn(raw_token, requested_board_id)
        except PermissionError as exc:
            status_code = 403 if "forbidden" in str(exc).lower() or "access" in str(exc).lower() else 401
            body = json.dumps({"error": str(exc)}).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return
        except Exception as exc:
            body = json.dumps({"error": f"Authentication error: {exc}"}).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": body})
            return

        ctx_token = _mcp_auth_ctx.set((principal, board_id))
        try:
            await self.inner_app(scope, receive, send)
        finally:
            _mcp_auth_ctx.reset(ctx_token)


def create_hosted_mcp_server(
    store: Any,
    registry: Any,
    ws_manager: Any,
    mutation_lock: asyncio.Lock,
) -> FastMCP:
    """Create a FastMCP instance wired directly to the live OmniHarness BoardDAGRegistry & WS hub."""
    mcp = FastMCP(
        name="synapse-mcp",
        instructions=(
            "SYNAPSE Multi-Agent Knowledge Board MCP server. "
            "Every tool call is scoped to the Knowledge Board authorized by your Bearer key "
            "and updates the live dashboard in real time."
        ),
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.tool()
    async def get_board_info() -> Dict[str, Any]:
        """Return metadata, caller identity, and node counts for the authorized Knowledge Board."""
        principal, board_id = _require_hosted_ctx()
        board = store.get_board(board_id) if hasattr(store, "get_board") else None
        dag = registry.get_dag(board_id)
        state = dag.get_graph_state()
        return {
            "board": board.model_dump() if board else {"id": board_id, "title": board_id},
            "principal": {
                "kind": principal.kind,
                "user_id": principal.user_id,
                "agent_key_name": principal.agent_key_name,
                "scopes": list(principal.scopes),
            },
            "stats": state.stats,
        }

    @mcp.tool()
    async def list_nodes(
        status: Optional[str] = None,
        node_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List Knowledge Graph nodes on the authorized board, optionally filtered by status or type."""
        _, board_id = _require_hosted_ctx()
        dag = registry.get_dag(board_id)
        nodes = dag.list_nodes()
        if status:
            want_status = status.strip().upper()
            nodes = [n for n in nodes if n.status.value == want_status]
        if node_type:
            want_type = node_type.strip().upper()
            nodes = [n for n in nodes if n.type.value == want_type]
        return [n.model_dump() for n in nodes]

    @mcp.tool()
    async def list_ready_tasks() -> List[Dict[str, Any]]:
        """Return all PENDING TASK nodes whose upstream dependencies are COMPLETED."""
        _, board_id = _require_hosted_ctx()
        dag = registry.get_dag(board_id)
        return [n.model_dump() for n in dag.get_ready_tasks()]

    @mcp.tool()
    async def get_task_subgraph(task_id: str, max_tokens: int = 1500) -> Dict[str, Any]:
        """Fetch scoped ~1.5k token ancestor context and conflict status for a task."""
        _, board_id = _require_hosted_ctx()
        dag = registry.get_dag(board_id)
        return dag.get_scoped_subgraph(node_id=task_id, max_tokens=max_tokens).model_dump()

    @mcp.tool()
    async def claim_task(
        task_id: str,
        agent_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Atomically claim a ready TASK node and broadcast TASK_CLAIMED to the board's live UI."""
        principal, board_id = _require_hosted_ctx()
        if "tasks:claim" not in principal.scopes and principal.kind != "human":
            raise PermissionError("Insufficient scope: tasks:claim required.")

        resolved_agent = agent_id or principal.agent_key_name or principal.display_name or principal.user_id
        tool_name = "synapse-mcp"
        ik = idempotency_key or f"mcp-claim-{board_id}-{task_id}-{uuid.uuid4().hex[:8]}"
        dag = registry.get_dag(board_id)

        async with mutation_lock:
            node = dag.claim_task(
                task_id=task_id,
                agent_id=resolved_agent,
                tool_name=tool_name,
                idempotency_key=ik,
            )
            user, _ = store.upsert_workspace_user(
                user_id=resolved_agent,
                tool_name=tool_name,
                status="working",
                last_task_id=task_id,
                preserve_working=False,
                board_id=board_id,
            )

        await ws_manager.broadcast(
            {
                "event": "TASK_CLAIMED",
                "event_type": "EVENT_TASK_CLAIMED",
                "type": "EVENT_TASK_CLAIMED",
                "board_id": board_id,
                "timestamp": time.time(),
                "node_id": task_id,
                "task_id": task_id,
                "agent_id": resolved_agent,
                "tool_name": tool_name,
                "node": node.model_dump(),
                "user": user.model_dump() if user else None,
                "actor": resolved_agent,
            },
            board_id=board_id,
        )
        return {"message": "Task claimed successfully", "task": node.model_dump()}

    @mcp.tool()
    async def submit_task(
        task_id: str,
        output: Dict[str, Any],
        agent_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit completed output for a claimed or conflicted TASK node."""
        principal, board_id = _require_hosted_ctx()
        if "tasks:submit" not in principal.scopes and principal.kind != "human":
            raise PermissionError("Insufficient scope: tasks:submit required.")

        resolved_agent = agent_id or principal.agent_key_name or principal.display_name or principal.user_id
        ik = idempotency_key or f"mcp-submit-{board_id}-{task_id}-{uuid.uuid4().hex[:8]}"
        dag = registry.get_dag(board_id)

        async with mutation_lock:
            target = dag.get_node(task_id)
            if target and target.status == NodeStatus.STALE_CONFLICT:
                node = dag.resolve_conflict(
                    task_id=task_id,
                    new_output=output,
                    actor=resolved_agent,
                    idempotency_key=ik,
                )
            else:
                node = dag.submit_task(
                    task_id=task_id,
                    agent_id=resolved_agent,
                    output=output,
                    idempotency_key=ik,
                )
            user, _ = store.upsert_workspace_user(
                user_id=resolved_agent,
                tool_name="synapse-mcp",
                status="joined",
                last_task_id=task_id,
                preserve_working=False,
                board_id=board_id,
            )

        await ws_manager.broadcast(
            {
                "event": "TASK_SUBMITTED",
                "event_type": "EVENT_TASK_SUBMITTED",
                "type": "EVENT_TASK_SUBMITTED",
                "board_id": board_id,
                "timestamp": time.time(),
                "node_id": task_id,
                "task_id": task_id,
                "agent_id": resolved_agent,
                "node": node.model_dump(),
                "user": user.model_dump() if user else None,
                "actor": resolved_agent,
            },
            board_id=board_id,
        )
        return {"message": "Task submitted successfully", "task": node.model_dump()}

    @mcp.tool()
    async def create_node(
        node_id: str,
        title: str,
        node_type: str = "TASK",
        payload: Optional[Dict[str, Any]] = None,
        dependencies: Optional[List[str]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a REQUIREMENT, DECISION, TASK, or ARTIFACT node on the authorized board."""
        principal, board_id = _require_hosted_ctx()
        if "nodes:create" not in principal.scopes and principal.kind != "human":
            raise PermissionError("Insufficient scope: nodes:create required.")

        resolved_agent = principal.agent_key_name or principal.display_name or principal.user_id
        dag = registry.get_dag(board_id)
        ik = idempotency_key or f"mcp-create-{board_id}-{node_id}-{uuid.uuid4().hex[:8]}"

        async with mutation_lock:
            node = Node(
                id=node_id,
                title=title,
                type=NodeType(node_type.upper()),
                status=NodeStatus.PENDING,
                payload=payload or {},
                board_id=board_id,
                created_at=time.time(),
                updated_at=time.time(),
            )
            created = dag.add_node(
                node=node,
                dependencies=dependencies or [],
                actor=resolved_agent,
                idempotency_key=ik,
            )

        await ws_manager.broadcast(
            {
                "event": "NODE_CREATED",
                "event_type": "EVENT_NODE_CREATED",
                "type": "EVENT_NODE_CREATED",
                "board_id": board_id,
                "timestamp": time.time(),
                "node_id": created.id,
                "node": created.model_dump(),
                "actor": resolved_agent,
            },
            board_id=board_id,
        )
        return {"message": "Node created successfully", "node": created.model_dump()}

    @mcp.tool()
    async def update_requirement(
        req_id: str,
        new_spec: str,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update a requirement node and propagate STALE_CONFLICT blast-radius invalidation."""
        principal, board_id = _require_hosted_ctx()
        if "nodes:create" not in principal.scopes and principal.kind != "human":
            raise PermissionError("Insufficient scope for requirement updates.")

        resolved_agent = principal.agent_key_name or principal.display_name or principal.user_id
        dag = registry.get_dag(board_id)
        ik = idempotency_key or f"mcp-req-{board_id}-{req_id}-{uuid.uuid4().hex[:8]}"

        async with mutation_lock:
            req_node, invalidated_ids = dag.update_requirement(
                req_id=req_id,
                new_spec=new_spec,
                actor=resolved_agent,
                idempotency_key=ik,
            )

        await ws_manager.broadcast(
            {
                "event": "REQUIREMENT_UPDATED",
                "event_type": "EVENT_REQUIREMENT_UPDATED",
                "type": "EVENT_REQUIREMENT_UPDATED",
                "board_id": board_id,
                "timestamp": time.time(),
                "node_id": req_id,
                "new_spec": new_spec,
                "version": req_node.version,
                "actor": resolved_agent,
                "node": req_node.model_dump(),
            },
            board_id=board_id,
        )
        if invalidated_ids:
            await ws_manager.broadcast(
                {
                    "event": "BLAST_RADIUS_ALERT",
                    "event_type": "EVENT_CONFLICT_DETECTED",
                    "type": "EVENT_CONFLICT_DETECTED",
                    "board_id": board_id,
                    "timestamp": time.time(),
                    "root_cause_node": req_id,
                    "invalidated_nodes": invalidated_ids,
                    "actor": resolved_agent,
                },
                board_id=board_id,
            )
        return {
            "requirement": req_node.model_dump(),
            "invalidated_nodes": invalidated_ids,
            "blast_radius_count": len(invalidated_ids),
        }

    return mcp


def run_stdio_bridge() -> None:
    """
    Run a local stdio MCP server that proxies to a remote SYNAPSE server using
    OMNIHARNESS_SERVER / OMNIHARNESS_API_KEY / OMNIHARNESS_BOARD_ID (or .omniharness.json).
    """
    from adapters.base import BaseAgentAdapter
    from adapters.cli import load_saved_profile

    saved = load_saved_profile()
    server_url = (os.environ.get("OMNIHARNESS_SERVER") or saved.get("server") or "http://localhost:8000").rstrip("/")
    api_key = os.environ.get("OMNIHARNESS_API_KEY") or saved.get("api_key")
    board_id = os.environ.get("OMNIHARNESS_BOARD_ID") or saved.get("board_id")
    agent_id = os.environ.get("OMNIHARNESS_AGENT_ID") or saved.get("agent_id") or "synapse-mcp"

    client = BaseAgentAdapter(
        agent_id=agent_id,
        server_url=server_url,
        tool_name="synapse-mcp",
        api_key=api_key,
        board_id=board_id,
    )

    bridge = FastMCP(name="synapse-mcp-stdio")

    @bridge.tool()
    def list_ready_tasks() -> List[Dict[str, Any]]:
        """List ready tasks on the connected SYNAPSE Knowledge Board."""
        res = client.client.get("/api/tasks/ready")
        res.raise_for_status()
        return res.json()

    @bridge.tool()
    def get_task_subgraph(task_id: str) -> Dict[str, Any]:
        """Get scoped ~1.5k token ancestor context for a task."""
        return client.get_subgraph(task_id)

    @bridge.tool()
    def claim_task(task_id: str) -> Dict[str, Any]:
        """Claim a task lease on the connected SYNAPSE Knowledge Board."""
        return client.claim_task(task_id)

    @bridge.tool()
    def submit_task(task_id: str, output: Dict[str, Any]) -> Dict[str, Any]:
        """Submit task output on the connected SYNAPSE Knowledge Board."""
        return client.submit_result(task_id, output)

    @bridge.tool()
    def create_node(
        node_id: str,
        title: str,
        node_type: str = "TASK",
        payload: Optional[Dict[str, Any]] = None,
        dependencies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a node on the connected SYNAPSE Knowledge Board."""
        return client.create_node(
            node_id=node_id,
            title=title,
            node_type=node_type,
            payload=payload,
            dependencies=dependencies,
        )

    @bridge.tool()
    def update_requirement(req_id: str, new_spec: str) -> Dict[str, Any]:
        """Update a requirement specification and trigger blast-radius invalidation."""
        return client.update_requirement(req_id=req_id, new_spec=new_spec)

    bridge.run(transport="stdio")


if __name__ == "__main__":
    run_stdio_bridge()
