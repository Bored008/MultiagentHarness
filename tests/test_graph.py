"""
Unit tests for NetworkX In-Memory DAG, Cycle Prevention,
Blast-Radius Invalidation, Scoped Subgraphs, and Conflict Resolution.
"""
import os
import tempfile
import pytest

from harness.models import (
    Edge,
    GraphStateResponse,
    Node,
    NodeStatus,
    NodeType,
    SubgraphResponse,
)
from harness.graph import (
    CycleError,
    DAGManager,
    NodeNotFoundError,
    TaskConflictError,
)
from harness.store import EventStore


@pytest.fixture
def temp_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = EventStore(db_path=path)
    yield store
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def dag(temp_store):
    return DAGManager(store=temp_store)


def test_build_from_store(temp_store):
    # Pre-populate store with nodes and edges
    req = Node(id="REQ-01", title="Auth Spec", type=NodeType.REQUIREMENT, status=NodeStatus.COMPLETED)
    dec = Node(id="DEC-01", title="JWT Architecture", type=NodeType.DECISION, status=NodeStatus.COMPLETED)
    tsk = Node(id="TSK-01", title="Implement JWT", type=NodeType.TASK, status=NodeStatus.PENDING)

    temp_store.upsert_node(req)
    temp_store.upsert_node(dec)
    temp_store.upsert_node(tsk)

    temp_store.add_edge("REQ-01", "DEC-01")
    temp_store.add_edge("DEC-01", "TSK-01")

    # Initialize manager and verify hydration
    manager = DAGManager(store=temp_store)
    assert len(manager.list_nodes()) == 3
    assert len(manager.list_edges()) == 2
    assert manager.get_node("REQ-01").title == "Auth Spec"
    assert manager.get_node("TSK-01").status == NodeStatus.PENDING

    # Test rehydration via build_from_store
    manager.build_from_store(temp_store)
    assert len(manager.list_nodes()) == 3


def test_cycle_prevention(dag):
    # 1. Add base nodes
    req = Node(id="REQ-01", title="Spec", type=NodeType.REQUIREMENT)
    dec = Node(id="DEC-01", title="Decision", type=NodeType.DECISION)
    tsk1 = Node(id="TSK-01", title="Task 1", type=NodeType.TASK)
    tsk2 = Node(id="TSK-02", title="Task 2", type=NodeType.TASK)

    dag.add_node(req)
    dag.add_node(dec, dependencies=["REQ-01"])
    dag.add_node(tsk1, dependencies=["DEC-01"])
    dag.add_node(tsk2, dependencies=["TSK-01"])

    # 2. Prevent self-loop on add_node
    loop_node = Node(id="LOOP-01", title="Self Loop", type=NodeType.TASK)
    with pytest.raises(ValueError):
        dag.add_node(loop_node, dependencies=["LOOP-01"])

    # 3. Prevent self-loop on add_dependency
    with pytest.raises(ValueError):
        dag.add_dependency("REQ-01", "REQ-01")

    # 4. Prevent direct 2-node cycle (TSK-01 -> DEC-01 when DEC-01 -> TSK-01 exists)
    with pytest.raises(ValueError):
        dag.add_dependency(source="TSK-01", target="DEC-01")

    # 5. Prevent transitive cycle (TSK-02 -> REQ-01 when REQ-01 -> DEC-01 -> TSK-01 -> TSK-02)
    with pytest.raises(ValueError):
        dag.add_dependency(source="TSK-02", target="REQ-01")

    # 6. Verify graph state remains valid and acyclic
    assert len(dag.list_edges()) == 3
    assert dag.get_downstream_dependents("REQ-01") == ["DEC-01", "TSK-01", "TSK-02"]


def test_downstream_dependents_and_blast_radius(dag):
    # Diamond graph:
    #      REQ-01
    #     /      \
    #  DEC-01   DEC-02
    #     \      /
    #      TSK-01
    #        |
    #      TSK-02
    req = Node(id="REQ-01", title="Root", type=NodeType.REQUIREMENT)
    dec1 = Node(id="DEC-01", title="Decision 1", type=NodeType.DECISION)
    dec2 = Node(id="DEC-02", title="Decision 2", type=NodeType.DECISION)
    tsk1 = Node(id="TSK-01", title="Task 1", type=NodeType.TASK)
    tsk2 = Node(id="TSK-02", title="Task 2", type=NodeType.TASK)

    dag.add_node(req)
    dag.add_node(dec1, dependencies=["REQ-01"])
    dag.add_node(dec2, dependencies=["REQ-01"])
    dag.add_node(tsk1, dependencies=["DEC-01", "DEC-02"])
    dag.add_node(tsk2, dependencies=["TSK-01"])

    # Verify blast radius of REQ-01 includes all downstream nodes
    blast_radius = dag.get_downstream_dependents("REQ-01")
    assert set(blast_radius) == {"DEC-01", "DEC-02", "TSK-01", "TSK-02"}
    # TSK-02 must come after TSK-01 in topological ordering
    assert blast_radius.index("TSK-01") < blast_radius.index("TSK-02")

    # Blast radius of DEC-01
    assert set(dag.get_downstream_dependents("DEC-01")) == {"TSK-01", "TSK-02"}

    # Blast radius of leaf node is empty
    assert dag.get_downstream_dependents("TSK-02") == []


