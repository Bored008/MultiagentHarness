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


class BoardStage(str, Enum):
    BUILDING = "building"
    BUILT = "built"


class BoardRole(str, Enum):
    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


class MemberStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REVOKED = "revoked"


class Node(BaseModel):
    id: str
    title: str
    type: NodeType
    status: NodeStatus = NodeStatus.PENDING
    assigned_to: Optional[str] = None
    version: int = 1
    payload: Dict[str, Any] = Field(default_factory=dict)
    board_id: str = "default"
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class Edge(BaseModel):
    source: str
    target: str
    relation: str = "relies_on"
    board_id: str = "default"


class Event(BaseModel):
    event_id: str
    idempotency_key: str
    event_type: str
    node_id: str
    actor: str
    data: Dict[str, Any] = Field(default_factory=dict)
    board_id: str = "default"
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


class WorkspaceUser(BaseModel):
    user_id: str
    display_name: str
    tool_name: str = "generic_tool"
    client_host: Optional[str] = None
    status: str = "joined"
    last_task_id: Optional[str] = None
    active_tasks: List[str] = Field(default_factory=list)
    completed_tasks: List[str] = Field(default_factory=list)
    board_id: str = "default"
    joined_at: float = Field(default_factory=time.time)
    last_seen: float = Field(default_factory=time.time)


class JoinWorkspaceRequest(BaseModel):
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    display_name: Optional[str] = None
    tool_name: str = "generic_tool"
    client_host: Optional[str] = None


class GraphStateResponse(BaseModel):
    nodes: List[Node]
    edges: List[Edge]
    stats: Dict[str, int]
    users: List[WorkspaceUser] = Field(default_factory=list)
    board_id: str = "default"


class GraphSnapshot(BaseModel):
    """Portable snapshot of DAG nodes, dependency edges, and full event history."""

    version: int = 1
    board_id: str = "default"
    exported_at: float = Field(default_factory=time.time)
    nodes: List[Node] = Field(default_factory=list)
    edges: List[Edge] = Field(default_factory=list)
    events: List[Event] = Field(default_factory=list)
    users: List[WorkspaceUser] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Multi-Board, Friend Access Control & Persistent Agent Keys
# ---------------------------------------------------------------------------

class Board(BaseModel):
    id: str
    slug: str
    title: str
    description: str = ""
    stage: BoardStage = BoardStage.BUILDING
    owner_id: str
    owner_email: Optional[str] = None
    role: Optional[BoardRole] = None
    node_count: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class CreateBoardRequest(BaseModel):
    title: str
    slug: Optional[str] = None
    description: str = ""
    stage: BoardStage = BoardStage.BUILDING
    seed_template: Optional[str] = None


class UpdateBoardRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    stage: Optional[BoardStage] = None


class BoardMember(BaseModel):
    board_id: str
    user_id: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    role: BoardRole = BoardRole.EDITOR
    status: MemberStatus = MemberStatus.PENDING
    invited_by: Optional[str] = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class BoardInvite(BaseModel):
    code: str
    board_id: str
    role: BoardRole = BoardRole.EDITOR
    require_approval: bool = True
    created_by: str
    created_at: float = Field(default_factory=time.time)
    revoked_at: Optional[float] = None


class CreateInviteRequest(BaseModel):
    role: BoardRole = BoardRole.EDITOR
    require_approval: bool = True


class JoinBoardRequest(BaseModel):
    invite_code: str
    display_name: Optional[str] = None


class UpdateMemberRequest(BaseModel):
    role: Optional[BoardRole] = None
    status: Optional[MemberStatus] = None


class BoardAgentKey(BaseModel):
    id: str
    board_id: str
    name: str
    key_prefix: str
    created_by: str
    created_by_email: Optional[str] = None
    scopes: List[str] = Field(default_factory=lambda: ["tasks:read", "tasks:claim", "tasks:submit", "nodes:create"])
    revoked_at: Optional[float] = None
    last_used_at: Optional[float] = None
    created_at: float = Field(default_factory=time.time)


class CreateAgentKeyRequest(BaseModel):
    name: str = "synapse-agent"
    scopes: List[str] = Field(default_factory=lambda: ["tasks:read", "tasks:claim", "tasks:submit", "nodes:create"])


class CreateAgentKeyResponse(BaseModel):
    key: BoardAgentKey
    api_key: str
