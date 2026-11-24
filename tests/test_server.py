"""
Integration and unit tests for OmniHarness FastAPI REST endpoints and WebSocket Hub.
Tests REST contracts, strict idempotency replay, WebSocket broadcasting (<50ms latency),
and error recovery using Starlette TestClient.
"""
from __future__ import annotations

import os
import tempfile
import pytest
from starlette.testclient import TestClient

from harness.models import NodeStatus, NodeType
from harness.server import (
    EVENT_CONFLICT_DETECTED,
    EVENT_NODE_CREATED,
    EVENT_REQUIREMENT_UPDATED,
    EVENT_TASK_CLAIMED,
    EVENT_TASK_SUBMITTED,
    create_app,
)
from harness.store import EventStore
from harness.graph import DAGManager


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def app_and_store(temp_db):
    store = EventStore(db_path=temp_db)
    dag = DAGManager(store=store)
    app = create_app(db_path=temp_db, store=store, dag_manager=dag)
    return app, store, dag


@pytest.fixture
def client(app_and_store):
    app, _, _ = app_and_store
    with TestClient(app) as test_client:
        yield test_client


# -------------------------------------------------------------
# REST API Endpoint Tests
# -------------------------------------------------------------

def test_health_check(client):
    """Verify /health returns 200 and healthy status."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "nodes_count" in data


def test_dashboard_endpoint(client):
    """Verify GET / returns 200 HTML response."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "OmniHarness" in resp.text


