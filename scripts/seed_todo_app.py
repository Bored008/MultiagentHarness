"""
Seed the new To-Do Application requirements, architectural decisions, and tasks
into OmniHarness on https://jewel-untitled-respective-devel.trycloudflare.com
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
import time

SERVER_URL = "https://jewel-untitled-respective-devel.trycloudflare.com"

TODO_PYDANTIC_CODE = '''"""
Pydantic Schemas for To-Do Application.
Features:
- Task title, deadline, expected hours
- Status: pending (yellow), working (orange), incomplete (red), completed (green)
- Priority: low, medium, high, and mvp (extreme priority)
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(str, Enum):
    PENDING = "pending"        # Yellow
    WORKING = "working"        # Orange
    INCOMPLETE = "incomplete"  # Red
    COMPLETED = "completed"    # Green


class TaskPriority(str, Enum):
    LOW = "low"                # Blue
    MEDIUM = "medium"          # Amber
    HIGH = "high"              # Rose / Red
    MVP = "mvp"                # Extreme / Flaming Purple Neon


class ColorTheme(BaseModel):
    text_color: str
    bg_color: str
    border_color: str
    hex_code: str
    badge_class: str


STATUS_COLOR_MAP: Dict[TaskStatus, ColorTheme] = {
    TaskStatus.PENDING: ColorTheme(
        text_color="text-yellow-400",
        bg_color="bg-yellow-500/20",
        border_color="border-yellow-500/50",
        hex_code="#EAB308",
        badge_class="text-yellow-400 bg-yellow-500/20 border-yellow-500/50"
    ),
    TaskStatus.WORKING: ColorTheme(
        text_color="text-orange-400",
        bg_color="bg-orange-500/20",
        border_color="border-orange-500/50",
        hex_code="#F97316",
        badge_class="text-orange-400 bg-orange-500/20 border-orange-500/50"
    ),
    TaskStatus.INCOMPLETE: ColorTheme(
        text_color="text-red-400",
        bg_color="bg-red-500/20",
        border_color="border-red-500/50",
        hex_code="#EF4444",
        badge_class="text-red-400 bg-red-500/20 border-red-500/50"
    ),
    TaskStatus.COMPLETED: ColorTheme(
        text_color="text-green-400",
        bg_color="bg-green-500/20",
        border_color="border-green-500/50",
        hex_code="#22C55E",
        badge_class="text-green-400 bg-green-500/20 border-green-500/50"
    ),
}

PRIORITY_COLOR_MAP: Dict[TaskPriority, ColorTheme] = {
    TaskPriority.LOW: ColorTheme(
        text_color="text-blue-400",
        bg_color="bg-blue-500/20",
        border_color="border-blue-500/40",
        hex_code="#3B82F6",
        badge_class="text-blue-400 bg-blue-500/20 border-blue-500/40"
    ),
    TaskPriority.MEDIUM: ColorTheme(
        text_color="text-amber-400",
        bg_color="bg-amber-500/20",
        border_color="border-amber-500/40",
        hex_code="#F59E0B",
        badge_class="text-amber-400 bg-amber-500/20 border-amber-500/40"
    ),
    TaskPriority.HIGH: ColorTheme(
        text_color="text-rose-400",
        bg_color="bg-rose-500/20",
        border_color="border-rose-500/50",
        hex_code="#E11D48",
        badge_class="text-rose-400 bg-rose-500/20 border-rose-500/50 font-bold"
    ),
    TaskPriority.MVP: ColorTheme(
        text_color="text-purple-300",
        bg_color="bg-purple-600/30",
        border_color="border-purple-400",
        hex_code="#A855F7",
        badge_class="text-purple-300 bg-purple-600/30 border-purple-400 shadow-md shadow-purple-500/50 font-black animate-pulse"
    ),
}


