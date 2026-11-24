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

from adapters.base import BaseAgentAdapter, ConflictListener, OmniHarnessClient
from adapters.claude_adapter import ClaudeCodeAdapter, build_parser as build_claude_parser, main as claude_main
from adapters.codex_adapter import CodexAdapter, build_parser as build_codex_parser, main as codex_main
from adapters.agy_adapter import AntigravityAdapter, build_parser as build_agy_parser, main as agy_main


# ---------------------------------------------------------------------------
# 1. Contract Symmetry & Inheritance Tests
# ---------------------------------------------------------------------------

def test_adapter_contract_symmetry():
    """Verify ClaudeCodeAdapter, CodexAdapter, and AntigravityAdapter expose the exact same API contract."""
    claude_cls = ClaudeCodeAdapter
    codex_cls = CodexAdapter
    agy_cls = AntigravityAdapter

    assert issubclass(claude_cls, BaseAgentAdapter)
    assert issubclass(codex_cls, BaseAgentAdapter)
    assert issubclass(agy_cls, BaseAgentAdapter)
    assert OmniHarnessClient is BaseAgentAdapter

    # Core required methods
    required_methods = [
        "claim_task",
        "get_subgraph",
        "submit_result",
        "submit_task",
        "listen_for_conflicts",
        "format_context_prompt",
        "format_scoped_prompt",
        "create_node",
        "update_requirement",
        "get_graph",
        "get_node_events",
        "close",
    ]

    for method_name in required_methods:
        assert hasattr(claude_cls, method_name), f"ClaudeCodeAdapter missing {method_name}"
        assert hasattr(codex_cls, method_name), f"CodexAdapter missing {method_name}"
        assert hasattr(agy_cls, method_name), f"AntigravityAdapter missing {method_name}"

        claude_sig = inspect.signature(getattr(claude_cls, method_name))
        codex_sig = inspect.signature(getattr(codex_cls, method_name))
        agy_sig = inspect.signature(getattr(agy_cls, method_name))
        assert claude_sig == codex_sig == agy_sig, f"Signature mismatch for {method_name}"

    # Verify default agent IDs are distinct and descriptive
    claude_inst = ClaudeCodeAdapter()
    codex_inst = CodexAdapter()
    agy_inst = AntigravityAdapter()
    assert claude_inst.agent_id == "claude-code-cli"
    assert codex_inst.agent_id == "openai-codex-cursor"
    assert agy_inst.agent_id == "antigravity-agent"

    claude_inst.close()
    codex_inst.close()
    agy_inst.close()


def test_cli_parser_symmetry():
    """Verify both CLI entrypoint parsers offer the identical command structure."""
    p1 = build_claude_parser()
    p2 = build_codex_parser()

    # Both parsers have claim, subgraph, submit, listen, graph, context
    p1_actions = {a.dest for a in p1._actions}
    p2_actions = {a.dest for a in p2._actions}
    assert p1_actions == p2_actions

    # Verify common command destinations
    assert "command" in p1_actions

    # Extract subparser commands
    subparsers_action1 = next(a for a in p1._actions if a.dest == "command")
    subparsers_action2 = next(a for a in p2._actions if a.dest == "command")
    assert set(subparsers_action1.choices.keys()) == set(subparsers_action2.choices.keys())
    assert {"claim", "subgraph", "submit", "listen", "graph", "context"}.issubset(
        set(subparsers_action1.choices.keys())
    )


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


# ---------------------------------------------------------------------------
# 6. OmniHarnessClient Alias & Extended Formatting Tests
# ---------------------------------------------------------------------------

