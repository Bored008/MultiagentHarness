"""
Unit & Integration tests for TaskMaster To-Do FastAPI CRUD Endpoints and WebSocket events (TSK-02).
"""
import os
import tempfile
import pytest
from fastapi.testclient import TestClient

from harness.server import create_app
from todo.models import TodoPriority, TodoStatus
from todo.store import TodoStore


@pytest.fixture
def client_and_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "omni.db")
        todo_path = os.path.join(tmpdir, "todos.db")
        store = TodoStore(db_path=todo_path)
        app = create_app(db_path=db_path, todo_store=store)
        with TestClient(app) as client:
            yield client, store


def test_create_todo_valid_and_validation_errors(client_and_store):
    client, store = client_and_store

    # 1. Valid creation
    payload = {
        "title": "Design Neon Status Badges",
        "description": "Ensure Tailwind color matrix matches REQ-01 specification",
        "deadline": "2026-12-01T15:00:00Z",
        "expected_hours": 3.5,
        "status": "working",
        "priority": "mvp",
    }
    resp = client.post("/api/todos", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == payload["title"]
    assert data["status"] == "working"
    assert data["priority"] == "mvp"
    assert data["expected_hours"] == 3.5
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data

    # 2. Reject empty title
    bad_payload = dict(payload, title="")
    resp = client.post("/api/todos", json=bad_payload)
    assert resp.status_code == 422

    # 3. Reject non-positive expected_hours
    bad_hours = dict(payload, expected_hours=0)
    resp = client.post("/api/todos", json=bad_hours)
    assert resp.status_code == 422

    bad_hours_neg = dict(payload, expected_hours=-1.5)
    resp = client.post("/api/todos", json=bad_hours_neg)
    assert resp.status_code == 422

    # 4. Reject invalid deadline format
    bad_deadline = dict(payload, deadline="invalid-timestamp")
    resp = client.post("/api/todos", json=bad_deadline)
    assert resp.status_code == 422


def test_list_todos_with_filtering_and_aggregates(client_and_store):
    client, store = client_and_store

    # Create multiple tasks
    t1 = {
        "title": "Build Auth Flow",
        "description": "JWT authentication in FastAPI",
        "deadline": "2026-12-01T10:00:00Z",
        "expected_hours": 4.0,
        "status": "pending",
        "priority": "mvp",
    }
    t2 = {
        "title": "Setup PostgreSQL Connection",
        "description": "Asyncpg driver config",
        "deadline": "2026-12-02T10:00:00Z",
        "expected_hours": 2.5,
        "status": "working",
        "priority": "high",
    }
    t3 = {
        "title": "Write Playwright E2E Tests",
        "description": "Test auth and todo table interactions",
        "deadline": "2026-12-03T10:00:00Z",
        "expected_hours": 3.5,
        "status": "completed",
        "priority": "mvp",
    }

    client.post("/api/todos", json=t1)
    client.post("/api/todos", json=t2)
    client.post("/api/todos", json=t3)

    # 1. Unfiltered query
    resp = client.get("/api/todos")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 3
    assert data["stats"]["total_count"] == 3
    assert data["stats"]["total_expected_hours"] == 10.0
    assert data["stats"]["mvp_count"] == 2
    assert data["stats"]["mvp_expected_hours"] == 7.5

    # 2. Filter by status=pending
    resp_pending = client.get("/api/todos?status=pending")
    assert resp_pending.status_code == 200
    pending_data = resp_pending.json()
    assert len(pending_data["items"]) == 1
    assert pending_data["items"][0]["title"] == "Build Auth Flow"
    # Aggregate metrics reflect full dataset
    assert pending_data["stats"]["total_count"] == 3

    # 3. Filter by priority=mvp
    resp_mvp = client.get("/api/todos?priority=mvp")
    assert resp_mvp.status_code == 200
    mvp_items = resp_mvp.json()["items"]
    assert len(mvp_items) == 2
    assert {i["title"] for i in mvp_items} == {"Build Auth Flow", "Write Playwright E2E Tests"}

    # 4. Search query q matching title or description
    resp_q = client.get("/api/todos?q=Asyncpg")
    assert resp_q.status_code == 200
    q_items = resp_q.json()["items"]
    assert len(q_items) == 1
    assert q_items[0]["title"] == "Setup PostgreSQL Connection"


def test_get_and_patch_todo(client_and_store):
    client, store = client_and_store

    create_resp = client.post(
        "/api/todos",
        json={
            "title": "Initial Draft",
            "description": "First version",
            "deadline": "2026-12-10T12:00:00Z",
            "expected_hours": 1.5,
            "status": "pending",
            "priority": "low",
        },
    )
    todo_id = create_resp.json()["id"]
    initial_updated_at = create_resp.json()["updated_at"]

    # 1. Get single item
    get_resp = client.get(f"/api/todos/{todo_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == todo_id

    # 2. Partial PATCH update
    patch_resp = client.patch(
        f"/api/todos/{todo_id}",
        json={
            "status": "completed",
            "expected_hours": 2.0,
            "priority": "mvp",
        },
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched["status"] == "completed"
    assert patched["expected_hours"] == 2.0
    assert patched["priority"] == "mvp"
    assert patched["title"] == "Initial Draft"
    assert patched["updated_at"] >= initial_updated_at

    # 3. PATCH non-existent returns 404
    bad_patch = client.patch("/api/todos/NONEXISTENT", json={"status": "working"})
    assert bad_patch.status_code == 404


def test_delete_todo(client_and_store):
    client, store = client_and_store

    create_resp = client.post(
        "/api/todos",
        json={
            "title": "Task to Delete",
            "deadline": "2026-12-15T00:00:00Z",
            "expected_hours": 1.0,
        },
    )
    todo_id = create_resp.json()["id"]

    del_resp = client.delete(f"/api/todos/{todo_id}")
    assert del_resp.status_code == 200
    assert del_resp.json() == {"status": "deleted", "id": todo_id}

    # Verify 404 on subsequent get
    assert client.get(f"/api/todos/{todo_id}").status_code == 404

    # Verify 404 on deleting non-existent item
    assert client.delete(f"/api/todos/{todo_id}").status_code == 404


def test_todo_websocket_broadcast(client_and_store):
    client, store = client_and_store

    with client.websocket_connect("/ws") as ws:
        # Check initial handshake event if emitted
        initial = ws.receive_json()
        assert initial.get("event") in ("CONNECTED", "CONNECTION_ESTABLISHED")

        # Create todo
        create_resp = client.post(
            "/api/todos",
            json={
                "title": "WebSocket Sync Task",
                "deadline": "2026-12-20T12:00:00Z",
                "expected_hours": 2.0,
                "status": "pending",
                "priority": "mvp",
            },
        )
        assert create_resp.status_code == 201
        todo_id = create_resp.json()["id"]

        msg = ws.receive_json()
        assert msg["event"] == "TODO_CREATED"
        assert msg["todo"]["id"] == todo_id

        # Update todo
        patch_resp = client.patch(
            f"/api/todos/{todo_id}",
            json={"status": "working"},
        )
        assert patch_resp.status_code == 200

        msg_upd = ws.receive_json()
        assert msg_upd["event"] == "TODO_UPDATED"
        assert msg_upd["todo"]["status"] == "working"

        # Delete todo
        del_resp = client.delete(f"/api/todos/{todo_id}")
        assert del_resp.status_code == 200

        msg_del = ws.receive_json()
        assert msg_del["event"] == "TODO_DELETED"
        assert msg_del["todo_id"] == todo_id