class TodoTask(BaseModel):
    id: str = Field(default_factory=lambda: f"TASK-{uuid.uuid4().hex[:6].upper()}")
    title: str = Field(..., min_length=1, max_length=255, description="Task title")
    description: Optional[str] = Field(default="", description="Detailed notes")
    deadline: str = Field(..., description="Target completion ISO-8601 deadline (e.g. 2026-11-28 18:00)")
    expected_hours: float = Field(..., gt=0, description="Approximate estimated hours")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="pending, working, incomplete, completed")
    priority: TaskPriority = Field(default=TaskPriority.MEDIUM, description="low, medium, high, mvp")
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
'''


def post_json(endpoint: str, data: dict) -> dict:
    url = f"{SERVER_URL}{endpoint}"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    print(f"Connecting to OmniHarness at {SERVER_URL}...")

    # 1. Post REQ-01 (To-Do App Requirements)
    req_node = {
        "id": "REQ-01",
        "title": "To-Do App Specification & Color Matrix",
        "type": "REQUIREMENT",
        "payload": {
            "app_name": "TaskMaster To-Do App",
            "required_fields": ["title", "deadline", "expected_hours", "status", "priority"],
            "status_color_rules": {
                "pending": "yellow (#EAB308)",
                "working": "orange (#F97316)",
                "incomplete": "red (#EF4444)",
                "completed": "green (#22C55E)"
            },
            "priority_color_rules": {
                "low": "blue (#3B82F6)",
                "medium": "amber (#F59E0B)",
                "high": "rose red (#E11D48)",
                "mvp": "extreme purple neon (#A855F7, glowing badge)"
            },
            "summary_features": [
                "Real-time hours tally (total expected hours vs completed hours)",
                "Live deadline countdown and overdue alerts",
                "MVP task filter with high-contrast visual elevation"
            ]
        },
        "dependencies": [],
        "actor": "product-architect",
        "idempotency_key": "seed-todo-req-01"
    }
    r = post_json("/api/nodes", req_node)
    print(f"  ✔ Created [{r.get('type')}] {r.get('id')}: {r.get('title')}")

    # 2. Post DEC-01 (Architecture Decision)
    dec_node = {
        "id": "DEC-01",
        "title": "To-Do App UI & Color Design System Decision",
        "type": "DECISION",
        "payload": {
            "decision": "Single-page responsive To-Do dashboard with strict status/priority color-coding and MVP extreme priority tier",
            "status_taxonomy": {
                "pending": {"color": "yellow", "hex": "#eab308", "label": "Pending", "class": "bg-yellow-500/20 text-yellow-400 border-yellow-500/50"},
                "working": {"color": "orange", "hex": "#f97316", "label": "Working", "class": "bg-orange-500/20 text-orange-400 border-orange-500/50"},
                "incomplete": {"color": "red", "hex": "#ef4444", "label": "Incomplete", "class": "bg-red-500/20 text-red-400 border-red-500/50"},
                "completed": {"color": "green", "hex": "#22c55e", "label": "Completed", "class": "bg-green-500/20 text-green-400 border-green-500/50"}
            },
            "priority_taxonomy": {
                "low": {"color": "blue", "hex": "#3b82f6", "label": "Low"},
                "medium": {"color": "amber", "hex": "#f59e0b", "label": "Medium"},
                "high": {"color": "rose", "hex": "#e11d48", "label": "High"},
                "mvp": {"color": "extreme_purple", "hex": "#a855f7", "label": "MVP (Extreme)", "style": "neon_glow"}
            },
            "architecture": {
                "frontend": "Tailwind CSS + HTML5 + Vanilla JS / LocalStorage",
                "backend": "FastAPI + Pydantic v2",
                "storage": "Local In-Browser Storage + REST API Sync"
            }
        },
        "dependencies": ["REQ-01"],
        "actor": "antigravity-agent",
        "idempotency_key": "seed-todo-dec-01"
    }
    r = post_json("/api/nodes", dec_node)
    print(f"  ✔ Created [{r.get('type')}] {r.get('id')}: {r.get('title')}")

    # 3. Post TSK-01 (Pydantic Schema & Color Mapping Task)
    tsk01_node = {
        "id": "TSK-01",
        "title": "Define Pydantic Task Schemas & Color Taxonomy",
        "type": "TASK",
        "payload": {
            "instructions": "Define Pydantic v2 data models for TodoTask, TaskStatus, TaskPriority, and color mapping",
            "output": {
                "pydantic_code": TODO_PYDANTIC_CODE,
                "status": "APPROVED",
                "summary": "Pydantic v2 schema for Todo tasks created with all 4 status colors and MVP extreme priority"
            }
        },
        "dependencies": ["DEC-01"],
        "actor": "antigravity-agent",
        "idempotency_key": "seed-todo-tsk-01"
    }
    r = post_json("/api/nodes", tsk01_node)
    print(f"  ✔ Created [{r.get('type')}] {r.get('id')}: {r.get('title')}")

    # Claim & Submit TSK-01
    post_json(f"/api/tasks/TSK-01/claim", {
        "task_id": "TSK-01",
        "agent_id": "antigravity-agent",
        "tool_name": "antigravity-agent",
        "idempotency_key": "claim-todo-tsk01"
    })
    post_json(f"/api/tasks/TSK-01/submit", {
        "task_id": "TSK-01",
        "agent_id": "antigravity-agent",
        "idempotency_key": "submit-todo-tsk01",
        "output": {
            "pydantic_code": TODO_PYDANTIC_CODE,
            "status": "COMPLETED",
            "summary": "Pydantic models with TaskStatus, TaskPriority, and color maps compiled."
        }
    })
    print(f"  ✔ TSK-01 submitted and marked COMPLETED!")

    # 4. Post TSK-02 (Interactive Site Implementation)
    tsk02_node = {
        "id": "TSK-02",
        "title": "Build Responsive To-Do Site with Color Badges",
        "type": "TASK",
        "payload": {
            "instructions": "Implement single-page modern To-Do app with status & priority color badges, expected hours calculator, and deadline tracking.",
            "target": "todo/index.html",
            "dependencies_satisfied": True
        },
        "dependencies": ["TSK-01"],
        "actor": "antigravity-agent",
        "idempotency_key": "seed-todo-tsk-02"
    }
    r = post_json("/api/nodes", tsk02_node)
    print(f"  ✔ Created [{r.get('type')}] {r.get('id')}: {r.get('title')}")

    # 5. Post TSK-03 (Multi-Laptop Sync & MVP Extreme Filter)
    tsk03_node = {
        "id": "TSK-03",
        "title": "MVP Extreme Priority Filter & Multi-Laptop Sync",
        "type": "TASK",
        "payload": {
            "instructions": "Add MVP extreme priority filter and WebSocket real-time sync for remote laptop task sharing.",
            "assigned_tool": "Laptop 2 (Codex / Antigravity / Claude Code)"
        },
        "dependencies": ["DEC-01"],
        "actor": "system",
        "idempotency_key": "seed-todo-tsk-03"
    }
    r = post_json("/api/nodes", tsk03_node)
    print(f"  ✔ Created [{r.get('type')}] {r.get('id')}: {r.get('title')}")

    print("\n🎉 To-Do App Plan and Decisions successfully posted into team harness!")
    print(f"Open live dashboard: {SERVER_URL}/dashboard")


if __name__ == "__main__":
    main()
