"""
FastAPI router for TaskMaster To-Do App.
Fulfills TSK-02 requirements and DEC-01 architecture contract.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from todo.models import (
    TodoCreate,
    TodoItem,
    TodoListResponse,
    TodoPriority,
    TodoStats,
    TodoStatus,
    TodoUpdate,
)
from todo.store import TodoStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/todos", tags=["todos"])

_fallback_store: Optional[TodoStore] = None


def get_todo_store(request: Request) -> TodoStore:
    """Dependency resolver for TodoStore from FastAPI app state or fallback singleton."""
    global _fallback_store
    if hasattr(request.app.state, "todo_store") and request.app.state.todo_store is not None:
        return request.app.state.todo_store
    if _fallback_store is None:
        _fallback_store = TodoStore()
    return _fallback_store


async def _broadcast_ws(request: Request, event_type: str, data: Dict[str, Any]) -> None:
    """Broadcast real-time to-do change events to connected WebSocket clients."""
    ws_manager = getattr(request.app.state, "ws_manager", None)
    if ws_manager and hasattr(ws_manager, "broadcast"):
        payload = {
            "event": event_type,
            "event_type": event_type,
            "type": event_type,
            "timestamp": time.time(),
            **data,
        }
        try:
            await ws_manager.broadcast(payload)
        except Exception as e:
            logger.warning("Failed to broadcast WebSocket event %s: %s", event_type, e)


@router.get("", response_model=TodoListResponse)
@router.get("/", response_model=TodoListResponse)
async def list_todos(
    status: Optional[str] = Query(None, description="Filter by status (pending, working, incomplete, completed)"),
    priority: Optional[str] = Query(None, description="Filter by priority (low, medium, high, mvp)"),
    q: Optional[str] = Query(None, description="Search query string matching title or description"),
    store: TodoStore = Depends(get_todo_store),
):
    """
    List to-do items filtered by status, priority, or search query.
    Returns filtered items alongside aggregate metrics for all tasks.
    """
    items = store.list_filtered(status=status, priority=priority, q=q)
    stats = store.aggregate_stats()
    return TodoListResponse(items=items, stats=stats)


@router.get("/stats", response_model=TodoStats)
async def get_todo_stats(store: TodoStore = Depends(get_todo_store)):
    """Return aggregated telemetry statistics."""
    return store.aggregate_stats()


@router.post("", response_model=TodoItem, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=TodoItem, status_code=status.HTTP_201_CREATED)
async def create_todo(
    payload: TodoCreate,
    request: Request,
    store: TodoStore = Depends(get_todo_store),
):
    """
    Create a new validated To-Do item.
    Broadcasts TODO_CREATED event over WebSocket.
    """
    item = store.create(payload)
    await _broadcast_ws(request, "TODO_CREATED", {"todo": item.model_dump()})
    return item


@router.get("/{todo_id}", response_model=TodoItem)
async def get_todo(
    todo_id: str,
    store: TodoStore = Depends(get_todo_store),
):
    """Retrieve a single To-Do item by ID."""
    item = store.get(todo_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"To-Do item '{todo_id}' not found.",
        )
    return item


@router.patch("/{todo_id}", response_model=TodoItem)
async def update_todo(
    todo_id: str,
    payload: TodoUpdate,
    request: Request,
    store: TodoStore = Depends(get_todo_store),
):
    """
    Partially update a To-Do item.
    Updates updated_at deterministically and broadcasts TODO_UPDATED event over WebSocket.
    """
    updated = store.update(todo_id, payload)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"To-Do item '{todo_id}' not found.",
        )
    await _broadcast_ws(request, "TODO_UPDATED", {"todo": updated.model_dump()})
    return updated


@router.delete("/{todo_id}")
async def delete_todo(
    todo_id: str,
    request: Request,
    store: TodoStore = Depends(get_todo_store),
):
    """
    Delete a To-Do item by ID.
    Broadcasts TODO_DELETED event over WebSocket.
    """
    deleted = store.delete(todo_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"To-Do item '{todo_id}' not found.",
        )
    await _broadcast_ws(request, "TODO_DELETED", {"todo_id": todo_id})
    return {"status": "deleted", "id": todo_id}
