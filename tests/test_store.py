"""
Unit tests for SQLite Event Store and Idempotency.
"""
import os
import tempfile
import pytest

from harness.models import Edge, Event, Node, NodeType, NodeStatus
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


def test_list_events_and_snapshot_roundtrip_idempotent(temp_store):
    """Verify chronological event listing and idempotent snapshot export/import."""
    n1 = Node(id="REQ-01", title="Req", type=NodeType.REQUIREMENT, status=NodeStatus.PENDING)
    n2 = Node(id="TSK-01", title="Task", type=NodeType.TASK, status=NodeStatus.COMPLETED)
    temp_store.upsert_node(n1)
    temp_store.upsert_node(n2)
    temp_store.add_edge("REQ-01", "TSK-01", "relies_on")

    e1, _ = temp_store.record_event("NODE_CREATED", "REQ-01", "architect", {"v": 1}, idempotency_key="ik-1")
    e2, _ = temp_store.record_event("TASK_CLAIMED", "TSK-01", "claude", {"v": 1}, idempotency_key="ik-2")
    e3, _ = temp_store.record_event("TASK_SUBMITTED", "TSK-01", "claude", {"done": True}, idempotency_key="ik-3")

    all_events = temp_store.list_events()
    assert [e.event_id for e in all_events] == [e1.event_id, e2.event_id, e3.event_id]
    assert [all_events[i].timestamp for i in range(3)] == sorted(e.timestamp for e in all_events)

    snapshot = temp_store.export_snapshot()
    assert len(snapshot.nodes) == 2
    assert len(snapshot.edges) == 1
    assert len(snapshot.events) == 3

    temp_store.clear()
    assert temp_store.list_nodes() == []
    assert temp_store.list_edges() == []
    assert temp_store.list_events() == []

    counts = temp_store.import_snapshot(
        nodes=snapshot.nodes,
        edges=snapshot.edges,
        events=snapshot.events,
        replace=True,
    )
    assert counts == {"nodes_imported": 2, "edges_imported": 1, "events_imported": 3}
    assert {n.id for n in temp_store.list_nodes()} == {"REQ-01", "TSK-01"}
    assert len(temp_store.list_edges()) == 1
    assert [e.event_id for e in temp_store.list_events()] == [e1.event_id, e2.event_id, e3.event_id]

    # Re-importing same snapshot in merge mode is idempotent on event_id and idempotency_key
    duplicate_key_event = Event(
        event_id="different-event-id",
        idempotency_key="ik-2",
        event_type="TASK_CLAIMED",
        node_id="TSK-01",
        actor="claude",
        data={},
    )
    reimport_counts = temp_store.import_snapshot(
        nodes=snapshot.nodes,
        edges=snapshot.edges,
        events=snapshot.events + [duplicate_key_event],
        replace=False,
    )
    assert reimport_counts == {"nodes_imported": 2, "edges_imported": 1, "events_imported": 0}
    assert len(temp_store.list_events()) == 3