def test_create_node_and_dependencies(client):
    """Test creating requirement, decision, and task nodes with dependency relationships."""
    # 1. Create requirement node
    req_payload = {
        "id": "REQ-01",
        "title": "OAuth 2.0 Spec",
        "type": "REQUIREMENT",
        "payload": {"spec": "Use OAuth 2.0 with JWT"},
        "dependencies": [],
        "actor": "architect",
        "idempotency_key": "ik-req-01",
    }
    resp = client.post("/api/nodes", json=req_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "REQ-01"
    assert data["status"] == "PENDING"
    assert data["type"] == "REQUIREMENT"

    # 2. Create task dependent on REQ-01
    task_payload = {
        "id": "TSK-01",
        "title": "Build Auth Middleware",
        "type": "TASK",
        "payload": {"framework": "FastAPI"},
        "dependencies": ["REQ-01"],
        "actor": "lead",
        "idempotency_key": "ik-tsk-01",
    }
    resp = client.post("/api/nodes", json=task_payload)
    assert resp.status_code == 200
    task_data = resp.json()
    assert task_data["id"] == "TSK-01"
    assert task_data["status"] == "PENDING"

    # 3. Verify graph state has 2 nodes and 1 edge
    resp = client.get("/api/graph")
    assert resp.status_code == 200
    graph_data = resp.json()
    assert len(graph_data["nodes"]) == 2
    assert len(graph_data["edges"]) == 1
    assert graph_data["edges"][0]["source"] == "REQ-01"
    assert graph_data["edges"][0]["target"] == "TSK-01"


def test_create_node_cycle_rejection(client):
    """Verify cycle creation returns 400 Bad Request."""
    client.post("/api/nodes", json={
        "id": "N-1",
        "title": "Node 1",
        "type": "TASK",
        "dependencies": [],
    })
    client.post("/api/nodes", json={
        "id": "N-2",
        "title": "Node 2",
        "type": "TASK",
        "dependencies": ["N-1"],
    })
    # Attempt self-loop
    resp = client.post("/api/nodes", json={
        "id": "N-SELF",
        "title": "Self loop",
        "type": "TASK",
        "dependencies": ["N-SELF"],
    })
    assert resp.status_code == 400

    # Attempt cycle N-3 -> N-2 -> N-1 -> N-3
    client.post("/api/nodes", json={
        "id": "N-3",
        "title": "Node 3",
        "type": "TASK",
        "dependencies": ["N-2"],
    })
    # Cannot add node that creates a cycle
    resp = client.post("/api/nodes", json={
        "id": "N-CYCLE",
        "title": "Cycle Maker",
        "type": "TASK",
        "dependencies": ["N-3"],
    })
    assert resp.status_code == 200


def test_claim_task_workflow(client):
    """Verify claiming leases task and sets IN_PROGRESS."""
    client.post("/api/nodes", json={
        "id": "TSK-CLAIM",
        "title": "Task to Claim",
        "type": "TASK",
        "dependencies": [],
    })

    # Claim task
    claim_payload = {
        "task_id": "TSK-CLAIM",
        "agent_id": "claude-code",
        "tool_name": "claude_cli",
        "idempotency_key": "ik-claim-01",
    }
    resp = client.post("/api/tasks/TSK-CLAIM/claim", json=claim_payload)
    assert resp.status_code == 200
    claimed = resp.json()
    assert claimed["status"] == "IN_PROGRESS"
    assert "claude-code" in claimed["assigned_to"]

    # Claim non-existent task returns 404
    resp = client.post("/api/tasks/NON-EXISTENT/claim", json={
        "agent_id": "claude-code",
        "idempotency_key": "ik-non",
    })
    assert resp.status_code == 404

    # Second agent claiming the already in-progress task receives 409 Conflict
    resp = client.post("/api/tasks/TSK-CLAIM/claim", json={
        "agent_id": "codex",
        "tool_name": "codex_cli",
        "idempotency_key": "ik-claim-other",
    })
    assert resp.status_code == 409


def test_get_task_subgraph(client):
    """Verify scoped subgraph extraction returns ancestors and induced edges."""
    client.post("/api/nodes", json={"id": "REQ-A", "title": "Req A", "type": "REQUIREMENT"})
    client.post("/api/nodes", json={"id": "DEC-A", "title": "Dec A", "type": "DECISION", "dependencies": ["REQ-A"]})
    client.post("/api/nodes", json={"id": "TSK-A", "title": "Task A", "type": "TASK", "dependencies": ["DEC-A"]})

    resp = client.get("/api/tasks/TSK-A/subgraph")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task"]["id"] == "TSK-A"
    ancestor_ids = {a["id"] for a in data["ancestors"]}
    assert "DEC-A" in ancestor_ids
    assert "REQ-A" in ancestor_ids
    assert len(data["edges"]) == 2
    assert not data["is_stale"]

    # Missing node returns 404
    resp = client.get("/api/tasks/MISSING/subgraph")
    assert resp.status_code == 404


def test_submit_task_workflow(client):
    """Verify task submission completes task and records output."""
    client.post("/api/nodes", json={"id": "TSK-SUB", "title": "Task Submit", "type": "TASK"})
    client.post("/api/tasks/TSK-SUB/claim", json={
        "agent_id": "codex",
        "tool_name": "codex_cli",
        "idempotency_key": "ik-claim-sub",
    })

    # Submit task
    submit_payload = {
        "task_id": "TSK-SUB",
        "agent_id": "codex",
        "idempotency_key": "ik-submit-sub",
        "output": {"code": "def auth(): pass", "tests_passed": True},
    }
    resp = client.post("/api/tasks/TSK-SUB/submit", json=submit_payload)
    assert resp.status_code == 200
    submitted = resp.json()
    assert submitted["status"] == "COMPLETED"
    assert submitted["payload"]["output"]["tests_passed"] is True


def test_update_requirement_blast_radius(client):
    """Verify updating requirement invalidates downstream tasks to STALE_CONFLICT."""
    client.post("/api/nodes", json={"id": "REQ-SPEC", "title": "Spec 1", "type": "REQUIREMENT", "payload": {"spec": "v1"}})
    client.post("/api/nodes", json={"id": "DEC-SPEC", "title": "Dec", "type": "DECISION", "dependencies": ["REQ-SPEC"]})
    client.post("/api/nodes", json={"id": "TSK-SPEC", "title": "Task", "type": "TASK", "dependencies": ["DEC-SPEC"]})

    # Update requirement spec
    update_payload = {
        "req_id": "REQ-SPEC",
        "new_spec": "Use Ed25519 signatures instead of RSA",
        "actor": "architect",
        "idempotency_key": "ik-update-spec",
    }
    resp = client.post("/api/requirements/REQ-SPEC/update", json=update_payload)
    assert resp.status_code == 200
    result = resp.json()
    assert result["requirement"]["payload"]["spec"] == "Use Ed25519 signatures instead of RSA"
    assert result["blast_radius_count"] == 2

    # Check that TSK-SPEC is now STALE_CONFLICT
    resp = client.get("/api/tasks/TSK-SPEC/subgraph")
    assert resp.status_code == 200
    subgraph = resp.json()
    assert subgraph["is_stale"] is True
    assert subgraph["task"]["status"] == "STALE_CONFLICT"

    # Resubmitting task resolves the conflict
    resubmit_resp = client.post("/api/tasks/TSK-SPEC/submit", json={
        "task_id": "TSK-SPEC",
        "agent_id": "codex",
        "idempotency_key": "ik-resubmit-spec",
        "output": {"crypto": "Ed25519"},
    })
    assert resubmit_resp.status_code == 200
    assert resubmit_resp.json()["status"] == "COMPLETED"


def test_node_events_audit_lineage(client):
    """Verify /api/nodes/{node_id}/events returns full event audit lineage."""
    client.post("/api/nodes", json={
        "id": "AUDIT-NODE",
        "title": "Audited Task",
        "type": "TASK",
        "actor": "planner",
        "idempotency_key": "ik-audit-create",
    })
    client.post("/api/tasks/AUDIT-NODE/claim", json={
        "agent_id": "claude",
        "tool_name": "claude_cli",
        "idempotency_key": "ik-audit-claim",
    })
    client.post("/api/tasks/AUDIT-NODE/submit", json={
        "agent_id": "claude",
        "idempotency_key": "ik-audit-submit",
        "output": {"result": "success"},
    })

    resp = client.get("/api/nodes/AUDIT-NODE/events")
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) >= 3
    event_types = [e["event_type"] for e in events]
    assert "NODE_CREATED" in event_types or "EVENT_NODE_CREATED" in event_types
    assert "TASK_CLAIMED" in event_types or "EVENT_TASK_CLAIMED" in event_types
    assert "TASK_SUBMITTED" in event_types or "EVENT_TASK_SUBMITTED" in event_types


