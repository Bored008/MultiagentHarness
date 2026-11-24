"""
Unit tests for TaskMaster To-Do domain models and SQLite WAL store (TSK-01).
"""
import os
import tempfile
import pytest
from pydantic import ValidationError

from todo.models import (
    TodoCreate,
    TodoItem,
    TodoPriority,
    TodoStats,
    TodoStatus,
    TodoUpdate,
)
from todo.store import TodoStore


def test_todo_create_validation():
    # Valid model
    item = TodoCreate(
        title="Valid Task",
        description="A good description",
        deadline="2026-12-31T23:59:59Z",
        expected_hours=3.5,
        status=TodoStatus.PENDING,
        priority=TodoPriority.MVP,
    )
    assert item.title == "Valid Task"
    assert item.expected_hours == 3.5
    assert item.status == TodoStatus.PENDING
    assert item.priority == TodoPriority.MVP


def test_todo_create_rejects_empty_title():
    with pytest.raises(ValidationError):
        TodoCreate(
            title="",
            deadline="2026-12-31T23:59:59Z",
            expected_hours=2.0,
        )

    with pytest.raises(ValidationError):
        TodoCreate(
            title="   \t\n  ",
            deadline="2026-12-31T23:59:59Z",
            expected_hours=2.0,
        )


def test_todo_create_rejects_non_positive_hours():
    with pytest.raises(ValidationError):
        TodoCreate(
            title="Zero Hours Task",
            deadline="2026-12-31T23:59:59Z",
            expected_hours=0.0,
        )

    with pytest.raises(ValidationError):
        TodoCreate(
            title="Negative Hours Task",
            deadline="2026-12-31T23:59:59Z",
            expected_hours=-2.5,
        )


def test_todo_create_rejects_invalid_deadline():
    with pytest.raises(ValidationError):
        TodoCreate(
            title="Bad Deadline Task",
            deadline="tomorrow at 5pm",
            expected_hours=1.0,
        )


def test_todo_store_persistence_wal_mode():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_todos.db")
        store1 = TodoStore(db_path=db_path)

        created = store1.create(
            TodoCreate(
                title="Persistent Task",
                description="Testing SQLite WAL persistence",
                deadline="2026-11-30T18:00:00Z",
                expected_hours=4.0,
                status=TodoStatus.WORKING,
                priority=TodoPriority.MVP,
            ),
            todo_id="TASK-001",
        )
        assert created.id == "TASK-001"

        # Reopen with new store instance
        store2 = TodoStore(db_path=db_path)
        retrieved = store2.get("TASK-001")
        assert retrieved is not None
        assert retrieved.title == "Persistent Task"
        assert retrieved.status == TodoStatus.WORKING
        assert retrieved.priority == TodoPriority.MVP
        assert retrieved.expected_hours == 4.0


def test_todo_store_filtering_and_search():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_todos.db")
        store = TodoStore(db_path=db_path)

        store.create(
            TodoCreate(
                title="Frontend Auth Widget",
                description="Build login form with JWT",
                deadline="2026-12-01T12:00:00Z",
                expected_hours=3.0,
                status=TodoStatus.PENDING,
                priority=TodoPriority.HIGH,
            ),
            todo_id="T1",
        )
        store.create(
            TodoCreate(
                title="Backend Auth Route",
                description="FastAPI login endpoints",
                deadline="2026-12-02T12:00:00Z",
                expected_hours=5.0,
                status=TodoStatus.COMPLETED,
                priority=TodoPriority.MVP,
            ),
            todo_id="T2",
        )
        store.create(
            TodoCreate(
                title="Database Migrations",
                description="Alembic schema setup",
                deadline="2026-12-03T12:00:00Z",
                expected_hours=2.0,
                status=TodoStatus.WORKING,
                priority=TodoPriority.LOW,
            ),
            todo_id="T3",
        )

        # Filter by status
        pending = store.list_filtered(status="pending")
        assert len(pending) == 1
        assert pending[0].id == "T1"

        # Filter by priority
        mvp = store.list_filtered(priority="mvp")
        assert len(mvp) == 1
        assert mvp[0].id == "T2"

        # Search query matching title or description
        auth_items = store.list_filtered(q="Auth")
        assert len(auth_items) == 2
        assert {item.id for item in auth_items} == {"T1", "T2"}

        desc_match = store.list_filtered(q="Alembic")
        assert len(desc_match) == 1
        assert desc_match[0].id == "T3"


def test_todo_store_aggregate_stats():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_todos.db")
        store = TodoStore(db_path=db_path)

        store.create(
            TodoCreate(
                title="Task 1",
                deadline="2026-12-01T00:00:00Z",
                expected_hours=2.5,
                status=TodoStatus.PENDING,
                priority=TodoPriority.MVP,
            )
        )
        store.create(
            TodoCreate(
                title="Task 2",
                deadline="2026-12-02T00:00:00Z",
                expected_hours=3.5,
                status=TodoStatus.PENDING,
                priority=TodoPriority.MEDIUM,
            )
        )
        store.create(
            TodoCreate(
                title="Task 3",
                deadline="2026-12-03T00:00:00Z",
                expected_hours=4.0,
                status=TodoStatus.COMPLETED,
                priority=TodoPriority.MVP,
            )
        )

        stats = store.aggregate_stats()
        assert stats.total_count == 3
        assert stats.total_expected_hours == 10.0
        assert stats.status_counts["pending"] == 2
        assert stats.status_counts["completed"] == 1
        assert stats.status_counts["working"] == 0
        assert stats.status_hours["pending"] == 6.0
        assert stats.status_hours["completed"] == 4.0
        assert stats.mvp_count == 2
        assert stats.mvp_expected_hours == 6.5


def test_todo_store_update_and_delete():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_todos.db")
        store = TodoStore(db_path=db_path)

        item = store.create(
            TodoCreate(
                title="Initial Task",
                deadline="2026-12-01T00:00:00Z",
                expected_hours=1.0,
                status=TodoStatus.PENDING,
                priority=TodoPriority.LOW,
            ),
            todo_id="TASK-UPD",
        )

        # Update status and title
        updated = store.update(
            "TASK-UPD",
            TodoUpdate(
                title="Updated Task Title",
                status=TodoStatus.COMPLETED,
                expected_hours=2.5,
            ),
        )
        assert updated is not None
        assert updated.title == "Updated Task Title"
        assert updated.status == TodoStatus.COMPLETED
        assert updated.expected_hours == 2.5
        assert updated.updated_at >= item.updated_at

        # Delete
        deleted = store.delete("TASK-UPD")
        assert deleted is True
        assert store.get("TASK-UPD") is None