def test_omniharness_client_alias_and_methods():
    """Verify OmniHarnessClient alias, tool_name initialization, and prompt formatting."""
    captured = {}

    def mock_handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        if "claim" in request.url.path:
            body = json.loads(request.read().decode())
            captured["claim_body"] = body
            return httpx.Response(200, json={"status": "IN_PROGRESS", "assigned_to": f"{body['agent_id']} ({body.get('tool_name')})"})
        elif "submit" in request.url.path:
            body = json.loads(request.read().decode())
            captured["submit_body"] = body
            return httpx.Response(200, json={"status": "COMPLETED", "version": 2})
        return httpx.Response(404)

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler), base_url="http://localhost:8000")

    client = OmniHarnessClient(
        base_url="http://localhost:8000",
        agent_id="test-client-agent",
        tool_name="omniharness-core",
        client=mock_client,
    )
    assert client.agent_id == "test-client-agent"
    assert client.tool_name == "omniharness-core"
    assert client.server_url == "http://localhost:8000"

    # Test claim_task passes tool_name
    client.claim_task("TSK-01", idempotency_key="client-claim-001")
    assert captured["claim_body"]["tool_name"] == "omniharness-core"
    assert captured["claim_body"]["agent_id"] == "test-client-agent"

    # Test submit_task alias
    client.submit_task("TSK-01", output={"status": "done"}, idempotency_key="client-sub-001")
    assert captured["submit_body"]["output"] == {"status": "done"}
    assert captured["submit_body"]["idempotency_key"] == "client-sub-001"

    # Test format_scoped_prompt directly with subgraph dict
    subgraph_data = {
        "task": {
            "id": "TSK-05",
            "title": "Crypto Verifier",
            "status": "PENDING",
            "version": 1,
            "payload": {"algorithm": "Ed25519"},
        },
        "ancestors": [
            {
                "id": "REQ-01",
                "title": "Auth Architecture",
                "type": "REQUIREMENT",
                "payload": {"spec": "Use asymmetric keys"},
            }
        ],
        "is_stale": False,
    }
    prompt = client.format_scoped_prompt(subgraph_data)
    assert "Assigned Task: Crypto Verifier (TSK-05)" in prompt
    assert "Auth Architecture" in prompt
    assert "Ed25519" in prompt
    assert "STALE CONFLICT" not in prompt

    # Test format_context_prompt with subgraph dict overload
    prompt_from_dict = client.format_context_prompt(subgraph_data)
    assert prompt_from_dict == prompt

    client.close()


# ---------------------------------------------------------------------------
# 7. HTTP Error Mapping & Status Code Tests (409 Conflict, 404 Not Found)
# ---------------------------------------------------------------------------

def test_http_error_mapping_409_conflict(monkeypatch, capsys):
    """Verify HTTP 409 Conflict raises HTTPStatusError and CLI exits with code 2."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={"detail": "Task TSK-01 is already claimed by claude-code-cli"},
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler), base_url="http://localhost:8000")

    # 1. Direct adapter call raises httpx.HTTPStatusError
    adapter = CodexAdapter(client=mock_client)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        adapter.claim_task("TSK-01")
    assert exc_info.value.response.status_code == 409
    assert "Task TSK-01 is already claimed" in exc_info.value.response.json()["detail"]
    adapter.close()

    # 2. Claude CLI returns exit code 2 (Conflict)
    monkeypatch.setattr(
        ClaudeCodeAdapter,
        "__init__",
        lambda self, *args, **kwargs: super(ClaudeCodeAdapter, self).__init__(
            agent_id="claude-code-cli",
            server_url="http://localhost:8000",
            client=mock_client,
        )
    )
    code_claude = claude_main(["claim", "TSK-01"])
    assert code_claude == 2
    err_claude = capsys.readouterr().err
    assert "[ERROR 409]" in err_claude
    assert "already claimed" in err_claude

    # 3. Codex CLI with --json outputs structured error and exits with code 2
    monkeypatch.setattr(
        CodexAdapter,
        "__init__",
        lambda self, *args, **kwargs: super(CodexAdapter, self).__init__(
            agent_id="openai-codex-cursor",
            server_url="http://localhost:8000",
            client=mock_client,
        )
    )
    code_codex = codex_main(["claim", "TSK-01", "--json"])
    assert code_codex == 2
    out_codex = capsys.readouterr().out
    data_codex = json.loads(out_codex)
    assert data_codex["status_code"] == 409
    assert "already claimed" in data_codex["error"]


def test_http_error_mapping_404_not_found(monkeypatch, capsys):
    """Verify HTTP 404 Not Found raises HTTPStatusError and CLI exits with code 1."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"detail": "Task TSK-UNKNOWN not found in DAG"},
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler), base_url="http://localhost:8000")

    # 1. Direct adapter call raises httpx.HTTPStatusError
    adapter = ClaudeCodeAdapter(client=mock_client)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        adapter.get_subgraph("TSK-UNKNOWN")
    assert exc_info.value.response.status_code == 404
    adapter.close()

    # 2. CLI handles 404 with code 1
    monkeypatch.setattr(
        ClaudeCodeAdapter,
        "__init__",
        lambda self, *args, **kwargs: super(ClaudeCodeAdapter, self).__init__(
            agent_id="claude-code-cli",
            server_url="http://localhost:8000",
            client=mock_client,
        )
    )
    code = claude_main(["subgraph", "TSK-UNKNOWN"])
    assert code == 1
    err = capsys.readouterr().err
    assert "[ERROR 404]" in err
    assert "TSK-UNKNOWN not found" in err