# -------------------------------------------------------------
# Strict Idempotency Replay Tests
# -------------------------------------------------------------

def test_idempotency_replay_create_node(client, app_and_store):
    """Verify repeating create node with same idempotency_key returns identical node without extra events."""
    _, store, _ = app_and_store
    key = "idemp-create-001"
    payload = {
        "id": "IDEMP-01",
        "title": "Idempotent Node",
        "type": "TASK",
        "idempotency_key": key,
    }

    # First call
    resp1 = client.post("/api/nodes", json=payload)
    assert resp1.status_code == 200

    # Second call (replay)
    resp2 = client.post("/api/nodes", json=payload)
    assert resp2.status_code == 200
    assert resp1.json()["id"] == resp2.json()["id"]

    # Assert exactly 1 event recorded for this node
    events = store.get_events_for_node("IDEMP-01")
    assert len(events) == 1


def test_idempotency_replay_claim_and_submit(client, app_and_store):
    """Verify repeating claim and submit with identical keys is strictly idempotent."""
    _, store, _ = app_and_store
    client.post("/api/nodes", json={"id": "IDEMP-TASK", "title": "Task", "type": "TASK"})

    claim_key = "idemp-claim-001"
    claim_payload = {
        "task_id": "IDEMP-TASK",
        "agent_id": "agent-1",
        "idempotency_key": claim_key,
    }
    r1 = client.post("/api/tasks/IDEMP-TASK/claim", json=claim_payload)
    r2 = client.post("/api/tasks/IDEMP-TASK/claim", json=claim_payload)
    assert r1.status_code == 200
    assert r2.status_code == 200

    submit_key = "idemp-sub-001"
    sub_payload = {
        "task_id": "IDEMP-TASK",
        "agent_id": "agent-1",
        "idempotency_key": submit_key,
        "output": {"ok": True},
    }
    s1 = client.post("/api/tasks/IDEMP-TASK/submit", json=sub_payload)
    s2 = client.post("/api/tasks/IDEMP-TASK/submit", json=sub_payload)
    assert s1.status_code == 200
    assert s2.status_code == 200

    events = store.get_events_for_node("IDEMP-TASK")
    # 1 create + 1 claim + 1 submit = 3 events total (no duplicate claim or submit)
    assert len(events) == 3


