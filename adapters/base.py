"""
SYNAPSE Base Agent Adapter.
Provides standard HTTP operations and real-time WebSocket conflict listening
for heterogeneous AI agents (Claude Code, OpenAI Codex / Cursor, Antigravity).
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional
import httpx
import websockets
import websockets.sync.client

logger = logging.getLogger("synapse.adapter")


class ConflictListener:
    """
    Background worker listening for EVENT_CONFLICT_DETECTED on SYNAPSE WebSocket.
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
        self._thread = threading.Thread(target=self._run, daemon=True, name="SYNAPSE-ConflictListener")
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                with websockets.sync.client.connect(
                    self.ws_url, open_timeout=3.0, close_timeout=1.0
                ) as ws:
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

                            event_name = data.get("event")
                            event_type = (
                                data.get("event_type")
                                or data.get("type")
                                or event_name
                            )
                            conflict_events = {
                                "EVENT_CONFLICT_DETECTED",
                                "CONFLICT_DETECTED",
                                "BLAST_RADIUS_ALERT",
                            }
                            if event_type in conflict_events or event_name in conflict_events:
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
    Base Agent Adapter for SYNAPSE.
    Handles task leasing, scoped context retrieval, submission, and conflict alerts.
    """

    def __init__(
        self,
        agent_id: str,
        server_url: str = "http://localhost:8000",
        ws_url: Optional[str] = None,
        client: Optional[httpx.Client] = None,
        base_url: Optional[str] = None,
        tool_name: Optional[str] = None,
        ws_path: str = "/ws/graph",
        api_key: Optional[str] = None,
        board_id: Optional[str] = None,
    ):
        self.agent_id = agent_id
        effective_server_url = (base_url or server_url).rstrip("/")
        self.server_url = effective_server_url
        self.tool_name = tool_name or agent_id
        self.api_key = api_key
        self.board_id = board_id

        if ws_url:
            self.ws_url = ws_url
        else:
            base = self.server_url
            clean_path = ws_path if ws_path.startswith("/") else f"/{ws_path}"
            if base.startswith("http://"):
                self.ws_url = "ws://" + base[7:] + clean_path
            elif base.startswith("https://"):
                self.ws_url = "wss://" + base[8:] + clean_path
            else:
                self.ws_url = f"ws://{base}{clean_path}"
            qs_parts = []
            if self.api_key:
                qs_parts.append(f"token={self.api_key}")
            if self.board_id:
                qs_parts.append(f"board_id={self.board_id}")
            if qs_parts:
                sep = "&" if "?" in self.ws_url else "?"
                self.ws_url = f"{self.ws_url}{sep}{'&'.join(qs_parts)}"

        self._custom_client = client is not None
        self._client = client or httpx.Client(base_url=self.server_url, timeout=30.0)
        try:
            self.client_host: Optional[str] = socket.gethostname()
        except Exception:
            self.client_host = None
        self._client.headers.setdefault("X-Agent-Id", self.agent_id)
        self._client.headers.setdefault("X-Tool-Name", self.tool_name)
        if self.client_host:
            self._client.headers.setdefault("X-Client-Host", self.client_host)
        if self.api_key:
            self._client.headers.setdefault("Authorization", f"Bearer {self.api_key}")
        if self.board_id:
            self._client.headers.setdefault("X-Board-Id", self.board_id)
        self._listeners: List[ConflictListener] = []

    def set_auth(self, api_key: Optional[str] = None, board_id: Optional[str] = None) -> None:
        """Configure Bearer Board Agent Key and target board_id on HTTP & WebSocket clients."""
        if api_key:
            self.api_key = api_key
            self._client.headers["Authorization"] = f"Bearer {api_key}"
        if board_id:
            self.board_id = board_id
            self._client.headers["X-Board-Id"] = board_id
        if api_key or board_id:
            base_ws = self.ws_url.split("?")[0]
            qs_parts = []
            if self.api_key:
                qs_parts.append(f"token={self.api_key}")
            if self.board_id:
                qs_parts.append(f"board_id={self.board_id}")
            self.ws_url = f"{base_ws}?{'&'.join(qs_parts)}" if qs_parts else base_ws

    def join_workspace(
        self,
        display_name: Optional[str] = None,
        client_host: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register this agent/laptop as an active participant in the shared workspace."""
        payload = {
            "user_id": self.agent_id,
            "agent_id": self.agent_id,
            "display_name": display_name or self.agent_id,
            "tool_name": self.tool_name,
            "client_host": client_host or self.client_host,
        }
        res = self._client.post("/api/users/join", json=payload)
        res.raise_for_status()
        return res.json()

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
            "tool_name": self.tool_name,
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

    # Alias submit_task to submit_result for contract completeness
    submit_task = submit_result

    def listen_for_conflicts(
        self,
        callback: Callable[[Dict[str, Any]], Any],
        ws_url: Optional[str] = None,
    ) -> ConflictListener:
        """
        Start a background WebSocket listener on /ws or /ws/graph.
        Triggers `callback` when EVENT_CONFLICT_DETECTED or BLAST_RADIUS_ALERT is received.
        """
        target_ws_url = ws_url or self.ws_url
        listener = ConflictListener(ws_url=target_ws_url, callback=callback)
        listener.start()
        self._listeners.append(listener)
        return listener

    def format_scoped_prompt(self, subgraph: Dict[str, Any]) -> str:
        """
        Compile a concise, structured markdown prompt (~300-800 tokens) suitable
        for autonomous execution in fresh agent sessions from a scoped subgraph dictionary.
        """
        task = subgraph.get("task", {})
        ancestors = subgraph.get("ancestors", [])
        is_stale = subgraph.get("is_stale", False)
        conflict_details = subgraph.get("conflict_details")
        task_id = task.get("id", "UNKNOWN")

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

    def format_context_prompt(self, task_id_or_subgraph: Any) -> str:
        """
        Fetch scoped subgraph (if task_id provided) or format existing subgraph,
        compiling a concise, structured prompt suitable for autonomous agent execution.
        """
        if isinstance(task_id_or_subgraph, dict):
            return self.format_scoped_prompt(task_id_or_subgraph)
        data = self.get_subgraph(str(task_id_or_subgraph))
        return self.format_scoped_prompt(data)

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


# Alias for client interoperability as specified in PROJECT.md
SynapseClient = BaseAgentAdapter
