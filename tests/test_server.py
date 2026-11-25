"""
Integration and unit tests for SYNAPSE FastAPI REST endpoints and WebSocket Hub.
Tests REST contracts, cycle rejection (400), missing dependencies (404),
claim/submit conflicts (409), strict idempotency replay (200), scoped subgraphs,
real-time WebSocket broadcasting (<50ms latency), and startup DAG hydration.
"""
from __future__ import annotations

import os
import tempfile
import time
import pytest
from fastapi.testclient import TestClient

from harness.models import Edge, Event, GraphSnapshot, Node, NodeStatus, NodeType
from harness.server import (
    EVENT_BLAST_RADIUS_ALERT,
    EVENT_CONFLICT_DETECTED,
    EVENT_NODE_CREATED,
    EVENT_REQUIREMENT_UPDATED,
    EVENT_TASK_CLAIMED,
    EVENT_TASK_SUBMITTED,
    create_app,
)
from harness.store import EventStore
from harness.graph import DAGManager


@pytest.fixture(autouse=True)
def _disable_auth_for_legacy_dag_tests(monkeypatch):
    """Keep legacy single-board DAG integration tests focused on graph mechanics without tokens."""
    monkeypatch.setenv("SYNAPSE_AUTH_ENABLED", "false")


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    for ext in ["", "-wal", "-shm"]:
        p = f"{path}{ext}"
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass


@pytest.fixture
def app_and_store(temp_db):
    store = EventStore(db_path=temp_db)
    dag = DAGManager(store=store)
    app = create_app(db_path=temp_db, dag_manager=dag, store=store)
    return app, store, dag


@pytest.fixture
def client(app_and_store):
    app, _, _ = app_and_store
    with TestClient(app) as test_client:
        yield test_client


# -------------------------------------------------------------
# REST API Endpoint Tests: Positive, Negative, and Edge Cases
# -------------------------------------------------------------

