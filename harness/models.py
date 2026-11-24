"""
Data models and schemas for OmniHarness.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class NodeType(str, Enum):
    REQUIREMENT = "REQUIREMENT"
    DECISION = "DECISION"
    TASK = "TASK"
    ARTIFACT = "ARTIFACT"


class NodeStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    STALE_CONFLICT = "STALE_CONFLICT"
    FAILED = "FAILED"
    INVALIDATED = "STALE_CONFLICT"
    NEEDS_RECHECK = "STALE_CONFLICT"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            val_upper = value.upper()
            if val_upper in ("INVALIDATED", "NEEDS_RECHECK"):
                return cls.STALE_CONFLICT
        return super()._missing_(value)


class Node(BaseModel):
    id: str
    title: str
    type: NodeType
    status: NodeStatus = NodeStatus.PENDING
    assigned_to: Optional[str] = None
    version: int = 1
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class Edge(BaseModel):
    source: str
    target: str
    relation: str = "relies_on"


class Event(BaseModel):
    event_id: str
    idempotency_key: str
    event_type: str
    node_id: str
    actor: str
    data: Dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)


class ClaimTaskRequest(BaseModel):
    task_id: Optional[str] = None
    agent_id: str
    tool_name: str = "generic_tool"
    idempotency_key: str


class SubmitTaskRequest(BaseModel):
    task_id: Optional[str] = None
    agent_id: str
    idempotency_key: str
    output: Dict[str, Any] = Field(default_factory=dict)


class CreateNodeRequest(BaseModel):
    id: str
    title: str
    type: NodeType
    status: Optional[NodeStatus] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    dependencies: List[str] = Field(default_factory=list)
    idempotency_key: Optional[str] = None
    actor: str = "system"


class SeedGraphRequest(BaseModel):
    template: str = "tictactoe"
    reset: bool = True


class UpdateRequirementRequest(BaseModel):
    req_id: str
    new_spec: str
    actor: str
    idempotency_key: str


class SubgraphResponse(BaseModel):
    task: Node
    ancestors: List[Node]
    edges: List[Edge]
    is_stale: bool = False
    conflict_details: Optional[str] = None


class GraphStateResponse(BaseModel):
    nodes: List[Node]
    edges: List[Edge]
    stats: Dict[str, int]
