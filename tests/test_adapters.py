"""
Unit and integration tests for OmniHarness Claude Code and Codex Adapters.
Verifies contract symmetry, strict idempotency key handling, and real-time WebSocket conflict alerts.
"""
from __future__ import annotations

import inspect
import json
import threading
import time
from typing import Any, Dict, List
import httpx
import pytest
import websockets.sync.server

from adapters.base import BaseAgentAdapter, ConflictListener
from adapters.claude_adapter import ClaudeCodeAdapter, build_parser as build_claude_parser, main as claude_main
from adapters.codex_adapter import CodexAdapter, build_parser as build_codex_parser, main as codex_main


# ---------------------------------------------------------------------------
# 1. Contract Symmetry & Inheritance Tests
# ---------------------------------------------------------------------------

def test_adapter_contract_symmetry():
    """Verify ClaudeCodeAdapter and CodexAdapter expose the exact same API contract."""
    claude_cls = ClaudeCodeAdapter
    codex_cls = CodexAdapter

    assert issubclass(claude_cls, BaseAgentAdapter)
    assert issubclass(codex_cls, BaseAgentAdapter)

    # Core required methods
    required_methods = [
        "claim_task",
        "get_subgraph",
        "submit_result",
        "listen_for_conflicts",
        "format_context_prompt",
        "create_node",
        "update_requirement",
        "get_graph",
        "get_node_events",
        "close",
    ]

    for method_name in required_methods:
        assert hasattr(claude_cls, method_name), f"ClaudeCodeAdapter missing {method_name}"
        assert hasattr(codex_cls, method_name), f"CodexAdapter missing {method_name}"

        claude_sig = inspect.signature(getattr(claude_cls, method_name))
        codex_sig = inspect.signature(getattr(codex_cls, method_name))
        assert claude_sig == codex_sig, f"Signature mismatch for {method_name}: {claude_sig} vs {codex_sig}"

    # Verify default agent IDs are distinct and descriptive
    claude_inst = ClaudeCodeAdapter()
    codex_inst = CodexAdapter()
    assert claude_inst.agent_id == "claude-code-cli"
    assert codex_inst.agent_id == "openai-codex-cursor"
    assert claude_inst.agent_id != codex_inst.agent_id
    claude_inst.close()
    codex_inst.close()


def test_cli_parser_symmetry():
    """Verify both CLI entrypoint parsers offer the identical command structure."""
    p1 = build_claude_parser()
    p2 = build_codex_parser()

    # Both parsers have claim, subgraph, submit, listen
    p1_actions = {a.dest for a in p1._actions}
    p2_actions = {a.dest for a in p2._actions}
    assert p1_actions == p2_actions


# ---------------------------------------------------------------------------
# 2. HTTP Operations & Idempotency Key Tests
# ---------------------------------------------------------------------------

def test_claim_task_explicit_idempotency_key():
    """Verify claim_task sends correct payload with explicit idempotency key."""
    captured_request = {}

    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tasks/TSK-01/claim"
        assert request.method == "POST"
        body = json.loads(request.read().decode())
        captured_request.update(body)
        return httpx.Response(
            200,
            json={
                "id": "TSK-01",
                "title": "Auth Middleware",
                "status": "IN_PROGRESS",
                "assigned_to": "claude-code-cli",
                "version": 1,
            },
        )

    transport = httpx.MockTransport(mock_handler)
    client = httpx.Client(transport=transport, base_url="http://mock-omniharness")

    adapter = ClaudeCodeAdapter(client=client)
    res = adapter.claim_task("TSK-01", idempotency_key="idemp-key-custom-001")

    assert captured_request["task_id"] == "TSK-01"
    assert captured_request["agent_id"] == "claude-code-cli"
    assert captured_request["idempotency_key"] == "idemp-key-custom-001"
    assert res["status"] == "IN_PROGRESS"
    assert res["assigned_to"] == "claude-code-cli"
    adapter.close()


def test_claim_task_auto_generated_idempotency_key():
    """Verify claim_task automatically generates a deterministic-prefix idempotency key when omitted."""
    captured_key = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        captured_key.append(body["idempotency_key"])
        return httpx.Response(200, json={"status": "IN_PROGRESS"})

    transport = httpx.MockTransport(mock_handler)
    client = httpx.Client(transport=transport, base_url="http://mock-omniharness")

    adapter = CodexAdapter(client=client)
    adapter.claim_task("TSK-02")

    assert len(captured_key) == 1
    assert captured_key[0].startswith("claim-openai-codex-cursor-TSK-02-")
    adapter.close()