# ---------------------------------------------------------------------------
# 8. CLI File Submission & Extended Commands Tests
# ---------------------------------------------------------------------------

def test_cli_submit_with_file_and_json(tmp_path, monkeypatch, capsys):
    """Verify submit command handles --file (JSON and text) and --json output formatting."""
    captured_payloads = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        captured_payloads.append(body)
        return httpx.Response(200, json={"status": "COMPLETED", "version": 2})

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler), base_url="http://localhost:8000")

    monkeypatch.setattr(
        ClaudeCodeAdapter,
        "__init__",
        lambda self, *args, **kwargs: super(ClaudeCodeAdapter, self).__init__(
            agent_id="claude-code-cli",
            server_url="http://localhost:8000",
            client=mock_client,
        )
    )
    monkeypatch.setattr(
        CodexAdapter,
        "__init__",
        lambda self, *args, **kwargs: super(CodexAdapter, self).__init__(
            agent_id="openai-codex-cursor",
            server_url="http://localhost:8000",
            client=mock_client,
        )
    )

    # 1. Submit JSON file via Claude CLI
    json_file = tmp_path / "result.json"
    json_file.write_text(json.dumps({"test_pass": True, "coverage": 98.5}), encoding="utf-8")
    code1 = claude_main(["submit", "TSK-01", "--file", str(json_file)])
    assert code1 == 0
    assert captured_payloads[0]["output"] == {"test_pass": True, "coverage": 98.5}
    out1 = capsys.readouterr().out
    assert "Successfully submitted task: TSK-01" in out1

    # 2. Submit non-JSON text file via Codex CLI with --json flag
    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("All cryptographic modules verified.", encoding="utf-8")
    code2 = codex_main(["submit", "TSK-02", "--file", str(txt_file), "--json"])
    assert code2 == 0
    assert captured_payloads[1]["output"] == {"content": "All cryptographic modules verified."}
    out2 = capsys.readouterr().out
    data = json.loads(out2)
    assert data["status"] == "COMPLETED"