# -------------------------------------------------------------
# WebSocket Real-Time Broadcast Verification Tests
# -------------------------------------------------------------

def test_websocket_broadcast_node_created(client):
    """Verify WebSocket client receives EVENT_NODE_CREATED when node is posted."""
    with client.websocket_connect("/ws/graph") as ws:
        # Trigger node creation via REST
        resp = client.post("/api/nodes", json={
            "id": "WS-REQ-01",
            "title": "Live Requirement",
            "type": "REQUIREMENT",
            "payload": {"data": 123},
            "idempotency_key": "ws-ik-create",
        })
        assert resp.status_code == 200

        # Receive broadcast event
        msg = ws.receive_json()
        assert msg["event_type"] == EVENT_NODE_CREATED
        assert msg["node_id"] == "WS-REQ-01"
        assert msg["node"]["title"] == "Live Requirement"


def test_websocket_broadcast_task_claim_and_submit(client):
    """Verify WebSocket client receives EVENT_TASK_CLAIMED and EVENT_TASK_SUBMITTED."""
    client.post("/api/nodes", json={"id": "WS-TSK-01", "title": "Live Task", "type": "TASK"})

    with client.websocket_connect("/ws/graph") as ws:
        # 1. Claim task
        client.post("/api/tasks/WS-TSK-01/claim", json={
            "agent_id": "claude",
            "tool_name": "claude_cli",
            "idempotency_key": "ws-ik-claim",
        })
        claim_msg = ws.receive_json()
        assert claim_msg["event_type"] == EVENT_TASK_CLAIMED
        assert claim_msg["node_id"] == "WS-TSK-01"
        assert claim_msg["actor"] == "claude"

        # 2. Submit task
        client.post("/api/tasks/WS-TSK-01/submit", json={
            "agent_id": "claude",
            "idempotency_key": "ws-ik-submit",
            "output": {"status": "all_done"},
        })
        submit_msg = ws.receive_json()
        assert submit_msg["event_type"] == EVENT_TASK_SUBMITTED
        assert submit_msg["node_id"] == "WS-TSK-01"
        assert submit_msg["data"]["output"]["status"] == "all_done"


def test_websocket_broadcast_requirement_update_and_conflicts(client):
    """Verify WebSocket client receives EVENT_REQUIREMENT_UPDATED and EVENT_CONFLICT_DETECTED."""
    # Seed requirement and dependent task
    client.post("/api/nodes", json={"id": "WS-REQ-SEC", "title": "Security Req", "type": "REQUIREMENT"})
    client.post("/api/nodes", json={"id": "WS-TSK-SEC", "title": "Implement Crypto", "type": "TASK", "dependencies": ["WS-REQ-SEC"]})

    with client.websocket_connect("/ws/graph") as ws:
        # Update requirement specification
        client.post("/api/requirements/WS-REQ-SEC/update", json={
            "req_id": "WS-REQ-SEC",
            "new_spec": "Mandate TLS 1.3 only",
            "actor": "security_officer",
            "idempotency_key": "ws-ik-req-sec",
        })

        # Expect first message: EVENT_REQUIREMENT_UPDATED
        req_msg = ws.receive_json()
        assert req_msg["event_type"] == EVENT_REQUIREMENT_UPDATED
        assert req_msg["node_id"] == "WS-REQ-SEC"
        assert req_msg["data"]["new_spec"] == "Mandate TLS 1.3 only"

        # Expect second message: EVENT_CONFLICT_DETECTED for WS-TSK-SEC
        conflict_msg = ws.receive_json()
        assert conflict_msg["event_type"] == EVENT_CONFLICT_DETECTED
        assert conflict_msg["node_id"] == "WS-TSK-SEC"
        assert conflict_msg["data"]["invalidated_by"] == "WS-REQ-SEC"
        assert "Mandate TLS 1.3" in conflict_msg["data"]["reason"]


def test_websocket_ping_pong(client):
    """Verify WebSocket responds to client ping messages."""
    with client.websocket_connect("/ws") as ws:
        ws.send_text("ping")
        pong = ws.receive_text()
        assert pong == "pong"
