"""
Pydantic v2 domain schemas and enums for TaskMaster To-Do App.
Fulfills TSK-01 requirements and DEC-01 architecture contract.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class TodoStatus(str, Enum):
    PENDING = "pending"
    WORKING = "working"
    INCOMPLETE = "incomplete"
    COMPLETED = "completed"


class TodoPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MVP = "mvp"


# Color mappings per REQ-01 / DEC-01 specification
STATUS_COLORS: Dict[TodoStatus, Dict[str, str]] = {
    TodoStatus.PENDING: {"label": "Pending", "hex": "#EAB308", "bg_class": "bg-yellow-500/20", "text_class": "text-yellow-400", "border_class": "border-yellow-500/40"},
    TodoStatus.WORKING: {"label": "Working", "hex": "#F97316", "bg_class": "bg-orange-500/20", "text_class": "text-orange-400", "border_class": "border-orange-500/40"},
    TodoStatus.INCOMPLETE: {"label": "Incomplete", "hex": "#EF4444", "bg_class": "bg-red-500/20", "text_class": "text-red-400", "border_class": "border-red-500/40"},
    TodoStatus.COMPLETED: {"label": "Completed", "hex": "#22C55E", "bg_class": "bg-emerald-500/20", "text_class": "text-emerald-400", "border_class": "border-emerald-500/40"},
}

PRIORITY_COLORS: Dict[TodoPriority, Dict[str, str]] = {
    TodoPriority.LOW: {"label": "Low", "hex": "#3B82F6", "bg_class": "bg-blue-500/20", "text_class": "text-blue-400", "border_class": "border-blue-500/40"},
    TodoPriority.MEDIUM: {"label": "Medium", "hex": "#F59E0B", "bg_class": "bg-amber-500/20", "text_class": "text-amber-400", "border_class": "border-amber-500/40"},
    TodoPriority.HIGH: {"label": "High", "hex": "#E11D48", "bg_class": "bg-rose-500/20", "text_class": "text-rose-400", "border_class": "border-rose-500/40"},
    TodoPriority.MVP: {"label": "MVP", "hex": "#A855F7", "bg_class": "bg-purple-500/20", "text_class": "text-purple-400", "border_class": "border-purple-500/50", "neon": True},
}


class TodoCreate(BaseModel):
    title: str = Field(..., description="Task title, non-empty")
    description: Optional[str] = Field(default="", description="Detailed task description")
    deadline: str = Field(..., description="ISO-8601 deadline timestamp")
    expected_hours: float = Field(..., gt=0, description="Estimated work hours (must be > 0)")
    status: TodoStatus = Field(default=TodoStatus.PENDING, description="Current workflow state")
    priority: TodoPriority = Field(default=TodoPriority.MEDIUM, description="Priority tier")

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Title must be a non-empty string.")
        return v.strip()

    @field_validator("deadline")
    @classmethod
    def validate_deadline(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Deadline must be a non-empty ISO-8601 formatted string.")
        try:
            iso_str = v.strip().replace("Z", "+00:00")
            datetime.fromisoformat(iso_str)
        except Exception as e:
            raise ValueError(f"Invalid ISO-8601 deadline format: {v}") from e
        return v.strip()

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("priority", mode="before")
    @classmethod
    def normalize_priority(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip().lower()
        return v


class TodoUpdate(BaseModel):
    title: Optional[str] = Field(default=None, description="Updated task title")
    description: Optional[str] = Field(default=None, description="Updated task description")
    deadline: Optional[str] = Field(default=None, description="Updated ISO-8601 deadline timestamp")
    expected_hours: Optional[float] = Field(default=None, gt=0, description="Updated work hours (must be > 0)")
    status: Optional[TodoStatus] = Field(default=None, description="Updated workflow state")
    priority: Optional[TodoPriority] = Field(default=None, description="Updated priority tier")

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if not isinstance(v, str) or not v.strip():
                raise ValueError("Title must be a non-empty string.")
            return v.strip()
        return v

    @field_validator("deadline")
    @classmethod
    def validate_deadline(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if not isinstance(v, str) or not v.strip():
                raise ValueError("Deadline must be a non-empty ISO-8601 formatted string.")
            try:
                iso_str = v.strip().replace("Z", "+00:00")
                datetime.fromisoformat(iso_str)
            except Exception as e:
                raise ValueError(f"Invalid ISO-8601 deadline format: {v}") from e
            return v.strip()
        return v

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("priority", mode="before")
    @classmethod
    def normalize_priority(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip().lower()
        return v


class TodoItem(BaseModel):
    id: str = Field(..., description="Unique task identifier")
    title: str = Field(..., description="Task title")
    description: str = Field(default="", description="Detailed task description")
    deadline: str = Field(..., description="ISO-8601 deadline timestamp")
    expected_hours: float = Field(..., gt=0, description="Estimated work hours")
    status: TodoStatus = Field(default=TodoStatus.PENDING, description="Current workflow state")
    priority: TodoPriority = Field(default=TodoPriority.MEDIUM, description="Priority tier")
    created_at: float = Field(..., description="Creation epoch timestamp (seconds)")
    updated_at: float = Field(..., description="Last update epoch timestamp (seconds)")


class TodoStats(BaseModel):
    total_count: int = Field(default=0, description="Total count of tasks")
    total_expected_hours: float = Field(default=0.0, description="Total estimated work hours")
    status_counts: Dict[str, int] = Field(default_factory=dict, description="Task counts partitioned by status")
    status_hours: Dict[str, float] = Field(default_factory=dict, description="Estimated hours partitioned by status")
    priority_counts: Dict[str, int] = Field(default_factory=dict, description="Task counts partitioned by priority")
    priority_hours: Dict[str, float] = Field(default_factory=dict, description="Estimated hours partitioned by priority")
    mvp_count: int = Field(default=0, description="Count of MVP tasks")
    mvp_expected_hours: float = Field(default=0.0, description="Estimated hours for MVP tasks")


class TodoListResponse(BaseModel):
    items: List[TodoItem] = Field(default_factory=list, description="List of tasks matching query")
    stats: TodoStats = Field(default_factory=TodoStats, description="Aggregated telemetry statistics")