def test_submit_result_with_and_without_idempotency():
    """Verify submit_result payload structure and idempotency key handling."""
    requests_received = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tasks/TSK-02/submit"
        body = json.loads(request.read().decode())
        requests_received.append(body)
        return httpx.Response(
            200,
            json={
                "id": "TSK-02",
                "status": "COMPLETED",
                "version": 2,
            },
        )

    transport = httpx.MockTransport(mock_handler)
    client = httpx.Client(transport=transport, base_url="http://mock-omniharness")

    adapter = CodexAdapter(client=client)

    # 1. Custom key
    res1 = adapter.submit_result(
        "TSK-02",
        output={"code": "jwt_verify()"},
        idempotency_key="submit-key-999",
    )
    assert res1["status"] == "COMPLETED"
    assert requests_received[0]["idempotency_key"] == "submit-key-999"
    assert requests_received[0]["output"] == {"code": "jwt_verify()"}

    # 2. Auto generated key
    res2 = adapter.submit_result(
        "TSK-02",
        output={"code": "ed25519_verify()"},
    )
    assert res2["status"] == "COMPLETED"
    assert requests_received[1]["idempotency_key"].startswith("submit-openai-codex-cursor-TSK-02-")
    assert requests_received[1]["output"] == {"code": "ed25519_verify()"}
    adapter.close()


def test_get_subgraph_and_format_prompt():
    """Verify get_subgraph and structured markdown prompt compilation."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tasks/TSK-03/subgraph"
        return httpx.Response(
            200,
            json={
                "task": {
                    "id": "TSK-03",
                    "title": "Token Validation Route",
                    "type": "TASK",
                    "status": "STALE_CONFLICT",
                    "version": 1,
                    "payload": {"route": "/api/validate"},
                },
                "ancestors": [
                    {
                        "id": "REQ-01",
                        "title": "Auth Spec",
                        "type": "REQUIREMENT",
                        "payload": {"algorithm": "Ed25519"},
                    },
                    {
                        "id": "DEC-01",
                        "title": "Asymmetric Signing",
                        "type": "DECISION",
                        "payload": {"decision": "Use Ed25519 instead of HS256"},
                    },
                ],
                "edges": [],
                "is_stale": True,
                "conflict_details": "REQ-01 specification updated to v2",
            },
        )

    transport = httpx.MockTransport(mock_handler)
    client = httpx.Client(transport=transport, base_url="http://mock-omniharness")

    adapter = ClaudeCodeAdapter(client=client)
    subgraph = adapter.get_subgraph("TSK-03")
    assert subgraph["is_stale"] is True
    assert len(subgraph["ancestors"]) == 2

    # Check formatted prompt
    prompt = adapter.format_context_prompt("TSK-03")
    assert "Assigned Task: Token Validation Route (TSK-03)" in prompt
    assert "STALE CONFLICT DETECTED" in prompt
    assert "REQ-01 specification updated to v2" in prompt
    assert "[REQUIREMENT] REQ-01" in prompt
    assert "Auth Spec" in prompt
    assert "[DECISION] DEC-01" in prompt
    assert "Asymmetric Signing" in prompt
    assert "/api/validate" in prompt
    adapter.close()


# ---------------------------------------------------------------------------
# 3. WebSocket Conflict Alert Catching Tests
# ---------------------------------------------------------------------------

def test_websocket_conflict_listener_catches_conflicts():
    """
    Verify listen_for_conflicts connects to WebSocket, filters out non-conflict events,
    and reliably invokes the callback when EVENT_CONFLICT_DETECTED arrives.
    """
    server_ready = threading.Event()
    received_conflicts: List[Dict[str, Any]] = []

    def ws_handler(ws):
        # 1. Send normal node created event (should NOT trigger callback)
        ws.send(json.dumps({"event_type": "EVENT_NODE_CREATED", "node_id": "REQ-01"}))
        time.sleep(0.05)

        # 2. Send conflict detected event (SHOULD trigger callback)
        ws.send(
            json.dumps({
                "event_type": "EVENT_CONFLICT_DETECTED",
                "node_id": "TSK-03",
                "actor": "system",
                "data": {"reason": "Upstream REQ-01 was modified to Ed25519"},
            })
        )
        time.sleep(0.05)

        # 3. Send normal task submitted event (should NOT trigger callback)
        ws.send(json.dumps({"event_type": "EVENT_TASK_SUBMITTED", "node_id": "TSK-01"}))

    # Spin up ephemeral local sync websocket server
    server = websockets.sync.server.serve(ws_handler, "127.0.0.1", 0)
    server_port = server.socket.getsockname()[1]
    ws_url = f"ws://127.0.0.1:{server_port}"

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    adapter = CodexAdapter(ws_url=ws_url)

    conflict_arrived = threading.Event()

    def on_conflict(evt: Dict[str, Any]):
        received_conflicts.append(evt)
        conflict_arrived.set()

    listener = adapter.listen_for_conflicts(on_conflict)

    # Wait for the conflict event
    assert conflict_arrived.wait(timeout=3.0), "Timeout waiting for conflict alert"

    # Assert exactly one conflict was caught
    assert len(received_conflicts) == 1
    conflict_data = received_conflicts[0]
    assert conflict_data["event_type"] == "EVENT_CONFLICT_DETECTED"
    assert conflict_data["node_id"] == "TSK-03"
    assert "Upstream REQ-01" in conflict_data["data"]["reason"]

    # Verify listener lifecycle
    assert listener.is_alive() is True
    listener.stop()
    assert listener.is_alive() is False

    server.shutdown()
    adapter.close()


# ---------------------------------------------------------------------------
# 4. CLI Entrypoint Tests
# ---------------------------------------------------------------------------

def test_claude_cli_claim_and_submit(monkeypatch, capsys):
    """Verify ClaudeCodeAdapter CLI entrypoint handles claim and submit commands."""
    requests = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if "claim" in request.url.path:
            return httpx.Response(200, json={"status": "IN_PROGRESS", "assigned_to": "claude-code-cli"})
        elif "submit" in request.url.path:
            return httpx.Response(200, json={"status": "COMPLETED", "version": 2})
        return httpx.Response(404)

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler), base_url="http://localhost:8000")

    # Monkeypatch ClaudeCodeAdapter to use mock_client
    monkeypatch.setattr(
        ClaudeCodeAdapter,
        "__init__",
        lambda self, *args, **kwargs: super(ClaudeCodeAdapter, self).__init__(
            agent_id=kwargs.get("agent_id", "claude-code-cli"),
            server_url="http://localhost:8000",
            client=mock_client,
        )
    )

    # Test claim
    code = claude_main(["claim", "TSK-01", "--idempotency-key", "cli-key-1"])
    assert code == 0
    captured = capsys.readouterr().out
    assert "Successfully claimed task: TSK-01" in captured

    # Test submit with json flag
    code = claude_main(["submit", "TSK-01", "--output", '{"doc": "done"}', "--json"])
    assert code == 0
    captured_json = capsys.readouterr().out
    data = json.loads(captured_json)
    assert data["status"] == "COMPLETED"


def test_codex_cli_subgraph_and_listen(monkeypatch, capsys):
    """Verify CodexAdapter CLI entrypoint handles subgraph and prompt commands."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if "subgraph" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "task": {"id": "TSK-02", "title": "Token Verifier", "status": "PENDING", "version": 1},
                    "ancestors": [],
                    "edges": [],
                    "is_stale": False,
                },
            )
        return httpx.Response(404)

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler), base_url="http://localhost:8000")

    monkeypatch.setattr(
        CodexAdapter,
        "__init__",
        lambda self, *args, **kwargs: super(CodexAdapter, self).__init__(
            agent_id=kwargs.get("agent_id", "openai-codex-cursor"),
            server_url="http://localhost:8000",
            client=mock_client,
        )
    )

    code = codex_main(["subgraph", "TSK-02", "--prompt"])
    assert code == 0
    captured = capsys.readouterr().out
    assert "Assigned Task: Token Verifier (TSK-02)" in captured


