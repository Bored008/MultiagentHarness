"""
In-Memory NetworkX DAG Manager and Blast-Radius Invalidation Engine for OmniHarness.
Provides real-time dependency resolution, cycle prevention, atomic downstream
blast-radius invalidation, scoped subgraph extraction, conflict resolution,
and crash recovery hydration from SQLite EventStore.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx

from harness.models import (
    Edge,
    Event,
    GraphStateResponse,
    Node,
    NodeStatus,
    NodeType,
    SubgraphResponse,
)
from harness.store import EventStore

logger = logging.getLogger("omniharness.graph")


class GraphError(Exception):
    """Base exception for graph operations."""
    pass


class CycleError(GraphError, ValueError):
    """Raised when an operation would introduce a cycle into the DAG."""
    pass


class NodeNotFoundError(GraphError, KeyError):
    """Raised when a referenced node does not exist in the graph."""
    pass


class TaskConflictError(GraphError):
    """Raised when a task cannot be claimed or submitted due to status conflict."""
    pass


class DAGManager:
    """
    Thread-safe in-memory DAG manager backed by NetworkX and SQLite EventStore.
    Wraps networkx.DiGraph for instant dependency traversal, cycle rejection,
    and blast-radius invalidation.
    """

    def __init__(self, store: Optional[EventStore] = None):
        self.store = store
        self.graph: nx.DiGraph = nx.DiGraph()
        self._nodes: Dict[str, Node] = {}
        self._lock = threading.RLock()
        if self.store is not None:
            self.build_from_store(self.store)

    def build_from_store(self, store: EventStore) -> None:
        """
        Reconstruct in-memory DAG from SQLite store nodes and edges.
        Asserts acyclicity of the persisted graph.
        """
        with self._lock:
            self.store = store
            self.graph.clear()
            self._nodes.clear()

            nodes = store.list_nodes()
            for node in nodes:
                self._nodes[node.id] = node
                self.graph.add_node(node.id, data=node)

            edges = store.list_edges()
            for edge in edges:
                self.graph.add_edge(edge.source, edge.target, relation=edge.relation)

            if not nx.is_directed_acyclic_graph(self.graph):
                cycles = list(nx.simple_cycles(self.graph))
                raise CycleError(f"Database contains cyclic dependencies: {cycles}")

            logger.info("Hydrated %d nodes and %d edges from store", len(nodes), len(edges))

    def hydrate_from_store(self) -> None:
        """Alias for build_from_store using existing store."""
        if self.store is not None:
            self.build_from_store(self.store)

    def get_node(self, node_id: Any) -> Optional[Node]:
        """Fetch node by ID from in-memory index, falling back to store."""
        if isinstance(node_id, Node):
            return node_id
        with self._lock:
            if node_id in self._nodes:
                return self._nodes[node_id]
            if self.store is not None:
                node = self.store.get_node(str(node_id))
                if node is not None:
                    self._nodes[node.id] = node
                    self.graph.add_node(node.id, data=node)
                    return node
            return None

    def has_node(self, node_id: Any) -> bool:
        """Check if node exists in in-memory graph or store."""
        if isinstance(node_id, Node):
            return True
        return self.get_node(node_id) is not None

    def list_nodes(self) -> List[Node]:
        """Return all nodes in the DAG."""
        with self._lock:
            return list(self._nodes.values())

    def list_edges(self) -> List[Edge]:
        """Return all directed edges in the DAG."""
        with self._lock:
            edges: List[Edge] = []
            for u, v, data in self.graph.edges(data=True):
                edges.append(Edge(source=u, target=v, relation=data.get("relation", "relies_on")))
            return edges

    def add_node(
        self,
        node: Node,
        dependencies: Optional[List[str]] = None,
        actor: str = "system",
        idempotency_key: Optional[str] = None,
    ) -> Node:
        """
        Add a node and its incoming dependency edges (dependency -> node).
        Validates dependencies and rejects cycles (raises ValueError / CycleError).
        Synchronizes with SQLite store if attached.
        """
        with self._lock:
            deps = list(dict.fromkeys(dependencies or []))

            # 1. Check for self-loops
            for dep_id in deps:
                if dep_id == node.id:
                    raise CycleError(f"Self-loop detected: node '{node.id}' cannot depend on itself.")

            # 2. Verify dependencies exist (load from store if not in memory)
            for dep_id in deps:
                if dep_id not in self._nodes and self.store is not None:
                    stored_node = self.store.get_node(dep_id)
                    if stored_node is not None:
                        self._nodes[stored_node.id] = stored_node
                        self.graph.add_node(stored_node.id, data=stored_node)
                if dep_id not in self._nodes:
                    raise NodeNotFoundError(f"Dependency node '{dep_id}' not found.")

            # 3. Check for cycles: if path exists from node.id to dep_id, edge creates cycle
            if self.graph.has_node(node.id):
                for dep_id in deps:
                    if nx.has_path(self.graph, node.id, dep_id):
                        cycle_path = nx.shortest_path(self.graph, node.id, dep_id) + [node.id]
                        raise CycleError(
                            f"Cycle detected: adding edge {dep_id} -> {node.id} creates cycle "
                            f"{' -> '.join(cycle_path)}"
                        )

            # 4. Apply to in-memory graph
            self._nodes[node.id] = node
            self.graph.add_node(node.id, data=node)
            for dep_id in deps:
                self.graph.add_edge(dep_id, node.id, relation="relies_on")

            # 5. Persist to SQLite store
            if self.store is not None:
                self.store.upsert_node(node)
                for dep_id in deps:
                    self.store.add_edge(source=dep_id, target=node.id, relation="relies_on")
                self.store.record_event(
                    event_type="NODE_CREATED",
                    node_id=node.id,
                    actor=actor,
                    data={
                        "title": node.title,
                        "type": node.type.value if hasattr(node.type, "value") else str(node.type),
                        "dependencies": deps,
                    },
                    idempotency_key=idempotency_key,
                )

            return node

    def add_dependency(self, source: str, target: str, relation: str = "relies_on") -> Edge:
        """
        Add directed dependency edge: source -> target (source is dependency, target is dependent).
        Rejects self-loops and circular dependencies before touching graph or store.
        """
        with self._lock:
            if not self.has_node(source):
                raise NodeNotFoundError(f"Source node '{source}' not found.")
            if not self.has_node(target):
                raise NodeNotFoundError(f"Target node '{target}' not found.")
            if source == target:
                raise CycleError(f"Self-loop detected: node '{source}' cannot depend on itself.")

            if self.graph.has_edge(source, target):
                return Edge(source=source, target=target, relation=relation)

            # Cycle detection: path from target to source means adding source -> target creates cycle
            if nx.has_path(self.graph, target, source):
                cycle_path = nx.shortest_path(self.graph, target, source) + [target]
                raise CycleError(
                    f"Cycle detected: adding edge {source} -> {target} creates cycle "
                    f"{' -> '.join(cycle_path)}"
                )

            self.graph.add_edge(source, target, relation=relation)
            if self.store is not None:
                self.store.add_edge(source=source, target=target, relation=relation)
            return Edge(source=source, target=target, relation=relation)

    def add_edge(self, source: str, target: str, relation: str = "relies_on") -> Edge:
        """Alias for add_dependency."""
        return self.add_dependency(source=source, target=target, relation=relation)

    def get_downstream_dependents(self, node_id: str) -> List[str]:
        """
        Return all descendant node IDs reachable from node_id (blast radius),
        ordered in topological sort order.
        """
        with self._lock:
            if node_id not in self._nodes:
                raise NodeNotFoundError(f"Node '{node_id}' not found.")
            descendants = nx.descendants(self.graph, node_id)
            if not descendants:
                return []
            sub = self.graph.subgraph(descendants)
            try:
                return list(nx.topological_sort(sub))
            except Exception:
                return sorted(list(descendants))

    def get_descendants(self, node_id: str) -> Set[str]:
        """Return all transitive downstream descendant node IDs as a set."""
        with self._lock:
            if node_id not in self._nodes:
                raise NodeNotFoundError(f"Node '{node_id}' not found.")
            return nx.descendants(self.graph, node_id)

    def get_ancestors(self, node_id: str) -> Set[str]:
        """Return all transitive upstream ancestor node IDs as a set."""
        with self._lock:
            if node_id not in self._nodes:
                raise NodeNotFoundError(f"Node '{node_id}' not found.")
            return nx.ancestors(self.graph, node_id)

    def get_upstream_dependencies(self, node_id: str) -> List[str]:
        """
        Return all ancestor node IDs that node_id depends on, ordered topologically.
        """
        with self._lock:
            if node_id not in self._nodes:
                raise NodeNotFoundError(f"Node '{node_id}' not found.")
            ancestors = nx.ancestors(self.graph, node_id)
            if not ancestors:
                return []
            sub = self.graph.subgraph(ancestors)
            try:
                return list(nx.topological_sort(sub))
            except Exception:
                return sorted(list(ancestors))

    def invalidate_subtree(
        self,
        node_id: str,
        reason: str,
        actor: str = "system",
    ) -> List[Node]:
        """
        Mark downstream tasks and dependents STALE_CONFLICT, update payload
        with conflict info, record event in store, upsert in store.
        Returns list of invalidated Node objects.
        """
        with self._lock:
            if node_id not in self._nodes:
                raise NodeNotFoundError(f"Node '{node_id}' not found.")

            downstream_ids = self.get_downstream_dependents(node_id)
            if not downstream_ids:
                return []

            invalidated_nodes: List[Node] = []
            now = time.time()

            for dep_id in downstream_ids:
                target_node = self._nodes[dep_id]
                prev_status = (
                    target_node.status.value
                    if hasattr(target_node.status, "value")
                    else str(target_node.status)
                )

                target_node.status = NodeStatus.STALE_CONFLICT
                target_node.version += 1
                target_node.updated_at = now
                target_node.payload["is_stale"] = True
                target_node.payload["stale_reason"] = reason
                target_node.payload["conflict_reason"] = reason
                target_node.payload["conflict"] = {
                    "reason": reason,
                    "invalidated_by": node_id,
                    "timestamp": now,
                    "actor": actor,
                }

                if self.store is not None:
                    self.store.upsert_node(target_node)
                    self.store.record_event(
                        event_type="CONFLICT_DETECTED",
                        node_id=dep_id,
                        actor=actor,
                        data={
                            "cause_node_id": node_id,
                            "previous_status": prev_status,
                            "new_status": NodeStatus.STALE_CONFLICT.value,
                            "reason": reason,
                            "version": target_node.version,
                        },
                    )

                self.graph.nodes[dep_id]["data"] = target_node
                invalidated_nodes.append(target_node)

            return invalidated_nodes

    def invalidate_downstream(
        self,
        node_id: str,
        actor: str = "system",
        reason: Optional[str] = None,
    ) -> List[str]:
        """Alias returning list of invalidated node IDs."""
        nodes = self.invalidate_subtree(
            node_id=node_id,
            reason=reason or f"Upstream dependency '{node_id}' updated.",
            actor=actor,
        )
        return [n.id for n in nodes]

    def get_task_subgraph(
        self,
        task_id: str,
        max_depth: Optional[int] = None,
        max_tokens: int = 1500,
    ) -> SubgraphResponse:
        """
        Extract scoped context containing target task, ordered ancestors,
        induced edges, and staleness/conflict details (~1.5k tokens).
        """
        with self._lock:
            task = self.get_node(task_id)
            if not task:
                raise NodeNotFoundError(f"Task '{task_id}' not found.")

            ancestor_ids = nx.ancestors(self.graph, task_id)
            # Rank ancestors by shortest path distance to target task
            ancestors_with_dist = [
                (nx.shortest_path_length(self.graph, a, task_id), a)
                for a in ancestor_ids
            ]
            ancestors_with_dist.sort(key=lambda x: (x[0], x[1]))

            selected_nodes: List[Node] = []
            selected_ids = {task_id}
            is_stale = (task.status == NodeStatus.STALE_CONFLICT) or bool(task.payload.get("is_stale", False))
            conflict_details = task.payload.get("conflict_reason") or task.payload.get("stale_reason")

            # Token budgeting: estimate ~250 tokens per ancestor, ~300 base tokens
            budget_ancestors = max(1, (max_tokens - 300) // 250) if max_tokens > 300 else 1

            for dist, a_id in ancestors_with_dist:
                if max_depth is not None and dist > max_depth:
                    continue
                if len(selected_nodes) >= budget_ancestors:
                    break
                anc_node = self._nodes[a_id]
                selected_nodes.append(anc_node)
                selected_ids.add(a_id)

                if anc_node.status == NodeStatus.STALE_CONFLICT or bool(anc_node.payload.get("is_stale", False)):
                    is_stale = True
                    if not conflict_details:
                        anc_reason = anc_node.payload.get("conflict_reason") or anc_node.payload.get("stale_reason")
                        conflict_details = (
                            f"Ancestor '{a_id}' has conflict: {anc_reason or 'specification changed'}"
                        )

            # Induced subgraph edges
            induced = self.graph.subgraph(selected_ids)
            subgraph_edges = [
                Edge(source=u, target=v, relation=data.get("relation", "relies_on"))
                for u, v, data in induced.edges(data=True)
            ]

            return SubgraphResponse(
                task=task,
                ancestors=selected_nodes,
                edges=subgraph_edges,
                is_stale=is_stale,
                conflict_details=conflict_details,
            )

    def get_scoped_subgraph(
        self,
        node_id: str,
        max_depth: Optional[int] = None,
        max_tokens: int = 1500,
    ) -> SubgraphResponse:
        """Alias for get_task_subgraph."""
        return self.get_task_subgraph(task_id=node_id, max_depth=max_depth, max_tokens=max_tokens)

    def resolve_conflict(
        self,
        task_id: str,
        new_output: Dict[str, Any],
        actor: str,
        idempotency_key: Optional[str] = None,
    ) -> Node:
        """
        Resolve a conflict on a task: clear stale state, update payload with
        new_output, increment version, set status to COMPLETED, and record in store.
        """
        with self._lock:
            task = self.get_node(task_id)
            if not task:
                raise NodeNotFoundError(f"Task '{task_id}' not found.")

            now = time.time()
            task.status = NodeStatus.COMPLETED
            task.version += 1
            task.updated_at = now

            # Clear stale and conflict flags
            task.payload.pop("is_stale", None)
            task.payload.pop("stale_reason", None)
            task.payload.pop("conflict_reason", None)
            task.payload.pop("conflict", None)
            task.payload.pop("invalidated_by", None)

            # Attach resolved output and audit info
            task.payload["output"] = new_output
            task.payload["resolved_by"] = actor
            task.payload["resolved_at"] = now

            if self.store is not None:
                self.store.upsert_node(task)
                self.store.record_event(
                    event_type="CONFLICT_RESOLVED",
                    node_id=task_id,
                    actor=actor,
                    data={"version": task.version, "output": new_output},
                    idempotency_key=idempotency_key,
                )

            self.graph.nodes[task_id]["data"] = task
            return task

    def claim_task(
        self,
        task_id: str,
        agent_id: str,
        tool_name: str = "generic_tool",
        idempotency_key: Optional[str] = None,
    ) -> Node:
        """
        Claim a pending task for an agent atomically, leasing it in IN_PROGRESS.
        Rejects already claimed or completed tasks with TaskConflictError.
        Idempotent replay by the same agent returns the node.
        """
        with self._lock:
            node = self.get_node(task_id)
            if not node:
                raise NodeNotFoundError(f"Task '{task_id}' not found.")

            if self.store and idempotency_key:
                existing = self.store.check_idempotency(idempotency_key)
                if existing:
                    return node

            assigned_identifier = f"{agent_id} ({tool_name})" if tool_name != "generic_tool" else agent_id

            if node.status == NodeStatus.COMPLETED:
                raise TaskConflictError(f"Task '{task_id}' is already COMPLETED.")
            if node.status == NodeStatus.IN_PROGRESS and node.assigned_to not in (agent_id, assigned_identifier):
                raise TaskConflictError(f"Task '{task_id}' is already claimed by {node.assigned_to}.")

            node.status = NodeStatus.IN_PROGRESS
            node.assigned_to = assigned_identifier
            node.updated_at = time.time()

            if self.store is not None:
                self.store.upsert_node(node)
                self.store.record_event(
                    event_type="TASK_CLAIMED",
                    node_id=task_id,
                    actor=agent_id,
                    data={"assigned_to": assigned_identifier, "tool_name": tool_name},
                    idempotency_key=idempotency_key,
                )

            self.graph.nodes[task_id]["data"] = node
            return node

    def submit_task(
        self,
        task_id: str,
        agent_id: str,
        output: Dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> Node:
        """
        Submit output for a claimed task and mark it COMPLETED.
        Rejects tasks in STALE_CONFLICT state with TaskConflictError.
        """
        with self._lock:
            node = self.get_node(task_id)
            if not node:
                raise NodeNotFoundError(f"Task '{task_id}' not found.")

            if self.store and idempotency_key:
                existing = self.store.check_idempotency(idempotency_key)
                if existing:
                    return node

            if node.status == NodeStatus.STALE_CONFLICT:
                raise TaskConflictError(f"Cannot submit output for task '{task_id}' in STALE_CONFLICT state.")

            node.status = NodeStatus.COMPLETED
            node.assigned_to = agent_id
            node.payload["output"] = output
            node.version += 1
            node.updated_at = time.time()

            if self.store is not None:
                self.store.upsert_node(node)
                self.store.record_event(
                    event_type="TASK_SUBMITTED",
                    node_id=task_id,
                    actor=agent_id,
                    data={"output": output, "version": node.version},
                    idempotency_key=idempotency_key,
                )

            self.graph.nodes[task_id]["data"] = node
            return node

    def update_requirement(
        self,
        req_id: str,
        new_spec: str,
        actor: str = "system",
        idempotency_key: Optional[str] = None,
    ) -> Tuple[Node, List[str]]:
        """
        Update a requirement specification, bump version, persist event,
        and trigger downstream blast-radius invalidation.
        Returns (updated_requirement_node, list_of_invalidated_node_ids).
        """
        with self._lock:
            req_node = self.get_node(req_id)
            if not req_node:
                raise NodeNotFoundError(f"Requirement node '{req_id}' not found.")

            req_node.payload["spec"] = new_spec
            req_node.version += 1
            req_node.updated_at = time.time()

            if self.store is not None:
                self.store.upsert_node(req_node)
                self.store.record_event(
                    event_type="REQUIREMENT_UPDATED",
                    node_id=req_id,
                    actor=actor,
                    data={"new_spec": new_spec, "version": req_node.version},
                    idempotency_key=idempotency_key,
                )

            invalidated = self.invalidate_subtree(
                node_id=req_id,
                reason=f"Requirement spec updated to: {new_spec[:60]}",
                actor=actor,
            )
            return req_node, [n.id for n in invalidated]

    def get_ready_tasks(self) -> List[Node]:
        """
        Return all TASK nodes that are PENDING and all whose predecessors are COMPLETED.
        """
        with self._lock:
            ready: List[Node] = []
            for node in self._nodes.values():
                if node.type == NodeType.TASK and node.status == NodeStatus.PENDING:
                    preds = list(self.graph.predecessors(node.id))
                    if all(self._nodes[p].status == NodeStatus.COMPLETED for p in preds if p in self._nodes):
                        ready.append(node)
            return ready

    def get_execution_order(self) -> List[str]:
        """Return topological execution order of all nodes in the DAG."""
        with self._lock:
            return list(nx.topological_sort(self.graph))

    def get_graph_state(self) -> GraphStateResponse:
        """Return full snapshot of graph nodes, edges, and status statistics."""
        with self._lock:
            nodes = list(self._nodes.values())
            edges = self.list_edges()
            stats = {
                "total": len(nodes),
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "pending": sum(1 for n in nodes if n.status == NodeStatus.PENDING),
                "in_progress": sum(1 for n in nodes if n.status == NodeStatus.IN_PROGRESS),
                "completed": sum(1 for n in nodes if n.status == NodeStatus.COMPLETED),
                "stale_conflict": sum(1 for n in nodes if n.status == NodeStatus.STALE_CONFLICT),
                "failed": sum(1 for n in nodes if n.status == NodeStatus.FAILED),
            }
            return GraphStateResponse(
                nodes=nodes,
                edges=edges,
                stats=stats,
            )
