"""
End-to-end behavioral tests for:
  - Fail-closed authentication on REST, WebSocket, and /mcp (401/1008 when unauthenticated)
  - Multi-board Knowledge Boards ('building' vs 'built') with strict board isolation
  - Friend invite -> pending -> owner approval -> editor/viewer RBAC -> removal
  - Persistent revocable Board Agent Keys (omni_...) with cascade revocation on member removal
  - Hosted Synapse MCP (/mcp) Streamable HTTP tool calls updating the live board DAG
"""
from __future__ import annotations

import os
import tempfile
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from adapters.base import BaseAgentAdapter
from auth.supabase_auth import SupabaseAuthClient
from harness.server import create_app

TEST_JWT_SECRET = "test-only-hs256-secret-32-bytes-minimum!!"


def _make_user_jwt(user_id: str, email: str, display_name: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": user_id,
            "email": email,
            "aud": "authenticated",
            "iat": now,
            "exp": now + 3600,
            "user_metadata": {"display_name": display_name},
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )


@pytest.fixture
def secure_client():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    auth_client = SupabaseAuthClient(local_jwt_secret=TEST_JWT_SECRET, local_algorithm="HS256")
    app = create_app(db_path=db_path, auth_enabled=True, auth_client=auth_client)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        for ext in ("", "-wal", "-shm"):
            p = f"{db_path}{ext}"
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_fail_closed_unauthenticated_endpoints(secure_client: TestClient):
    """Unauthenticated requests must load the UI shell/health, while blocking all board/DAG/MCP endpoints."""
    assert secure_client.get("/health").status_code == 200
    assert secure_client.get("/dashboard").status_code == 200
    cfg = secure_client.get("/api/auth/config")
    assert cfg.status_code == 200
    assert cfg.json()["auth_enabled"] is True

    assert secure_client.get("/api/boards").status_code == 401
    assert secure_client.get("/api/graph").status_code == 401
    assert secure_client.post("/api/nodes", json={"id": "N1", "title": "X", "type": "TASK"}).status_code == 401
    assert secure_client.post("/api/reset").status_code == 401
    assert secure_client.get("/llms.txt").status_code == 401
    assert secure_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}).status_code == 401
    assert secure_client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}).status_code == 401

    with pytest.raises(Exception):
        with secure_client.websocket_connect("/ws/graph") as ws:
            ws.receive_json()


def test_multi_board_isolation_and_stages(secure_client: TestClient):
    """Users can maintain multiple 'building' and 'built' boards without node/DAG bleed."""
    owner_jwt = _make_user_jwt("owner-1", "owner@example.com", "Owner")

    b1 = secure_client.post(
        "/api/boards",
        headers=_auth(owner_jwt),
        json={"title": "Chat Box", "stage": "building", "seed_template": "auth"},
    )
    assert b1.status_code == 201
    board1_id = b1.json()["id"]
    assert b1.json()["stage"] == "building"

    b2 = secure_client.post(
        "/api/boards",
        headers=_auth(owner_jwt),
        json={"title": "Tic Tac Toe", "stage": "built"},
    )
    assert b2.status_code == 201
    board2_id = b2.json()["id"]
    assert b2.json()["stage"] == "built"

    # Toggle stage on board1
    patched = secure_client.patch(
        f"/api/boards/{board1_id}",
        headers=_auth(owner_jwt),
        json={"stage": "built"},
    )
    assert patched.status_code == 200
    assert patched.json()["stage"] == "built"

    # Same node ID REQ-01 can exist independently on Board 2 without conflicting with Board 1
    n2 = secure_client.post(
        f"/api/boards/{board2_id}/nodes",
        headers=_auth(owner_jwt),
        json={"id": "REQ-01", "title": "Board 2 Spec", "type": "REQUIREMENT"},
    )
    assert n2.status_code == 201

    g1 = secure_client.get(f"/api/boards/{board1_id}/graph", headers=_auth(owner_jwt)).json()
    g2 = secure_client.get(f"/api/boards/{board2_id}/graph", headers=_auth(owner_jwt)).json()
    assert any(n["id"] == "TSK-01" for n in g1["nodes"])
    assert [n["id"] for n in g2["nodes"]] == ["REQ-01"]
    assert g2["nodes"][0]["title"] == "Board 2 Spec"