def test_cli_graph_and_context_commands(monkeypatch, capsys):
    """Verify graph command and context alias command across both CLIs."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/graph":
            return httpx.Response(200, json={"nodes": [{"id": "REQ-01"}], "edges": [], "stats": {"total": 1}})
        elif "subgraph" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "task": {"id": "TSK-01", "title": "Setup", "status": "PENDING", "version": 1},
                    "ancestors": [],
                    "edges": [],
                    "is_stale": False,
                },
            )
        return httpx.Response(404)

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler), base_url="http://localhost:8000")

    monkeypatch.setattr(
        ClaudeCodeAdapter,
        "__init__",
        lambda self, *args, **kwargs: super(ClaudeCodeAdapter, self).__init__(
            agent_id="claude-code-cli",
            server_url="http://localhost:8000",
            client=mock_client,
        )
    )
    monkeypatch.setattr(
        CodexAdapter,
        "__init__",
        lambda self, *args, **kwargs: super(CodexAdapter, self).__init__(
            agent_id="openai-codex-cursor",
            server_url="http://localhost:8000",
            client=mock_client,
        )
    )

    # Claude graph command
    code1 = claude_main(["graph"])
    assert code1 == 0
    out1 = capsys.readouterr().out
    graph_data = json.loads(out1)
    assert graph_data["stats"]["total"] == 1

    # Codex context command
    code2 = codex_main(["context", "TSK-01"])
    assert code2 == 0
    out2 = capsys.readouterr().out
    assert "Assigned Task: Setup (TSK-01)" in out2


# ---------------------------------------------------------------------------
# 9. Extended WebSocket Conflict & BLAST_RADIUS_ALERT Catching Tests
# ---------------------------------------------------------------------------

def test_websocket_conflict_listener_catches_blast_radius_and_multiple_frames():
    """
    Verify listen_for_conflicts catches BLAST_RADIUS_ALERT, CONFLICT_DETECTED,
    and EVENT_CONFLICT_DETECTED frames, while ignoring non-conflict messages.
    """
    def ws_handler(ws):
        # 1. Ignored event: node created
        ws.send(json.dumps({"event_type": "EVENT_NODE_CREATED", "node_id": "REQ-01"}))
        time.sleep(0.04)

        # 2. Blast radius alert frame (SHOULD be caught)
        ws.send(
            json.dumps({
                "event": "BLAST_RADIUS_ALERT",
                "event_type": "BLAST_RADIUS_ALERT",
                "root_cause_node": "REQ-01",
                "invalidated_nodes": ["TSK-01", "TSK-02"],
                "severity": "CRITICAL",
                "message": "Requirement REQ-01 updated. 2 downstream nodes invalidated.",
            })
        )
        time.sleep(0.04)

        # 3. Individual conflict detected frame (SHOULD be caught)
        ws.send(
            json.dumps({
                "event": "CONFLICT_DETECTED",
                "event_type": "EVENT_CONFLICT_DETECTED",
                "node_id": "TSK-01",
                "data": {"reason": "Upstream specification modified"},
            })
        )
        time.sleep(0.04)

        # 4. Blast radius alert without event_type key (SHOULD be caught)
        ws.send(
            json.dumps({
                "event": "BLAST_RADIUS_ALERT",
                "root_cause_node": "REQ-02",
                "invalidated_nodes": ["TSK-03"],
                "severity": "CRITICAL",
            })
        )
        time.sleep(0.04)

        # 5. Ignored event: task submitted
        ws.send(json.dumps({"event_type": "EVENT_TASK_SUBMITTED", "node_id": "TSK-04"}))

    server = websockets.sync.server.serve(ws_handler, "127.0.0.1", 0)
    server_port = server.socket.getsockname()[1]
    ws_url = f"ws://127.0.0.1:{server_port}"

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    adapter = ClaudeCodeAdapter(ws_url=ws_url)
    received_conflicts: List[Dict[str, Any]] = []
    conflict_count_event = threading.Event()

    def on_conflict(evt: Dict[str, Any]):
        received_conflicts.append(evt)
        if len(received_conflicts) >= 3:
            conflict_count_event.set()

    listener = adapter.listen_for_conflicts(on_conflict)

    assert conflict_count_event.wait(timeout=4.0), "Timed out waiting for 3 conflict/blast radius events"
    assert len(received_conflicts) == 3

    # Check caught events
    event_names = [e.get("event") or e.get("event_type") for e in received_conflicts]
    assert "BLAST_RADIUS_ALERT" in event_names
    assert "CONFLICT_DETECTED" in event_names or "EVENT_CONFLICT_DETECTED" in event_names

    # Clean shutdown
    listener.stop()
    assert listener.is_alive() is False
    server.shutdown()
    adapter.close()


def test_websocket_listener_endpoint_paths():
    """Verify WebSocket URL construction for /ws and /ws/graph and custom ws_url override."""
    # 1. Default ws_path (/ws/graph)
    a1 = BaseAgentAdapter(agent_id="a1", server_url="http://localhost:8000")
    assert a1.ws_url == "ws://localhost:8000/ws/graph"
    a1.close()

    # 2. Custom ws_path (/ws)
    a2 = BaseAgentAdapter(agent_id="a2", server_url="http://localhost:8000", ws_path="/ws")
    assert a2.ws_url == "ws://localhost:8000/ws"
    a2.close()

    # 3. Explicit ws_url
    a3 = BaseAgentAdapter(agent_id="a3", server_url="http://localhost:8000", ws_url="wss://remote-hub.internal/ws/live")
    assert a3.ws_url == "wss://remote-hub.internal/ws/live"
    a3.close()