def test_invalidate_subtree(dag, temp_store):
    req = Node(id="REQ-01", title="Auth", type=NodeType.REQUIREMENT, status=NodeStatus.COMPLETED)
    dec = Node(id="DEC-01", title="JWT Architecture", type=NodeType.DECISION, status=NodeStatus.COMPLETED)
    tsk1 = Node(id="TSK-01", title="Draft Auth", type=NodeType.TASK, status=NodeStatus.COMPLETED)
    tsk2 = Node(id="TSK-02", title="Verify Auth", type=NodeType.TASK, status=NodeStatus.IN_PROGRESS)

    dag.add_node(req)
    dag.add_node(dec, dependencies=["REQ-01"])
    dag.add_node(tsk1, dependencies=["DEC-01"])
    dag.add_node(tsk2, dependencies=["TSK-01"])

    # Invalidate subtree from REQ-01
    invalidated = dag.invalidate_subtree(
        node_id="REQ-01",
        reason="Switched crypto standard from RSA to Ed25519",
        actor="lead-architect",
    )

    assert len(invalidated) == 3
    invalidated_ids = [n.id for n in invalidated]
    assert invalidated_ids == ["DEC-01", "TSK-01", "TSK-02"]

    # Verify each invalidated node has STALE_CONFLICT status and conflict payload
    for node in invalidated:
        assert node.status == NodeStatus.STALE_CONFLICT
        assert node.payload["is_stale"] is True
        assert node.payload["conflict_reason"] == "Switched crypto standard from RSA to Ed25519"
        assert node.payload["conflict"]["invalidated_by"] == "REQ-01"
        assert node.payload["conflict"]["actor"] == "lead-architect"

    # Verify REQ-01 itself was not marked STALE_CONFLICT
    assert dag.get_node("REQ-01").status == NodeStatus.COMPLETED

    # Verify SQLite store events recorded CONFLICT_DETECTED
    events = temp_store.get_events_for_node("TSK-02")
    conflict_events = [e for e in events if e.event_type == "CONFLICT_DETECTED"]
    assert len(conflict_events) == 1
    assert conflict_events[0].actor == "lead-architect"

    # Verify store node was updated
    stored_tsk2 = temp_store.get_node("TSK-02")
    assert stored_tsk2.status == NodeStatus.STALE_CONFLICT


def test_scoped_subgraph_healthy(dag):
    req = Node(id="REQ-01", title="Auth", type=NodeType.REQUIREMENT, status=NodeStatus.COMPLETED)
    dec = Node(id="DEC-01", title="JWT", type=NodeType.DECISION, status=NodeStatus.COMPLETED)
    tsk1 = Node(id="TSK-01", title="Draft", type=NodeType.TASK, status=NodeStatus.COMPLETED)
    tsk2 = Node(id="TSK-02", title="Review", type=NodeType.TASK, status=NodeStatus.PENDING)
    unrelated = Node(id="REQ-02", title="Billing", type=NodeType.REQUIREMENT, status=NodeStatus.PENDING)

    dag.add_node(req)
    dag.add_node(dec, dependencies=["REQ-01"])
    dag.add_node(tsk1, dependencies=["DEC-01"])
    dag.add_node(tsk2, dependencies=["TSK-01"])
    dag.add_node(unrelated)

    subgraph = dag.get_task_subgraph("TSK-02")
    assert isinstance(subgraph, SubgraphResponse)
    assert subgraph.task.id == "TSK-02"
    ancestor_ids = [a.id for a in subgraph.ancestors]
    assert set(ancestor_ids) == {"REQ-01", "DEC-01", "TSK-01"}
    assert "REQ-02" not in ancestor_ids

    # Healthy state assertions
    assert subgraph.is_stale is False
    assert subgraph.conflict_details is None

    # Check edges in scoped subgraph
    edge_pairs = [(e.source, e.target) for e in subgraph.edges]
    assert ("REQ-01", "DEC-01") in edge_pairs
    assert ("DEC-01", "TSK-01") in edge_pairs
    assert ("TSK-01", "TSK-02") in edge_pairs