def test_friend_approval_rbac_and_cascade_key_revocation(secure_client: TestClient):
    """
    A friend cannot access a board until approved by the owner.
    Once approved, they and their persistent AI agent key can work until the owner removes them,
    which immediately revokes both the friend's access and any AI keys they created.
    """
    owner_jwt = _make_user_jwt("owner-1", "owner@example.com", "Himanshu")
    friend_jwt = _make_user_jwt("friend-2", "friend@example.com", "Aarav")

    board_res = secure_client.post(
        "/api/boards",
        headers=_auth(owner_jwt),
        json={"title": "Shared Knowledge Board", "seed_template": "auth"},
    )
    assert board_res.status_code == 201
    board_id = board_res.json()["id"]

    # Stranger is blocked before invite/approval
    assert secure_client.get(f"/api/boards/{board_id}/graph", headers=_auth(friend_jwt)).status_code == 403

    # Owner creates invite requiring approval
    inv_res = secure_client.post(
        f"/api/boards/{board_id}/invites",
        headers=_auth(owner_jwt),
        json={"role": "editor", "require_approval": True},
    )
    assert inv_res.status_code == 201
    invite_code = inv_res.json()["code"]

    # Friend requests to join -> status=pending -> still blocked from graph
    join_res = secure_client.post(
        "/api/boards/join",
        headers=_auth(friend_jwt),
        json={"invite_code": invite_code},
    )
    assert join_res.status_code == 200
    assert join_res.json()["pending_approval"] is True
    assert secure_client.get(f"/api/boards/{board_id}/graph", headers=_auth(friend_jwt)).status_code == 403

    # Owner approves friend
    app_res = secure_client.post(
        f"/api/boards/{board_id}/members/friend-2/approve",
        headers=_auth(owner_jwt),
    )
    assert app_res.status_code == 200
    assert app_res.json()["status"] == "approved"

    # Friend can now read & create nodes, but CANNOT reset/admin the owner's board
    assert secure_client.get(f"/api/boards/{board_id}/graph", headers=_auth(friend_jwt)).status_code == 200
    assert secure_client.post(f"/api/boards/{board_id}/reset", headers=_auth(friend_jwt)).status_code == 403

    # Friend mints a persistent Board Agent Key for their AI
    key_res = secure_client.post(
        f"/api/boards/{board_id}/agent-keys",
        headers=_auth(friend_jwt),
        json={"name": "friend-codex"},
    )
    assert key_res.status_code == 201
    agent_api_key = key_res.json()["api_key"]
    assert agent_api_key.startswith("omni_")

    # Agent key works on the authorized board (via adapter & REST) and is blocked from admin reset or other boards
    other_board = secure_client.post(
        "/api/boards",
        headers=_auth(owner_jwt),
        json={"title": "Private Owner Board"},
    ).json()["id"]
    assert secure_client.get(f"/api/boards/{other_board}/graph", headers=_auth(agent_api_key)).status_code == 403
    assert secure_client.post(f"/api/boards/{board_id}/reset", headers=_auth(agent_api_key)).status_code == 403

    ready_res = secure_client.get("/api/tasks/ready", headers=_auth(agent_api_key))
    assert ready_res.status_code == 200
    assert any(t["id"] == "TSK-01" for t in ready_res.json())

    # Owner removes friend -> friend blocked AND friend's agent key immediately cascade-revoked
    del_res = secure_client.delete(
        f"/api/boards/{board_id}/members/friend-2",
        headers=_auth(owner_jwt),
    )
    assert del_res.status_code == 200
    assert del_res.json()["removed"] is True
    assert del_res.json()["agent_keys_revoked"] == 1

    assert secure_client.get(f"/api/boards/{board_id}/graph", headers=_auth(friend_jwt)).status_code == 403
    assert secure_client.get("/api/tasks/ready", headers=_auth(agent_api_key)).status_code == 401


def test_synapse_mcp_streamable_http_and_revocation(secure_client: TestClient):
    """Hosted /mcp endpoint executes board-scoped DAG tools and stops immediately when the key is revoked."""
    owner_jwt = _make_user_jwt("owner-1", "owner@example.com", "Himanshu")

    board_id = secure_client.post(
        "/api/boards",
        headers=_auth(owner_jwt),
        json={"title": "MCP Workspace", "seed_template": "auth"},
    ).json()["id"]

    key_data = secure_client.post(
        f"/api/boards/{board_id}/agent-keys",
        headers=_auth(owner_jwt),
        json={"name": "synapse-mcp-client"},
    ).json()
    api_key = key_data["api_key"]
    key_id = key_data["key"]["id"]

    mcp_headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }

    init_res = secure_client.post(
        "/mcp",
        headers=mcp_headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest-mcp", "version": "1.0"},
            },
        },
    )
    assert init_res.status_code == 200
    assert "result" in init_res.json()

    tools_res = secure_client.post(
        "/mcp/",
        headers=mcp_headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert tools_res.status_code == 200
    tool_names = {t["name"] for t in tools_res.json()["result"]["tools"]}
    assert {"get_board_info", "list_ready_tasks", "claim_task", "submit_task", "create_node"}.issubset(tool_names)

    # Claim and submit TSK-01 via MCP
    claim_res = secure_client.post(
        "/mcp/",
        headers=mcp_headers,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "claim_task", "arguments": {"task_id": "TSK-01"}},
        },
    )
    assert claim_res.status_code == 200
    assert claim_res.json()["result"]["isError"] is False

    submit_res = secure_client.post(
        "/mcp/",
        headers=mcp_headers,
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "submit_task",
                "arguments": {"task_id": "TSK-01", "output": {"summary": "Completed via MCP"}},
            },
        },
    )
    assert submit_res.status_code == 200
    assert submit_res.json()["result"]["isError"] is False

    # Verify board DAG shows TSK-01 COMPLETED
    node_res = secure_client.get(f"/api/boards/{board_id}/nodes/TSK-01", headers=_auth(owner_jwt))
    assert node_res.status_code == 200
    assert node_res.json()["status"] == "COMPLETED"

    # Revoke key -> /mcp rejects immediately
    rev_res = secure_client.delete(
        f"/api/boards/{board_id}/agent-keys/{key_id}",
        headers=_auth(owner_jwt),
    )
    assert rev_res.status_code == 200

    blocked_mcp = secure_client.post(
        "/mcp/",
        headers=mcp_headers,
        json={"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}},
    )
    assert blocked_mcp.status_code == 401
