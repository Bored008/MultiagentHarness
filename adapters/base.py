"""
OmniHarness Base Agent Adapter.
Provides standard HTTP operations and real-time WebSocket conflict listening
for heterogeneous AI agents (Claude Code, OpenAI Codex / Cursor, Antigravity).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional
import httpx
import websockets
import websockets.sync.client

logger = logging.getLogger("omniharness.adapter")


class ConflictListener:
    """
    Background worker listening for EVENT_CONFLICT_DETECTED on OmniHarness WebSocket.
    Runs synchronously inside a daemon thread for non-blocking execution across sync/async code.
    """

    def __init__(self, ws_url: str, callback: Callable[[Dict[str, Any]], Any]):
        self.ws_url = ws_url
        self.callback = callback
        self.stop_event = threading.Event()
        self.connected_event = threading.Event()
        self.is_connected = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> ConflictListener:
        if self._thread is not None and self._thread.is_alive():
            return self
        self.stop_event.clear()
        self.connected_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="OmniHarness-ConflictListener")
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                with websockets.sync.client.connect(self.ws_url) as ws:
                    self.is_connected = True
                    self.connected_event.set()
                    while not self.stop_event.is_set():
                        try:
                            message = ws.recv(timeout=0.5)
                            if isinstance(message, bytes):
                                message = message.decode("utf-8")
                            try:
                                data = json.loads(message)
                            except Exception:
                                continue

                            event_type = (
                                data.get("event_type")
                                or data.get("type")
                                or data.get("event")
                            )
                            if event_type == "EVENT_CONFLICT_DETECTED":
                                try:
                                    self.callback(data)
                                except Exception as cb_err:
                                    logger.error(f"Error in conflict callback: {cb_err}")
                        except TimeoutError:
                            continue
                        except Exception:
                            # Connection lost or closed
                            break
            except Exception:
                if self.stop_event.is_set():
                    break
                # Short backoff before reconnect
                time.sleep(0.3)
            finally:
                self.is_connected = False

    def stop(self, timeout: float = 2.0) -> None:
        self.stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait_connected(self, timeout: float = 3.0) -> bool:
        return self.connected_event.wait(timeout=timeout)


class BaseAgentAdapter:
    """
    Base Agent Adapter for OmniHarness.
    Handles task leasing, scoped context retrieval, submission, and conflict alerts.
    """

    def __init__(
        self,
        agent_id: str,
        server_url: str = "http://localhost:8000",
        ws_url: Optional[str] = None,
        client: Optional[httpx.Client] = None,
    ):
        self.agent_id = agent_id
        self.server_url = server_url.rstrip("/")

        if ws_url:
            self.ws_url = ws_url
        else:
            base = self.server_url
            if base.startswith("http://"):
                self.ws_url = "ws://" + base[7:] + "/ws/graph"
            elif base.startswith("https://"):
                self.ws_url = "wss://" + base[8:] + "/ws/graph"
            else:
                self.ws_url = f"ws://{base}/ws/graph"

        self._custom_client = client is not None
        self._client = client or httpx.Client(base_url=self.server_url, timeout=30.0)
        self._listeners: List[ConflictListener] = []

    @property
    def client(self) -> httpx.Client:
        return self._client

    def claim_task(
        self, task_id: str, idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Claim a task lease for this agent.
        Sends idempotency_key to ensure deterministic replay if retried.
        """
        key = idempotency_key or f"claim-{self.agent_id}-{task_id}-{uuid.uuid4().hex[:8]}"
        payload = {
            "task_id": task_id,
            "agent_id": self.agent_id,
            "idempotency_key": key,
        }
        res = self._client.post(f"/api/tasks/{task_id}/claim", json=payload)
        res.raise_for_status()
        return res.json()

    def get_subgraph(self, task_id: str) -> Dict[str, Any]:
        """
        Retrieve scoped ~1.5k token subgraph (ancestors, specs, conflict status)
        for zero-copy context handoff without full chat history.
        """
        res = self._client.get(f"/api/tasks/{task_id}/subgraph")
        res.raise_for_status()
        return res.json()

    def submit_result(
        self,
        task_id: str,
        output: Dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Submit completed work or artifact for a task.
        """
        key = idempotency_key or f"submit-{self.agent_id}-{task_id}-{uuid.uuid4().hex[:8]}"
        payload = {
            "task_id": task_id,
            "agent_id": self.agent_id,
            "idempotency_key": key,
            "output": output,
        }
        res = self._client.post(f"/api/tasks/{task_id}/submit", json=payload)
        res.raise_for_status()
        return res.json()

    def listen_for_conflicts(
        self, callback: Callable[[Dict[str, Any]], Any]
    ) -> ConflictListener:
        """
        Start a background WebSocket listener on /ws/graph.
        Triggers `callback` when EVENT_CONFLICT_DETECTED is received.
        """
        listener = ConflictListener(ws_url=self.ws_url, callback=callback)
        listener.start()
        self._listeners.append(listener)
        return listener

    def format_context_prompt(self, task_id: str) -> str:
        """
        Fetch scoped subgraph and compile a concise, structured prompt suitable
        for autonomous execution in fresh agent sessions.
        """
        data = self.get_subgraph(task_id)
        task = data.get("task", {})
        ancestors = data.get("ancestors", [])
        is_stale = data.get("is_stale", False)
        conflict_details = data.get("conflict_details")

        lines = [
            f"# Assigned Task: {task.get('title', task_id)} ({task_id})",
            f"Status: {task.get('status', 'PENDING')}",
            f"Version: {task.get('version', 1)}",
        ]

        if is_stale:
            lines.append("\n> [!CAUTION] STALE CONFLICT DETECTED")
            lines.append(f"> Conflict Reason: {conflict_details or 'Upstream dependency modified'}")
            lines.append("> Please inspect the updated upstream specifications and reconcile your implementation.\n")

        task_payload = task.get("payload", {})
        if task_payload:
            lines.append("\n## Task Instructions & Payload:")
            lines.append("```json")
            lines.append(json.dumps(task_payload, indent=2))
            lines.append("```")

        if ancestors:
            lines.append("\n## Upstream Decisions & Requirements:")
            for anc in ancestors:
                anc_type = anc.get("type", "NODE")
                anc_id = anc.get("id")
                anc_title = anc.get("title", "")
                anc_payload = anc.get("payload", {})
                lines.append(f"- **[{anc_type}] {anc_id}**: {anc_title}")
                if anc_payload:
                    lines.append(f"  Details: {json.dumps(anc_payload)}")

        return "\n".join(lines)

    def create_node(
        self,
        node_id: str,
        title: str,
        node_type: str,
        payload: Optional[Dict[str, Any]] = None,
        dependencies: Optional[List[str]] = None,
        idempotency_key: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Helper to create a node via REST API."""
        body = {
            "id": node_id,
            "title": title,
            "type": node_type,
            "payload": payload or {},
            "dependencies": dependencies or [],
            "idempotency_key": idempotency_key or f"create-{node_id}-{uuid.uuid4().hex[:8]}",
            "actor": actor or self.agent_id,
        }
        res = self._client.post("/api/nodes", json=body)
        res.raise_for_status()
        return res.json()

    def update_requirement(
        self,
        req_id: str,
        new_spec: str,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Helper to update a requirement and trigger blast-radius invalidation."""
        body = {
            "req_id": req_id,
            "new_spec": new_spec,
            "actor": self.agent_id,
            "idempotency_key": idempotency_key or f"req-update-{req_id}-{uuid.uuid4().hex[:8]}",
        }
        res = self._client.post(f"/api/requirements/{req_id}/update", json=body)
        res.raise_for_status()
        return res.json()

    def get_graph(self) -> Dict[str, Any]:
        """Helper to get full graph state."""
        res = self._client.get("/api/graph")
        res.raise_for_status()
        return res.json()

    def get_node_events(self, node_id: str) -> List[Dict[str, Any]]:
        """Helper to get audit lineage events for a node."""
        res = self._client.get(f"/api/nodes/{node_id}/events")
        res.raise_for_status()
        return res.json()

    def close(self) -> None:
        """Stop all listeners and close HTTP client."""
        for listener in self._listeners:
            listener.stop()
        self._listeners.clear()
        if not self._custom_client:
            self._client.close()

    def __enter__(self) -> BaseAgentAdapter:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