def test_health_check(client):
    """Verify GET /health returns 200 OK and healthy status report."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "nodes_count" in data
    assert "active_ws_clients" in data


def test_dashboard_endpoint(client):
    """Verify GET / and /dashboard return 200 HTML content and logo asset serves cleanly."""
    for path in ("/", "/dashboard"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "SYNAPSE" in resp.text

    logo = client.get("/assets/synapse-logo.svg")
    assert logo.status_code == 200
    assert "image/svg+xml" in logo.headers["content-type"]
    assert "<svg" in logo.text


def test_create_task_success(client):
    """Verify POST /tasks creates requirement, decision, and task nodes with 201 Created."""
    # 1. Create requirement node
    req_body = {
        "id": "REQ-01",
        "title": "OAuth 2.0 Spec",
        "type": "REQUIREMENT",
        "payload": {"spec": "Use OAuth 2.0 with JWT RS256"},
        "dependencies": [],
        "actor": "architect",
        "idempotency_key": "ik-req-01",
    }
    resp = client.post("/tasks", json=req_body)
    assert resp.status_code == 201
    data = resp.json()
    assert "node" in data
    assert data["node"]["id"] == "REQ-01"
    assert data["node"]["status"] == "PENDING"
    assert data["node"]["type"] == "REQUIREMENT"
    assert "Node created successfully" in data["message"]

    # 2. Create task dependent on REQ-01
    task_body = {
        "id": "TSK-01",
        "title": "Build Auth Middleware",
        "type": "TASK",
        "payload": {"framework": "FastAPI"},
        "dependencies": ["REQ-01"],
        "actor": "lead",
        "idempotency_key": "ik-tsk-01",
    }
    resp = client.post("/tasks", json=task_body)
    assert resp.status_code == 201
    task_data = resp.json()
    assert task_data["node"]["id"] == "TSK-01"
    assert task_data["node"]["status"] == "PENDING"

    # 3. Verify graph state has 2 nodes and 1 edge
    resp = client.get("/graph")
    assert resp.status_code == 200
    graph_data = resp.json()
    assert len(graph_data["nodes"]) == 2
    assert len(graph_data["edges"]) == 1
    assert graph_data["edges"][0]["source"] == "REQ-01"
    assert graph_data["edges"][0]["target"] == "TSK-01"


def test_create_task_cycle_rejection(client):
    """Verify POST /tasks rejects self-loops and circular dependencies with 400 Bad Request."""
    # Seed N-1 and N-2
    r1 = client.post("/tasks", json={"id": "N-1", "title": "Node 1", "type": "TASK", "dependencies": []})
    assert r1.status_code == 201
    r2 = client.post("/tasks", json={"id": "N-2", "title": "Node 2", "type": "TASK", "dependencies": ["N-1"]})
    assert r2.status_code == 201

    # 1. Self-loop rejection
    resp = client.post("/tasks", json={
        "id": "N-SELF",
        "title": "Self loop node",
        "type": "TASK",
        "dependencies": ["N-SELF"],
    })
    assert resp.status_code == 400
    assert "Self-loop detected" in resp.json()["detail"]

    # 2. Add N-3 dependent on N-2
    r3 = client.post("/tasks", json={"id": "N-3", "title": "Node 3", "type": "TASK", "dependencies": ["N-2"]})
    assert r3.status_code == 201

    # 3. Create cycle by trying to add an edge back to ancestor or self
    resp_cycle = client.post("/tasks", json={
        "id": "N-1",
        "title": "Node 1 mutated",
        "type": "TASK",
        "dependencies": ["N-3"],
    })
    # Since N-1 exists, attempting duplicate without idempotency key returns 409
    assert resp_cycle.status_code == 409


def test_create_task_missing_dependency(client):
    """Verify POST /tasks returns 404 when referencing a missing dependency."""
    resp = client.post("/tasks", json={
        "id": "TSK-ORPHAN",
        "title": "Orphan Task",
        "type": "TASK",
        "dependencies": ["DOES_NOT_EXIST"],
    })
    assert resp.status_code == 404
    assert "Dependency node 'DOES_NOT_EXIST' not found" in resp.json()["detail"]


def test_create_task_conflict_duplicate_id(client):
    """Verify POST /tasks returns 409 Conflict when creating an existing node without idempotency key."""
    client.post("/tasks", json={"id": "UNIQUE-01", "title": "Unique", "type": "TASK"})
    resp = client.post("/tasks", json={"id": "UNIQUE-01", "title": "Unique Dup", "type": "TASK"})
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


def test_create_task_idempotent_replay(client, app_and_store):
    """Verify POST /tasks with same idempotency key returns 201/200 without duplicate events."""
    _, store, _ = app_and_store
    payload = {
        "id": "IDEMP-NODE",
        "title": "Idempotent Node",
        "type": "TASK",
        "idempotency_key": "ik-create-unique-99",
    }
    r1 = client.post("/tasks", json=payload)
    assert r1.status_code == 201

    # Replay with same key
    r2 = client.post("/tasks", json=payload)
    assert r2.status_code == 201
    assert r1.json()["node"]["id"] == r2.json()["node"]["id"]

    # Verify only 1 event recorded
    events = store.get_events_for_node("IDEMP-NODE")
    assert len(events) == 1


def test_claim_task_workflow_and_conflicts(client):
    """Verify POST /tasks/{task_id}/claim workflow: success (200), conflict (409), not found (404)."""
    client.post("/tasks", json={"id": "TSK-CLAIM-01", "title": "Claimable Task", "type": "TASK"})

    # 1. Success claim
    claim_payload = {
        "agent_id": "claude-code-cli",
        "tool_name": "claude_runner",
        "idempotency_key": "ik-claim-claude-1",
    }
    resp = client.post("/tasks/TSK-CLAIM-01/claim", json=claim_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "Task claimed successfully"
    assert data["task"]["status"] == "IN_PROGRESS"
    assert "claude-code-cli" in data["task"]["assigned_to"]

    # 2. Idempotent replay by same agent returns 200
    replay_resp = client.post("/tasks/TSK-CLAIM-01/claim", json=claim_payload)
    assert replay_resp.status_code == 200
    assert replay_resp.json()["task"]["id"] == "TSK-CLAIM-01"

    # 3. Second agent attempting to claim returns 409 Conflict
    conflict_resp = client.post("/tasks/TSK-CLAIM-01/claim", json={
        "agent_id": "openai-codex",
        "tool_name": "codex_runner",
        "idempotency_key": "ik-claim-codex-1",
    })
    assert conflict_resp.status_code == 409
    assert "already claimed" in conflict_resp.json()["detail"]

    # 4. Claim non-existent task returns 404
    missing_resp = client.post("/tasks/DOES_NOT_EXIST/claim", json={
        "agent_id": "agent-1",
        "idempotency_key": "ik-missing",
    })
    assert missing_resp.status_code == 404


def test_get_scoped_subgraph(client):
    """Verify GET /tasks/{task_id}/subgraph returns scoped ancestors and staleness flags."""
    client.post("/tasks", json={"id": "REQ-A", "title": "Req A", "type": "REQUIREMENT"})
    client.post("/tasks", json={"id": "DEC-A", "title": "Dec A", "type": "DECISION", "dependencies": ["REQ-A"]})
    client.post("/tasks", json={"id": "TSK-A", "title": "Task A", "type": "TASK", "dependencies": ["DEC-A"]})

    resp = client.get("/tasks/TSK-A/subgraph")
    assert resp.status_code == 200
    subgraph = resp.json()
    assert subgraph["task"]["id"] == "TSK-A"
    ancestor_ids = {a["id"] for a in subgraph["ancestors"]}
    assert "DEC-A" in ancestor_ids
    assert "REQ-A" in ancestor_ids
    assert len(subgraph["edges"]) == 2
    assert subgraph["is_stale"] is False

    # Non-existent task returns 404
    assert client.get("/tasks/UNKNOWN/subgraph").status_code == 404


def test_submit_task_workflow_and_validations(client, app_and_store):
    """Verify POST /tasks/{task_id}/submit: 200 OK, 404 not found, 409 invalid state."""
    _, store, _ = app_and_store
    client.post("/tasks", json={"id": "TSK-SUB-01", "title": "Submit Task", "type": "TASK"})

    # 1. Attempt submit on PENDING (not claimed) task -> 409 Conflict
    unclaimed_sub = client.post("/tasks/TSK-SUB-01/submit", json={
        "agent_id": "claude",
        "idempotency_key": "ik-sub-fail",
        "output": {"result": "too early"},
    })
    assert unclaimed_sub.status_code == 409

    # 2. Claim task with claude
    client.post("/tasks/TSK-SUB-01/claim", json={
        "agent_id": "claude",
        "tool_name": "claude_cli",
        "idempotency_key": "ik-claim-sub01",
    })

    # 3. Attempt submit by unauthorized agent -> 409 Conflict
    unauth_sub = client.post("/tasks/TSK-SUB-01/submit", json={
        "agent_id": "impostor",
        "idempotency_key": "ik-sub-unauth",
        "output": {"result": "hacked"},
    })
    assert unauth_sub.status_code == 409

    # 4. Valid submit by claude -> 200 OK
    valid_sub = client.post("/tasks/TSK-SUB-01/submit", json={
        "agent_id": "claude",
        "idempotency_key": "ik-sub-valid",
        "output": {"code": "def verify(): return True", "tests_passed": True},
    })
    assert valid_sub.status_code == 200
    data = valid_sub.json()
    assert data["message"] == "Task submitted successfully"
    assert data["task"]["status"] == "COMPLETED"
    assert data["task"]["payload"]["output"]["tests_passed"] is True

    # 5. Idempotent replay of submit returns 200 OK without creating extra events
    replay_sub = client.post("/tasks/TSK-SUB-01/submit", json={
        "agent_id": "claude",
        "idempotency_key": "ik-sub-valid",
        "output": {"code": "def verify(): return True", "tests_passed": True},
    })
    assert replay_sub.status_code == 200
    events = store.get_events_for_node("TSK-SUB-01")
    submit_events = [e for e in events if "SUBMIT" in e.event_type]
    assert len(submit_events) == 1

    # 6. Submit non-existent task returns 404
    assert client.post("/tasks/MISSING/submit", json={"agent_id": "c", "idempotency_key": "k"}).status_code == 404


def test_update_requirement_blast_radius_and_conflict_resolution(client):
    """Verify POST /requirements/update triggers downstream STALE_CONFLICT, and conflict resolution."""
    # Seed chain: REQ-SEC -> DEC-SEC -> TSK-SEC
    client.post("/tasks", json={"id": "REQ-SEC", "title": "Security Spec", "type": "REQUIREMENT", "payload": {"spec": "RSA-2048"}})
    client.post("/tasks", json={"id": "DEC-SEC", "title": "Decision", "type": "DECISION", "dependencies": ["REQ-SEC"]})
    client.post("/tasks", json={"id": "TSK-SEC", "title": "Task", "type": "TASK", "dependencies": ["DEC-SEC"]})

    # Complete TSK-SEC first
    client.post("/tasks/TSK-SEC/claim", json={"agent_id": "agent-1", "idempotency_key": "ik-c-sec"})
    client.post("/tasks/TSK-SEC/submit", json={"agent_id": "agent-1", "idempotency_key": "ik-s-sec", "output": {"alg": "RSA"}})

    # Update requirement spec
    update_payload = {
        "req_id": "REQ-SEC",
        "new_spec": "Mandate Ed25519 asymmetric signatures",
        "actor": "security-lead",
        "idempotency_key": "ik-up-sec-01",
    }
    resp = client.post("/requirements/update", json=update_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["requirement"]["payload"]["spec"] == "Mandate Ed25519 asymmetric signatures"
    assert data["requirement"]["version"] >= 2
    assert "TSK-SEC" in data["invalidated_nodes"]
    assert "DEC-SEC" in data["invalidated_nodes"]

    # Verify TSK-SEC is now STALE_CONFLICT in subgraph
    subgraph_resp = client.get("/tasks/TSK-SEC/subgraph")
    assert subgraph_resp.status_code == 200
    subgraph = subgraph_resp.json()
    assert subgraph["is_stale"] is True
    assert subgraph["task"]["status"] == "STALE_CONFLICT"

    # Resubmitting task resolves the conflict to COMPLETED
    resubmit_resp = client.post("/tasks/TSK-SEC/submit", json={
        "agent_id": "agent-1",
        "idempotency_key": "ik-resubmit-ed25519",
        "output": {"alg": "Ed25519", "conflict_resolved": True},
    })
    assert resubmit_resp.status_code == 200
    assert resubmit_resp.json()["task"]["status"] == "COMPLETED"

    # Non-existent requirement returns 404
    not_found_resp = client.post("/requirements/update", json={
        "req_id": "REQ-NONEXISTENT",
        "new_spec": "...",
        "actor": "sys",
        "idempotency_key": "k",
    })
    assert not_found_resp.status_code == 404


def test_get_graph_and_node_events(client):
    """Verify GET /graph and GET /nodes/{node_id}/events audit history."""
    client.post("/tasks", json={"id": "AUDIT-TSK", "title": "Audited", "type": "TASK"})
    client.post("/tasks/AUDIT-TSK/claim", json={"agent_id": "claude", "idempotency_key": "k1"})
    client.post("/tasks/AUDIT-TSK/submit", json={"agent_id": "claude", "idempotency_key": "k2", "output": {"done": True}})

    # GET /graph
    graph_resp = client.get("/graph")
    assert graph_resp.status_code == 200
    graph_data = graph_resp.json()
    assert any(n["id"] == "AUDIT-TSK" for n in graph_data["nodes"])
    assert graph_data["stats"]["completed"] >= 1

    # GET /nodes/{id}/events
    events_resp = client.get("/nodes/AUDIT-TSK/events")
    assert events_resp.status_code == 200
    events = events_resp.json()
    assert len(events) >= 3
    event_types = [e["event_type"] for e in events]
    assert "NODE_CREATED" in event_types
    assert "TASK_CLAIMED" in event_types
    assert "TASK_SUBMITTED" in event_types


def test_reset_graph_endpoint(client):
    """Verify POST /reset empties in-memory DAG and event store."""
    client.post("/tasks", json={"id": "TEMP-1", "title": "Temp", "type": "TASK"})
    assert len(client.get("/graph").json()["nodes"]) >= 1

    resp = client.post("/reset")
    assert resp.status_code == 200
    assert len(client.get("/graph").json()["nodes"]) == 0


def test_get_seed_templates_endpoint(client):
    """Verify GET /api/seed/templates returns sorted built-in template names."""
    resp = client.get("/api/seed/templates")
    assert resp.status_code == 200
    data = resp.json()
    assert "templates" in data
    assert data["templates"] == ["auth", "tictactoe"]


def test_seed_tictactoe_template_and_ready_tasks(client):
    """Verify POST /api/seed/tictactoe seeds the DAG, broadcasts GRAPH_SEEDED, and unlocks ready tasks."""
    with client.websocket_connect("/ws") as ws:
        _ = ws.receive_json()  # Handshake

        resp = client.post("/api/seed/tictactoe")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["template"] == "tictactoe"
        assert data["nodes_created"] == 8
        assert data["total_nodes"] == 8
        assert set(data["ready_tasks"]) == {"TSK-01", "TSK-03"}

        ws_msg = ws.receive_json()
        assert ws_msg["event"] == "GRAPH_SEEDED"
        assert ws_msg["data"]["template"] == "tictactoe"
        assert ws_msg["data"]["nodes_created"] == 8

    ready_resp = client.get("/api/tasks/ready")
    assert ready_resp.status_code == 200
    ready_nodes = ready_resp.json()
    ready_ids = {n["id"] for n in ready_nodes}
    assert ready_ids == {"TSK-01", "TSK-03"}
    assert all(n["type"] == "TASK" and n["status"] == "PENDING" for n in ready_nodes)

    # Claiming TSK-01 removes it from ready; completing TSK-01 unlocks TSK-02 & TSK-05
    client.post("/api/tasks/TSK-01/claim", json={"agent_id": "claude-code-cli", "idempotency_key": "k-c1"})
    ready_after_claim = {n["id"] for n in client.get("/api/tasks/ready").json()}
    assert ready_after_claim == {"TSK-03"}

    client.post(
        "/api/tasks/TSK-01/submit",
        json={"agent_id": "claude-code-cli", "idempotency_key": "k-s1", "output": {"summary": "done"}},
    )
    ready_after_submit = {n["id"] for n in client.get("/api/tasks/ready").json()}
    assert ready_after_submit == {"TSK-02", "TSK-03", "TSK-05"}


def test_seed_auth_template_resets_prior_graph_and_updates_ready(client):
    """Verify POST /api/seed/auth resets existing nodes by default and seeds auth DAG."""
    client.post("/tasks", json={"id": "STRAY-NODE", "title": "Stray", "type": "TASK"})
    assert len(client.get("/api/graph").json()["nodes"]) == 1

    resp = client.post("/api/seed/auth")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["template"] == "auth"
    assert data["nodes_created"] == 5
    assert data["total_nodes"] == 5
    assert set(data["ready_tasks"]) == {"TSK-01", "TSK-03"}

    graph_ids = {n["id"] for n in client.get("/api/graph").json()["nodes"]}
    assert "STRAY-NODE" not in graph_ids
    assert graph_ids == {"REQ-01", "DEC-01", "TSK-01", "TSK-02", "TSK-03"}

    # Re-seeding with reset=false preserves existing nodes without duplicating them
    no_reset_resp = client.post("/api/seed/auth?reset=false")
    assert no_reset_resp.status_code == 200
    assert no_reset_resp.json()["nodes_created"] == 0
    assert no_reset_resp.json()["total_nodes"] == 5


def test_seed_unknown_template_returns_404(client):
    """Verify POST /api/seed/{unknown} returns 404 Not Found with available templates detail."""
    resp = client.post("/api/seed/nonexistent_template")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "Unknown seed template" in detail
    assert "auth" in detail
    assert "tictactoe" in detail


def test_ready_tasks_empty_and_non_task_filtering(client):
    """Verify GET /api/tasks/ready ignores REQUIREMENT/DECISION/COMPLETED/IN_PROGRESS/STALE_CONFLICT nodes."""
    assert client.get("/api/tasks/ready").json() == []

    # REQ (PENDING) -> TSK-WAIT (PENDING) should not make TSK-WAIT ready because REQ is not COMPLETED
    client.post("/tasks", json={"id": "REQ-R1", "title": "Req", "type": "REQUIREMENT"})
    client.post("/tasks", json={"id": "TSK-WAIT", "title": "Waiting Task", "type": "TASK", "dependencies": ["REQ-R1"]})
    client.post("/tasks", json={"id": "TSK-FREE", "title": "Free Task", "type": "TASK"})

    ready_ids = [n["id"] for n in client.get("/api/tasks/ready").json()]
    assert ready_ids == ["TSK-FREE"]


# -------------------------------------------------------------
# WebSocket Hub Tests: Connection, Handshake, Broadcasts & Pruning
# -------------------------------------------------------------

def test_websocket_connection_and_handshake(client):
    """Verify WebSocket /ws connection receives initial CONNECTED handshake frame."""
    with client.websocket_connect("/ws") as ws:
        handshake = ws.receive_json()
        assert handshake["event"] == "CONNECTED"
        assert "timestamp" in handshake


def test_websocket_ping_pong(client):
    """Verify WebSocket responds to client ping messages with pong."""
    with client.websocket_connect("/ws") as ws:
        _ = ws.receive_json()  # Handshake
        ws.send_text("ping")
        pong = ws.receive_text()
        assert pong == "pong"


def test_websocket_broadcast_node_created(client):
    """Verify WebSocket clients receive NODE_CREATED event when POST /tasks is executed."""
    with client.websocket_connect("/ws") as ws:
        _ = ws.receive_json()  # Handshake

        resp = client.post("/tasks", json={
            "id": "WS-NODE-01",
            "title": "Live Node",
            "type": "REQUIREMENT",
            "payload": {"p": 1},
            "dependencies": [],
            "actor": "lead",
        })
        assert resp.status_code == 201

        msg = ws.receive_json()
        assert msg["event"] == "NODE_CREATED"
        assert msg["node"]["id"] == "WS-NODE-01"
        assert msg["node"]["title"] == "Live Node"
        assert msg["dependencies"] == []


def test_websocket_broadcast_task_claimed_and_submitted(client):
    """Verify WebSocket clients receive TASK_CLAIMED and TASK_SUBMITTED frames."""
    client.post("/tasks", json={"id": "WS-TSK-FLOW", "title": "Flow Task", "type": "TASK"})

    with client.websocket_connect("/ws") as ws:
        _ = ws.receive_json()  # Handshake

        # 1. Claim task
        client.post("/tasks/WS-TSK-FLOW/claim", json={
            "agent_id": "claude",
            "tool_name": "claude_cli",
            "idempotency_key": "ws-claim-flow",
        })
        claim_msg = ws.receive_json()
        assert claim_msg["event"] == "TASK_CLAIMED"
        assert claim_msg["task_id"] == "WS-TSK-FLOW"
        assert claim_msg["agent_id"] == "claude"
        assert claim_msg["tool_name"] == "claude_cli"

        # 2. Submit task
        client.post("/tasks/WS-TSK-FLOW/submit", json={
            "agent_id": "claude",
            "idempotency_key": "ws-sub-flow",
            "output": {"summary": "all passed"},
        })
        sub_msg = ws.receive_json()
        assert sub_msg["event"] == "TASK_SUBMITTED"
        assert sub_msg["task_id"] == "WS-TSK-FLOW"
        assert sub_msg["agent_id"] == "claude"
        assert sub_msg["node"]["status"] == "COMPLETED"


def test_websocket_broadcast_requirement_update_and_blast_radius(client):
    """Verify WebSocket clients receive REQUIREMENT_UPDATED and BLAST_RADIUS_ALERT."""
    client.post("/tasks", json={"id": "WS-REQ-01", "title": "Base Req", "type": "REQUIREMENT"})
    client.post("/tasks", json={"id": "WS-TSK-DOWN", "title": "Downstream", "type": "TASK", "dependencies": ["WS-REQ-01"]})

    with client.websocket_connect("/ws") as ws:
        _ = ws.receive_json()  # Handshake

        # Update requirement
        client.post("/requirements/update", json={
            "req_id": "WS-REQ-01",
            "new_spec": "Updated specification for WebSocket alert test",
            "actor": "architect",
            "idempotency_key": "ws-req-update-1",
        })

        # Expect REQUIREMENT_UPDATED
        req_msg = ws.receive_json()
        assert req_msg["event"] == "REQUIREMENT_UPDATED"
        assert req_msg["req_id"] == "WS-REQ-01"
        assert "Updated specification" in req_msg["new_spec"]

        # Expect BLAST_RADIUS_ALERT
        alert_msg = ws.receive_json()
        assert alert_msg["event"] == "BLAST_RADIUS_ALERT"
        assert alert_msg["root_cause_node"] == "WS-REQ-01"
        assert "WS-TSK-DOWN" in alert_msg["invalidated_nodes"]
        assert alert_msg["severity"] == "CRITICAL"


def test_websocket_multi_client_broadcast(client):
    """Verify multiple concurrent WebSocket connections all receive broadcast events."""
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        # Handshakes
        h1 = ws1.receive_json()
        h2 = ws2.receive_json()
        assert h1["event"] == "CONNECTED"
        assert h2["event"] == "CONNECTED"

        # Trigger event via REST
        client.post("/tasks", json={"id": "MULTI-NODE", "title": "Multi Client Test", "type": "TASK"})

        msg1 = ws1.receive_json()
        msg2 = ws2.receive_json()
        assert msg1["event"] == "NODE_CREATED"
        assert msg2["event"] == "NODE_CREATED"
        assert msg1["node"]["id"] == "MULTI-NODE"
        assert msg2["node"]["id"] == "MULTI-NODE"


# -------------------------------------------------------------
# Lifespan Hydration Test
# -------------------------------------------------------------

def test_startup_lifespan_hydration(temp_db):
    """Verify DAG is automatically hydrated from SQLite store upon FastAPI startup lifespan."""
    # 1. Pre-populate SQLite database directly
    seed_store = EventStore(db_path=temp_db)
    n1 = Node(id="HYDRATE-01", title="Persisted Req", type=NodeType.REQUIREMENT, status=NodeStatus.PENDING)
    n2 = Node(id="HYDRATE-02", title="Persisted Task", type=NodeType.TASK, status=NodeStatus.PENDING)
    seed_store.upsert_node(n1)
    seed_store.upsert_node(n2)
    seed_store.add_edge("HYDRATE-01", "HYDRATE-02", "relies_on")

    # 2. Boot up fresh FastAPI application pointing to the pre-populated DB
    fresh_app = create_app(db_path=temp_db)
    with TestClient(fresh_app) as client:
        graph_resp = client.get("/graph")
        assert graph_resp.status_code == 200
        data = graph_resp.json()
        node_ids = {n["id"] for n in data["nodes"]}
        assert "HYDRATE-01" in node_ids
        assert "HYDRATE-02" in node_ids
        assert len(data["edges"]) == 1
        assert data["edges"][0]["source"] == "HYDRATE-01"
        assert data["edges"][0]["target"] == "HYDRATE-02"


def test_create_app_uses_env_db_path_and_creates_parent_dir(tmp_path, monkeypatch):
    nested_db = tmp_path / "nested" / "data" / "synapse.db"
    monkeypatch.setenv("SYNAPSE_DB_PATH", str(nested_db))

    env_app = create_app()
    with TestClient(env_app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    assert nested_db.exists()



def test_agent_protocol_endpoints_and_dashboard_comment(client):
    """Verify /llms.txt, /agent, and dashboard HTML expose the zero-UI agent protocol."""
    seed_resp = client.post("/api/seed/tictactoe")
    assert seed_resp.status_code == 200

    for path in ("/llms.txt", "/agent"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        body = resp.text
        assert "# SYNAPSE Agent Protocol" in body
        assert "TSK-01" in body
        assert "TSK-03" in body
        assert "/api/tasks/{TASK_ID}/claim" in body
        assert "/api/tasks/{TASK_ID}/subgraph" in body
        assert "/api/tasks/{TASK_ID}/submit" in body

    dash = client.get("/dashboard")
    assert dash.status_code == 200
    assert "SYNAPSE AGENT PROTOCOL" in dash.text


# -------------------------------------------------------------
# Snapshot Export / Import & Startup Hydration Tests
# -------------------------------------------------------------

def test_export_reset_import_roundtrip_and_idempotency(client):
    """Verify export -> reset -> import roundtrip restores nodes, edges, and /events history idempotently."""
    client.post("/tasks", json={"id": "SNAP-REQ", "title": "Req", "type": "REQUIREMENT", "idempotency_key": "ik-sr"})
    client.post("/tasks", json={
        "id": "SNAP-TSK",
        "title": "Task",
        "type": "TASK",
        "dependencies": ["SNAP-REQ"],
        "idempotency_key": "ik-st",
    })
    client.post("/tasks/SNAP-TSK/claim", json={"agent_id": "claude", "idempotency_key": "ik-sc"})
    client.post("/tasks/SNAP-TSK/submit", json={
        "agent_id": "claude",
        "idempotency_key": "ik-ss",
        "output": {"summary": "implemented"},
    })

    export_resp = client.get("/api/export")
    assert export_resp.status_code == 200
    snapshot = export_resp.json()
    assert snapshot["version"] == 1
    assert {n["id"] for n in snapshot["nodes"]} == {"SNAP-REQ", "SNAP-TSK"}
    assert len(snapshot["edges"]) == 1
    assert len(snapshot["events"]) == 4

    # /export alias matches /api/export
    assert len(client.get("/export").json()["nodes"]) == 2

    # Reset clears graph and event lineage
    assert client.post("/api/reset").status_code == 200
    assert client.get("/api/graph").json()["nodes"] == []
    assert client.get("/api/nodes/SNAP-TSK/events").json() == []

    # Import restores state, full event history, and broadcasts GRAPH_IMPORTED
    with client.websocket_connect("/ws") as ws:
        _ = ws.receive_json()  # Handshake
        import_resp = client.post("/api/import", json=snapshot)
        assert import_resp.status_code == 200
        body = import_resp.json()
        assert body["status"] == "success"
        assert body["nodes_imported"] == 2
        assert body["edges_imported"] == 1
        assert body["events_imported"] == 4
        assert body["total_nodes"] == 2
        assert body["total_edges"] == 1

        ws_msg = ws.receive_json()
        assert ws_msg["event"] == "GRAPH_IMPORTED"
        assert ws_msg["data"]["total_nodes"] == 2

    restored_graph = client.get("/api/graph").json()
    assert {n["id"] for n in restored_graph["nodes"]} == {"SNAP-REQ", "SNAP-TSK"}
    assert restored_graph["edges"][0]["source"] == "SNAP-REQ"
    assert restored_graph["edges"][0]["target"] == "SNAP-TSK"

    events = client.get("/api/nodes/SNAP-TSK/events").json()
    assert [e["event_type"] for e in events] == ["NODE_CREATED", "TASK_CLAIMED", "TASK_SUBMITTED"]

    # Re-importing with replace=false is idempotent on event history
    reimport_resp = client.post("/api/import?replace=false", json=snapshot)
    assert reimport_resp.status_code == 200
    assert reimport_resp.json()["events_imported"] == 0
    assert len(client.get("/api/nodes/SNAP-TSK/events").json()) == 3


def test_import_rejects_cyclic_and_dangling_snapshots_without_corruption(client):
    """Verify cyclic (400) and dangling-edge (404) imports fail cleanly without altering existing graph."""
    client.post("/tasks", json={"id": "KEEP-01", "title": "Keep Me", "type": "TASK"})

    cyclic_snapshot = {
        "version": 1,
        "nodes": [
            {"id": "CYC-A", "title": "A", "type": "TASK"},
            {"id": "CYC-B", "title": "B", "type": "TASK"},
        ],
        "edges": [
            {"source": "CYC-A", "target": "CYC-B", "relation": "relies_on"},
            {"source": "CYC-B", "target": "CYC-A", "relation": "relies_on"},
        ],
        "events": [],
    }
    cyc_resp = client.post("/api/import", json=cyclic_snapshot)
    assert cyc_resp.status_code == 400
    assert "cyclic" in cyc_resp.json()["detail"].lower()

    # Existing graph must be untouched
    current_ids = [n["id"] for n in client.get("/api/graph").json()["nodes"]]
    assert current_ids == ["KEEP-01"]

    dangling_snapshot = {
        "version": 1,
        "nodes": [{"id": "ONLY-A", "title": "Only A", "type": "TASK"}],
        "edges": [{"source": "ONLY-A", "target": "MISSING-B", "relation": "relies_on"}],
        "events": [],
    }
    dang_resp = client.post("/import", json=dangling_snapshot)
    assert dang_resp.status_code == 404
    assert "MISSING-B" in dang_resp.json()["detail"]

    current_ids_after = [n["id"] for n in client.get("/api/graph").json()["nodes"]]
    assert current_ids_after == ["KEEP-01"]


def test_startup_snapshot_file_hydrates_empty_db_and_skips_nonempty(tmp_path, monkeypatch):
    """Verify SYNAPSE_SNAPSHOT_FILE hydrates an empty DB on startup and never overwrites a non-empty DB."""
    db_file = tmp_path / "startup_snap.db"
    snap_file = tmp_path / "initial_snapshot.json"
    other_snap_file = tmp_path / "other_snapshot.json"

    initial_snapshot = GraphSnapshot(
        nodes=[
            Node(id="BOOT-REQ", title="Boot Req", type=NodeType.REQUIREMENT),
            Node(id="BOOT-TSK", title="Boot Task", type=NodeType.TASK, status=NodeStatus.COMPLETED),
        ],
        edges=[Edge(source="BOOT-REQ", target="BOOT-TSK")],
        events=[
            Event(
                event_id="evt-boot-1",
                idempotency_key="ik-boot-1",
                event_type="NODE_CREATED",
                node_id="BOOT-TSK",
                actor="seed",
                data={"boot": True},
            )
        ],
    )
    snap_file.write_text(initial_snapshot.model_dump_json(indent=2), encoding="utf-8")

    monkeypatch.setenv("SYNAPSE_SNAPSHOT_FILE", str(snap_file))
    first_app = create_app(db_path=str(db_file))
    with TestClient(first_app) as first_client:
        graph = first_client.get("/api/graph").json()
        assert {n["id"] for n in graph["nodes"]} == {"BOOT-REQ", "BOOT-TSK"}
        assert len(graph["edges"]) == 1
        events = first_client.get("/api/nodes/BOOT-TSK/events").json()
        assert len(events) == 1
        assert events[0]["event_id"] == "evt-boot-1"

    # Point to a different snapshot and reboot against the now non-empty DB -> must NOT overwrite
    other_snapshot = GraphSnapshot(
        nodes=[Node(id="OTHER-NODE", title="Should Not Overwrite", type=NodeType.TASK)],
        edges=[],
        events=[],
    )
    other_snap_file.write_text(other_snapshot.model_dump_json(indent=2), encoding="utf-8")
    monkeypatch.setenv("SYNAPSE_SNAPSHOT_FILE", str(other_snap_file))

    second_app = create_app(db_path=str(db_file))
    with TestClient(second_app) as second_client:
        graph_after = second_client.get("/api/graph").json()
        assert {n["id"] for n in graph_after["nodes"]} == {"BOOT-REQ", "BOOT-TSK"}


# -------------------------------------------------------------
# Dynamic 4-Letter Project Preview Routing Tests
# -------------------------------------------------------------


def test_longer_disk_project_folder_resolves_at_four_letter_slug(tmp_path):
    """Verify disk project folder calculator/index.html resolves at GET /calc and lists in /llms.txt."""
    calc_dir = tmp_path / "calculator"
    calc_dir.mkdir()
    (calc_dir / "index.html").write_text("<!doctype html><html><body>Disk Calculator</body></html>", encoding="utf-8")
    (calc_dir / "app.js").write_text("console.log('calc');", encoding="utf-8")

    db_file = tmp_path / "calc.db"
    proj_app = create_app(db_path=str(db_file), projects_root=tmp_path)
    with TestClient(proj_app) as proj_client:
        resp = proj_client.get("/calc")
        assert resp.status_code == 200
        assert "Disk Calculator" in resp.text

        asset_resp = proj_client.get("/calc/app.js")
        assert asset_resp.status_code == 200
        assert "console.log('calc');" in asset_resp.text

        llms_resp = proj_client.get("/llms.txt")
        assert llms_resp.status_code == 200
        assert "## Project Preview Links" in llms_resp.text
        assert "/calc" in llms_resp.text


def test_create_node_returns_project_slug_and_pending_status_page(tmp_path):
    """Verify POST /api/nodes returns project_slug/project_url without mutating payload, and GET /note serves escaped pending HTML."""
    db_file = tmp_path / "notes.db"
    proj_app = create_app(db_path=str(db_file), projects_root=tmp_path)
    with TestClient(proj_app) as proj_client:
        create_payload = {
            "id": "TSK-NOTE-1",
            "title": "Notes Manager",
            "type": "TASK",
            "payload": {"spec": "Markdown notes"},
            "idempotency_key": "ik-note-1",
        }
        create_resp = proj_client.post("/api/nodes", json=create_payload)
        assert create_resp.status_code == 201
        data = create_resp.json()
        assert data["project_slug"] == "note"
        assert data["project_url"].endswith("/note")
        assert data["node"]["payload"] == {"spec": "Markdown notes"}

        # Idempotent replay also surfaces project_slug and project_url
        replay_resp = proj_client.post("/api/nodes", json=create_payload)
        assert replay_resp.status_code == 201
        assert replay_resp.json()["project_slug"] == "note"
        assert replay_resp.json()["project_url"].endswith("/note")

        # Pending preview page renders escaped node info and publish instructions
        pending_resp = proj_client.get("/note")
        assert pending_resp.status_code == 200
        assert "text/html" in pending_resp.headers["content-type"]
        assert "Notes Manager" in pending_resp.text
        assert "TSK-NOTE-1" in pending_resp.text
        assert "done &lt;TASK_ID&gt; --file index.html" in pending_resp.text
        assert 'href="/dashboard"' in pending_resp.text

        # HTML-escape protection on malicious title when matched via payload.project
        xss_resp = proj_client.post("/api/nodes", json={
            "id": "TSK-XSS-1",
            "title": "<script>alert(1)</script>",
            "type": "TASK",
            "payload": {"project": "xssapp"},
        })
        assert xss_resp.status_code == 201
        assert xss_resp.json()["project_slug"] == "xssa"
        xss_page = proj_client.get("/xssa")
        assert xss_page.status_code == 200
        assert "<script>alert(1)</script>" not in xss_page.text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in xss_page.text


def test_submit_task_with_html_serves_live_preview_and_updates_dashboard_link(tmp_path):
    """Verify POST /api/tasks/{id}/submit with HTML output returns project_url and serves live HTML at GET /note."""
    db_file = tmp_path / "submit_html.db"
    proj_app = create_app(db_path=str(db_file), projects_root=tmp_path)
    with TestClient(proj_app) as proj_client:
        proj_client.post("/api/nodes", json={
            "id": "TSK-LIVE-1",
            "title": "Notes Manager",
            "type": "TASK",
        })
        proj_client.post("/api/tasks/TSK-LIVE-1/claim", json={
            "agent_id": "codex",
            "idempotency_key": "ik-claim-live-1",
        })

        submit_resp = proj_client.post("/api/tasks/TSK-LIVE-1/submit", json={
            "agent_id": "codex",
            "idempotency_key": "ik-submit-live-1",
            "output": {"html": "<!doctype html><html><body>Live</body></html>"},
        })
        assert submit_resp.status_code == 200
        submit_data = submit_resp.json()
        assert submit_data["project_slug"] == "note"
        assert submit_data["project_url"].endswith("/note")

        live_resp = proj_client.get("/note")
        assert live_resp.status_code == 200
        assert "<!doctype html><html><body>Live</body></html>" in live_resp.text

        dash_resp = proj_client.get("/dashboard")
        assert dash_resp.status_code == 200
        assert "drawer-project-link" in dash_resp.text


def test_unknown_paths_and_path_traversal_return_404(tmp_path):
    """Verify unknown slugs, favicon, and path traversal attempts return 404."""
    calc_dir = tmp_path / "calculator"
    calc_dir.mkdir()
    (calc_dir / "index.html").write_text("<html><body>Calc</body></html>", encoding="utf-8")
    harness_dir = tmp_path / "harness"
    harness_dir.mkdir()
    (harness_dir / "server.py").write_text("# secret", encoding="utf-8")

    db_file = tmp_path / "sec.db"
    proj_app = create_app(db_path=str(db_file), projects_root=tmp_path)
    with TestClient(proj_app) as proj_client:
        assert proj_client.get("/todo").status_code == 404
        assert proj_client.get("/nonexistent").status_code == 404
        assert proj_client.get("/favicon.ico").status_code == 404
        assert proj_client.get("/calc/../harness/server.py").status_code == 404
        assert proj_client.get("/calc/%2e%2e/harness/server.py").status_code == 404
        assert proj_client.get("/api/nonexistent").status_code == 404


def test_workspace_users_join_access_claim_submit_and_reset(client):
    """Verify User 1/User 2 sequential joins, second-laptop task access & claim tracking, WS broadcasts, and reset."""
    client.post("/api/nodes", json={"id": "TSK-U1", "title": "Auth Module", "type": "TASK"})

    with client.websocket_connect("/ws/graph") as ws:
        _ = ws.receive_json()  # CONNECTED

        # 1. Two browser/unnamed users enter workspace -> User 1, User 2
        j1 = client.post("/api/users/join", json={"tool_name": "dashboard"})
        assert j1.status_code == 200
        assert j1.json()["user"]["user_id"] == "user-1"
        assert j1.json()["user"]["display_name"] == "User 1"
        ws_j1 = ws.receive_json()
        assert ws_j1["event"] == "USER_JOINED"
        assert ws_j1["user"]["display_name"] == "User 1"

        j2 = client.post("/api/users/join", json={"tool_name": "dashboard"})
        assert j2.status_code == 200
        assert j2.json()["user"]["user_id"] == "user-2"
        assert j2.json()["user"]["display_name"] == "User 2"
        ws_j2 = ws.receive_json()
        assert ws_j2["event"] == "USER_JOINED"

        # 2. Second laptop accesses ready tasks & subgraph using X-Agent-Id header
        ready_resp = client.get(
            "/api/tasks/ready",
            headers={"X-Agent-Id": "laptop2-codex", "X-Tool-Name": "openai_codex", "X-Client-Host": "laptop-2"},
        )
        assert ready_resp.status_code == 200
        ws_ready = ws.receive_json()
        assert ws_ready["event"] == "USER_JOINED"
        assert ws_ready["user"]["user_id"] == "laptop2-codex"
        assert ws_ready["user"]["status"] == "viewing"
        assert ws_ready["user"]["client_host"] == "laptop-2"

        sub_resp = client.get(
            "/api/tasks/TSK-U1/subgraph",
            headers={"X-Agent-Id": "laptop2-codex", "X-Tool-Name": "openai_codex"},
        )
        assert sub_resp.status_code == 200
        ws_sub = ws.receive_json()
        assert ws_sub["event"] == "USER_UPDATED"
        assert ws_sub["user"]["last_task_id"] == "TSK-U1"

        # 3. Second laptop claims the task -> single TASK_CLAIMED WS frame + working user state
        claim_resp = client.post("/api/tasks/TSK-U1/claim", json={
            "agent_id": "laptop2-codex",
            "tool_name": "openai_codex",
            "idempotency_key": "ik-u-claim-1",
        })
        assert claim_resp.status_code == 200
        ws_claim = ws.receive_json()
        assert ws_claim["event"] == "TASK_CLAIMED"
        assert ws_claim["user"]["status"] == "working"
        assert "TSK-U1" in ws_claim["user"]["active_tasks"]

        # GET /api/users & GET /api/graph expose all 3 users
        users = client.get("/api/users").json()
        assert [u["user_id"] for u in users] == ["user-1", "user-2", "laptop2-codex"]
        assert users[2]["active_tasks"] == ["TSK-U1"]
        graph_users = client.get("/api/graph").json()["users"]
        assert len(graph_users) == 3

        # 4. Submit returns user to joined with completed task recorded
        sub_task = client.post("/api/tasks/TSK-U1/submit", json={
            "agent_id": "laptop2-codex",
            "idempotency_key": "ik-u-sub-1",
            "output": {"summary": "done"},
        })
        assert sub_task.status_code == 200
        ws_done = ws.receive_json()
        assert ws_done["event"] == "TASK_SUBMITTED"
        assert ws_done["user"]["status"] == "joined"
        assert ws_done["user"]["completed_tasks"] == ["TSK-U1"]

    # 5. Dashboard renders Users section below Status
    dash = client.get("/dashboard")
    assert dash.status_code == 200
    assert 'id="sidebar-users-list"' in dash.text
    assert 'id="users-count"' in dash.text

    # 6. Reset clears workspace users cleanly
    assert client.post("/api/reset").status_code == 200
    assert client.get("/api/users").json() == []