def test_scoped_subgraph_stale_ancestor(dag):
    req = Node(id="REQ-01", title="Auth", type=NodeType.REQUIREMENT, status=NodeStatus.COMPLETED)
    dec = Node(id="DEC-01", title="JWT", type=NodeType.DECISION, status=NodeStatus.COMPLETED)
    tsk = Node(id="TSK-01", title="Implement", type=NodeType.TASK, status=NodeStatus.PENDING)

    dag.add_node(req)
    dag.add_node(dec, dependencies=["REQ-01"])
    dag.add_node(tsk, dependencies=["DEC-01"])

    # Invalidate DEC-01
    dag.invalidate_subtree("REQ-01", reason="OAuth 1.0 deprecated")

    # Inspect TSK-01 subgraph
    subgraph = dag.get_task_subgraph("TSK-01")
    assert subgraph.is_stale is True
    assert subgraph.conflict_details is not None
    assert "OAuth 1.0 deprecated" in subgraph.conflict_details


def test_resolve_conflict(dag, temp_store):
    req = Node(id="REQ-01", title="Auth", type=NodeType.REQUIREMENT, status=NodeStatus.COMPLETED)
    tsk = Node(id="TSK-01", title="Task", type=NodeType.TASK, status=NodeStatus.COMPLETED)

    dag.add_node(req)
    dag.add_node(tsk, dependencies=["REQ-01"])

    dag.invalidate_subtree("REQ-01", reason="API Schema Changed")
    assert dag.get_node("TSK-01").status == NodeStatus.STALE_CONFLICT

    # Resolve conflict
    new_output = {"signature": "Ed25519", "key_size": 256, "status": "adapted"}
    resolved = dag.resolve_conflict(
        task_id="TSK-01",
        new_output=new_output,
        actor="codex-worker",
    )

    assert resolved.status == NodeStatus.COMPLETED
    assert resolved.payload["output"] == new_output
    assert resolved.payload["resolved_by"] == "codex-worker"
    assert "is_stale" not in resolved.payload
    assert "conflict_reason" not in resolved.payload

    # Subgraph for TSK-01 is now healthy
    subgraph = dag.get_task_subgraph("TSK-01")
    assert subgraph.is_stale is False

    # Event store has CONFLICT_RESOLVED event
    events = temp_store.get_events_for_node("TSK-01")
    assert any(e.event_type == "CONFLICT_RESOLVED" for e in events)


def test_get_graph_state(dag):
    req = Node(id="REQ-01", title="Req", type=NodeType.REQUIREMENT, status=NodeStatus.COMPLETED)
    tsk1 = Node(id="TSK-01", title="Task 1", type=NodeType.TASK, status=NodeStatus.IN_PROGRESS)
    tsk2 = Node(id="TSK-02", title="Task 2", type=NodeType.TASK, status=NodeStatus.PENDING)

    dag.add_node(req)
    dag.add_node(tsk1, dependencies=["REQ-01"])
    dag.add_node(tsk2, dependencies=["TSK-01"])

    state = dag.get_graph_state()
    assert isinstance(state, GraphStateResponse)
    assert len(state.nodes) == 3
    assert len(state.edges) == 2
    assert state.stats["total_nodes"] == 3
    assert state.stats["total_edges"] == 2
    assert state.stats["completed"] == 1
    assert state.stats["in_progress"] == 1
    assert state.stats["pending"] == 1
    assert state.stats["stale_conflict"] == 0


def test_task_claim_and_submission(dag):
    tsk = Node(id="TSK-01", title="Work", type=NodeType.TASK, status=NodeStatus.PENDING)
    dag.add_node(tsk)

    # Claim task
    claimed = dag.claim_task("TSK-01", agent_id="claude-code")
    assert claimed.status == NodeStatus.IN_PROGRESS
    assert claimed.assigned_to == "claude-code"

    # Submit task
    submitted = dag.submit_task("TSK-01", agent_id="claude-code", output={"code": "print('hello')"})
    assert submitted.status == NodeStatus.COMPLETED
    assert submitted.payload["output"] == {"code": "print('hello')"}

    # Attempt to submit when in STALE_CONFLICT raises TaskConflictError
    tsk2 = Node(id="TSK-02", title="Stale Task", type=NodeType.TASK, status=NodeStatus.STALE_CONFLICT)
    dag.add_node(tsk2)
    with pytest.raises(TaskConflictError):
        dag.submit_task("TSK-02", agent_id="codex", output={})