# ---------------------------------------------------------------------------
# 5. Helper Methods & Context Manager Tests
# ---------------------------------------------------------------------------

def test_helpers_and_context_manager():
    """Verify create_node, update_requirement, get_graph, get_node_events and context manager."""
    paths_called = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        paths_called.append(request.url.path)
        if request.url.path == "/api/nodes":
            return httpx.Response(200, json={"id": "NODE-1"})
        elif "/requirements/" in request.url.path:
            return httpx.Response(200, json={"invalidated": ["TSK-03"]})
        elif request.url.path == "/api/graph":
            return httpx.Response(200, json={"nodes": [], "edges": []})
        elif "/events" in request.url.path:
            return httpx.Response(200, json=[{"event_type": "EVENT_NODE_CREATED"}])
        return httpx.Response(404)

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler), base_url="http://localhost:8000")

    with BaseAgentAdapter(agent_id="test-agent", client=mock_client) as adapter:
        adapter.create_node("NODE-1", "Test Node", "TASK")
        adapter.update_requirement("REQ-01", "new spec")
        adapter.get_graph()
        adapter.get_node_events("NODE-1")

    assert "/api/nodes" in paths_called
    assert "/api/requirements/REQ-01/update" in paths_called
    assert "/api/graph" in paths_called
    assert "/api/nodes/NODE-1/events" in paths_called
