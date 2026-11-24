"""
Unit tests for SQLite Event Store and Idempotency.
"""
import os
import tempfile
import pytest

from harness.models import Node, NodeType, NodeStatus
from harness.store import EventStore


@pytest.fixture
def temp_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = EventStore(db_path=path)
    yield store
    if os.path.exists(path):
        os.remove(path)


def test_idempotent_event_recording(temp_store):
    # First write
    event1, is_new1 = temp_store.record_event(
        event_type="TASK_CLAIMED",
        node_id="TSK-01",
        actor="claude-code",
        data={"action": "claim"},
        idempotency_key="idemp-key-123"
    )
    assert is_new1 is True
    assert event1.idempotency_key == "idemp-key-123"

    # Second write with same idempotency key
    event2, is_new2 = temp_store.record_event(
        event_type="TASK_CLAIMED",
        node_id="TSK-01",
        actor="claude-code",
        data={"action": "duplicate_attempt"},
        idempotency_key="idemp-key-123"
    )
    assert is_new2 is False
    assert event2.event_id == event1.event_id
    assert event2.data["action"] == "claim"


def test_node_upsert_and_fetch(temp_store):
    node = Node(
        id="REQ-01",
        title="Authentication Requirement",
        type=NodeType.REQUIREMENT,
        status=NodeStatus.PENDING,
        payload={"spec": "Support OAuth2 and JWT"}
    )
    temp_store.upsert_node(node)

    fetched = temp_store.get_node("REQ-01")
    assert fetched is not None
    assert fetched.title == "Authentication Requirement"
    assert fetched.payload["spec"] == "Support OAuth2 and JWT"

    # Update node version and status
    node.version += 1
    node.status = NodeStatus.COMPLETED
    temp_store.upsert_node(node)

    updated = temp_store.get_node("REQ-01")
    assert updated.version == 2
    assert updated.status == NodeStatus.COMPLETED


def test_edge_recording(temp_store):
    temp_store.add_edge(source="REQ-01", target="TSK-01", relation="blocks")
    # Duplicate edge insert should be ignored
    temp_store.add_edge(source="REQ-01", target="TSK-01", relation="blocks")

    edges = temp_store.list_edges()
    assert len(edges) == 1
    assert edges[0].source == "REQ-01"
    assert edges[0].target == "TSK-01"
