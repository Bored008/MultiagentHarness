"""
SQLite WAL repository for TaskMaster To-Do App.
Fulfills TSK-01 requirements and DEC-01 architecture contract.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import time
import uuid
from typing import Any, Dict, Generator, List, Optional, Union

from todo.models import (
    TodoCreate,
    TodoItem,
    TodoPriority,
    TodoStats,
    TodoStatus,
    TodoUpdate,
)


class TodoStore:
    """Thread-safe SQLite repository operating in WAL mode."""

    def __init__(self, db_path: str = "todos.db") -> None:
        self.db_path = db_path
        self._init_db()

    @contextlib.contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS todos (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    deadline TEXT NOT NULL,
                    expected_hours REAL NOT NULL,
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_todos_priority ON todos(priority);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_todos_created_at ON todos(created_at);")

    def _row_to_item(self, row: sqlite3.Row) -> TodoItem:
        return TodoItem(
            id=row["id"],
            title=row["title"],
            description=row["description"] or "",
            deadline=row["deadline"],
            expected_hours=float(row["expected_hours"]),
            status=TodoStatus(row["status"]),
            priority=TodoPriority(row["priority"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def create(self, data: Union[TodoCreate, dict], todo_id: Optional[str] = None) -> TodoItem:
        if isinstance(data, dict):
            validated = TodoCreate(**data)
        else:
            validated = data

        item_id = todo_id or f"TASK-{uuid.uuid4().hex[:6].upper()}"
        now = time.time()

        item = TodoItem(
            id=item_id,
            title=validated.title,
            description=validated.description or "",
            deadline=validated.deadline,
            expected_hours=validated.expected_hours,
            status=validated.status,
            priority=validated.priority,
            created_at=now,
            updated_at=now,
        )

        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO todos (id, title, description, deadline, expected_hours, status, priority, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    item.id,
                    item.title,
                    item.description,
                    item.deadline,
                    item.expected_hours,
                    item.status.value,
                    item.priority.value,
                    item.created_at,
                    item.updated_at,
                ),
            )
        return item

    def get(self, todo_id: str) -> Optional[TodoItem]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM todos WHERE id = ?;", (todo_id,)).fetchone()
            if not row:
                return None
            return self._row_to_item(row)

    def list_filtered(
        self,
        status: Optional[Union[str, TodoStatus]] = None,
        priority: Optional[Union[str, TodoPriority]] = None,
        q: Optional[str] = None,
    ) -> List[TodoItem]:
        query = "SELECT * FROM todos WHERE 1=1"
        params: List[Any] = []

        if status is not None:
            status_val = status.value if isinstance(status, TodoStatus) else str(status).strip().lower()
            query += " AND status = ?"
            params.append(status_val)

        if priority is not None:
            priority_val = priority.value if isinstance(priority, TodoPriority) else str(priority).strip().lower()
            query += " AND priority = ?"
            params.append(priority_val)

        if q is not None and q.strip():
            query += " AND (title LIKE ? OR description LIKE ?)"
            pattern = f"%{q.strip()}%"
            params.extend([pattern, pattern])

        query += " ORDER BY created_at DESC;"

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_item(r) for r in rows]

    def update(self, todo_id: str, update_data: Union[TodoUpdate, dict]) -> Optional[TodoItem]:
        if isinstance(update_data, dict):
            validated = TodoUpdate(**update_data)
        else:
            validated = update_data

        existing = self.get(todo_id)
        if not existing:
            return None

        update_fields: Dict[str, Any] = {}
        if validated.title is not None:
            update_fields["title"] = validated.title
        if validated.description is not None:
            update_fields["description"] = validated.description
        if validated.deadline is not None:
            update_fields["deadline"] = validated.deadline
        if validated.expected_hours is not None:
            update_fields["expected_hours"] = validated.expected_hours
        if validated.status is not None:
            update_fields["status"] = validated.status.value
        if validated.priority is not None:
            update_fields["priority"] = validated.priority.value

        if not update_fields:
            return existing

        now = time.time()
        update_fields["updated_at"] = now

        set_clause = ", ".join(f"{col} = ?" for col in update_fields.keys())
        values = list(update_fields.values()) + [todo_id]

        with self._conn() as conn:
            conn.execute(f"UPDATE todos SET {set_clause} WHERE id = ?;", values)

        return self.get(todo_id)

    def delete(self, todo_id: str) -> bool:
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM todos WHERE id = ?;", (todo_id,))
            return cursor.rowcount > 0

    def aggregate_stats(self) -> TodoStats:
        status_counts = {s.value: 0 for s in TodoStatus}
        status_hours = {s.value: 0.0 for s in TodoStatus}
        priority_counts = {p.value: 0 for p in TodoPriority}
        priority_hours = {p.value: 0.0 for p in TodoPriority}

        total_count = 0
        total_expected_hours = 0.0
        mvp_count = 0
        mvp_expected_hours = 0.0

        with self._conn() as conn:
            # Status aggregates
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt, SUM(expected_hours) as total_hrs FROM todos GROUP BY status;"
            ).fetchall()
            for r in rows:
                st = r["status"]
                cnt = int(r["cnt"])
                hrs = float(r["total_hrs"] or 0.0)
                status_counts[st] = cnt
                status_hours[st] = round(hrs, 2)
                total_count += cnt
                total_expected_hours += hrs

            # Priority aggregates
            p_rows = conn.execute(
                "SELECT priority, COUNT(*) as cnt, SUM(expected_hours) as total_hrs FROM todos GROUP BY priority;"
            ).fetchall()
            for r in p_rows:
                pr = r["priority"]
                cnt = int(r["cnt"])
                hrs = float(r["total_hrs"] or 0.0)
                priority_counts[pr] = cnt
                priority_hours[pr] = round(hrs, 2)
                if pr == TodoPriority.MVP.value:
                    mvp_count = cnt
                    mvp_expected_hours = round(hrs, 2)

        return TodoStats(
            total_count=total_count,
            total_expected_hours=round(total_expected_hours, 2),
            status_counts=status_counts,
            status_hours=status_hours,
            priority_counts=priority_counts,
            priority_hours=priority_hours,
            mvp_count=mvp_count,
            mvp_expected_hours=round(mvp_expected_hours, 2),
        )

    def clear(self) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM todos;")
