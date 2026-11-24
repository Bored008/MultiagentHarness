"""
Shared CLI parser, profile/env resolution, and command runner for OmniHarness adapters.
Provides 100% symmetrical UX across Claude Code, OpenAI Codex/Cursor, and Antigravity.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Type
import httpx

from adapters.base import BaseAgentAdapter

DEFAULT_SERVER_URL = "http://localhost:8000"
PROFILE_FILENAME = ".omniharness.json"


def _load_json_dict(path: Path) -> Dict[str, Any]:
    """Safely load a JSON object from disk, returning {} if missing or invalid."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_saved_profile() -> Dict[str, Any]:
    """
    Load saved OmniHarness CLI profile from ~/.omniharness.json and CWD .omniharness.json.
    CWD settings take precedence over home-directory settings.
    """
    profile: Dict[str, Any] = {}
    home_data = _load_json_dict(Path.home() / PROFILE_FILENAME)
    cwd_data = _load_json_dict(Path.cwd() / PROFILE_FILENAME)
    for source in (home_data, cwd_data):
        for key in ("server", "agent_id"):
            val = source.get(key)
            if isinstance(val, str) and val.strip():
                profile[key] = val.strip()
    return profile


def save_cli_profile(
    server_url: str,
    agent_id: str,
    global_scope: bool = False,
) -> Path:
    """Persist {server, agent_id} into .omniharness.json (CWD or ~/.omniharness.json)."""
    target = (Path.home() / PROFILE_FILENAME) if global_scope else (Path.cwd() / PROFILE_FILENAME)
    existing = _load_json_dict(target)
    existing["server"] = server_url.strip().rstrip("/")
    existing["agent_id"] = agent_id.strip()
    target.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    return target


def resolve_cli_config(
    cli_server: Optional[str],
    cli_agent_id: Optional[str],
    default_agent_id: str,
) -> Tuple[str, str]:
    """
    Resolve (server_url, agent_id) with strict precedence:
      1. Explicit CLI flags (--server / --agent-id)
      2. Environment variables (OMNIHARNESS_SERVER / OMNIHARNESS_AGENT_ID)
      3. Saved profile (.omniharness.json in CWD, then ~/.omniharness.json)
      4. Defaults (http://localhost:8000, default_agent_id)
    """
    saved = load_saved_profile()

    env_server = os.environ.get("OMNIHARNESS_SERVER", "").strip()
    env_agent_id = os.environ.get("OMNIHARNESS_AGENT_ID", "").strip()

    raw_server = (
        (cli_server.strip() if isinstance(cli_server, str) and cli_server.strip() else None)
        or (env_server or None)
        or saved.get("server")
        or DEFAULT_SERVER_URL
    )
    server_url = raw_server.rstrip("/")

    agent_id = (
        (cli_agent_id.strip() if isinstance(cli_agent_id, str) and cli_agent_id.strip() else None)
        or (env_agent_id or None)
        or saved.get("agent_id")
        or default_agent_id
    )
    return server_url, agent_id


def _copy_to_clipboard(text: str) -> bool:
    """Best-effort copy of text to system clipboard across Windows, macOS, and Linux."""
    commands: List[List[str]]
    if sys.platform.startswith("win"):
        commands = [["clip"]]
    elif sys.platform == "darwin":
        commands = [["pbcopy"]]
    else:
        commands = [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]

    for cmd in commands:
        try:
            subprocess.run(cmd, input=text, text=True, check=True)
            return True
        except Exception:
            continue
    return False


def _compute_ready_ids(
    nodes: Iterable[Dict[str, Any]],
    edges: Iterable[Dict[str, Any]],
) -> List[str]:
    """Compute ready TASK IDs locally when /api/tasks/ready is unavailable."""
    node_list = list(nodes)
    node_by_id = {n["id"]: n for n in node_list if "id" in n}
    preds: Dict[str, List[str]] = {}
    for edge in edges:
        src = edge.get("source")
        tgt = edge.get("target")
        if src and tgt:
            preds.setdefault(tgt, []).append(src)

    ready_ids: List[str] = []
    for node in node_list:
        nid = node.get("id")
        if nid and node.get("type") == "TASK" and node.get("status") == "PENDING":
            node_preds = preds.get(nid, [])
            if all(node_by_id[p].get("status") == "COMPLETED" for p in node_preds if p in node_by_id):
                ready_ids.append(nid)
    return ready_ids


def _get_ready_task_ids(adapter: BaseAgentAdapter, graph: Optional[Dict[str, Any]] = None) -> List[str]:
    """Fetch ready task IDs from /api/tasks/ready, falling back to local graph computation."""
    try:
        res = adapter.client.get("/api/tasks/ready")
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list):
                return [item["id"] for item in data if isinstance(item, dict) and "id" in item]
    except Exception:
        pass

    graph_data = graph if graph is not None else adapter.get_graph()
    return _compute_ready_ids(graph_data.get("nodes", []), graph_data.get("edges", []))


def _resolve_submit_output(
    file_path: Optional[str],
    output_arg: Optional[str],
    summary_arg: Optional[str] = None,
    default_summary: Optional[str] = None,
) -> Dict[str, Any]:
    """Parse task output payload from --file, --output, positional summary, or fallback."""
    if file_path:
        content = Path(file_path).read_text(encoding="utf-8")
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else {"content": parsed}
        except Exception:
            return {"content": content}
    if output_arg:
        try:
            parsed = json.loads(output_arg)
            return parsed if isinstance(parsed, dict) else {"summary": output_arg}
        except Exception:
            return {"summary": output_arg}
    if summary_arg is not None:
        return {"summary": summary_arg}
    if default_summary is not None:
        return {"summary": default_summary}
    return {"completed": True, "timestamp": time.time()}


def _add_common_flags(target: argparse.ArgumentParser, default_agent_id: str, suppress_defaults: bool) -> None:
    """Register shared global CLI flags on root and subparsers."""
    unset = argparse.SUPPRESS if suppress_defaults else None
    json_default: Any = argparse.SUPPRESS if suppress_defaults else False
    target.add_argument(
        "--server",
        default=unset,
        help=f"OmniHarness REST server base URL (env: OMNIHARNESS_SERVER, default: {DEFAULT_SERVER_URL})",
    )
    target.add_argument(
        "--ws-url",
        default=unset,
        help="Custom WebSocket URL for real-time events (default: derived from --server)",
    )
    target.add_argument(
        "--agent-id",
        default=unset,
        help=f"Agent identifier for logging and lease ownership (env: OMNIHARNESS_AGENT_ID, default: {default_agent_id})",
    )
    target.add_argument(
        "--json",
        action="store_true",
        default=json_default,
        help="Output results strictly as formatted JSON",
    )


def build_adapter_parser(
    prog: str,
    description: str,
    default_agent_id: str,
) -> argparse.ArgumentParser:
    """Build the symmetrical CLI parser shared across all OmniHarness agent adapters."""
    common = argparse.ArgumentParser(add_help=False)
    _add_common_flags(common, default_agent_id=default_agent_id, suppress_defaults=False)

    sub_common = argparse.ArgumentParser(add_help=False)
    _add_common_flags(sub_common, default_agent_id=default_agent_id, suppress_defaults=True)

    parser = argparse.ArgumentParser(
        prog=prog,
        description=description,
        parents=[common],
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Connect command
    connect_p = subparsers.add_parser(
        "connect",
        parents=[sub_common],
        help="Save OmniHarness server URL and agent ID profile and verify /health",
    )
    connect_p.add_argument(
        "server_url",
        help="OmniHarness server base URL (e.g. http://192.168.1.50:8000)",
    )
    connect_p.add_argument(
        "--global",
        dest="global_config",
        action="store_true",
        help="Save profile to ~/.omniharness.json instead of CWD .omniharness.json",
    )

    # Tasks command
    subparsers.add_parser(
        "tasks",
        parents=[sub_common],
        help="List shared graph nodes and highlight [READY] claimable tasks",
    )

    # Users command
    subparsers.add_parser(
        "users",
        parents=[sub_common],
        help="List users and agents who joined the shared workspace",
    )

    # Start command (claim + scoped prompt in 1 step)
    start_p = subparsers.add_parser(
        "start",
        parents=[sub_common],
        help="Claim a task (or next ready task) and print its scoped prompt",
    )
    start_p.add_argument(
        "task_id",
        nargs="?",
        default=None,
        help="Task ID to start (omit to auto-pick the first [READY] task)",
    )
    start_p.add_argument(
        "--copy",
        action="store_true",
        help="Copy the scoped prompt to clipboard",
    )
    start_p.add_argument(
        "--idempotency-key",
        default=None,
        help="Custom idempotency key for deterministic replay",
    )

    # Done command (ergonomic submit)
    done_p = subparsers.add_parser(
        "done",
        parents=[sub_common],
        help="Submit task completion with an optional plain-text summary",
    )
    done_p.add_argument("task_id", help="ID of completed task")
    done_p.add_argument(
        "summary",
        nargs="?",
        default=None,
        help="Short completion summary (stored as {\"summary\": ...})",
    )
    done_p.add_argument(
        "--output",
        default=None,
        help="JSON string or text content representing output payload",
    )
    done_p.add_argument(
        "--file",
        default=None,
        help="Path to file containing output content to submit",
    )
    done_p.add_argument(
        "--idempotency-key",
        default=None,
        help="Custom idempotency key for submit operation",
    )

    # Seed command
    seed_p = subparsers.add_parser(
        "seed",
        parents=[sub_common],
        help="Seed the shared DAG from a built-in template (default: tictactoe)",
    )
    seed_p.add_argument(
        "template",
        nargs="?",
        default="tictactoe",
        help="Template name (e.g. tictactoe, auth; default: tictactoe)",
    )
    seed_p.add_argument(
        "--no-reset",
        action="store_true",
        help="Seed without resetting existing graph nodes first",
    )

    # Reset command
    subparsers.add_parser(
        "reset",
        parents=[sub_common],
        help="Reset knowledge graph and event store",
    )

    # Claim command
    claim_p = subparsers.add_parser(
        "claim",
        parents=[sub_common],
        help="Claim lease for a task node",
    )
    claim_p.add_argument("task_id", help="ID of task to claim (e.g. TSK-01)")
    claim_p.add_argument(
        "--idempotency-key",
        default=None,
        help="Custom idempotency key for deterministic replay",
    )

    # Subgraph command
    subgraph_p = subparsers.add_parser(
        "subgraph",
        parents=[sub_common],
        help="Retrieve scoped upstream context for a task",
    )
    subgraph_p.add_argument("task_id", help="ID of task to inspect")
    subgraph_p.add_argument(
        "--prompt",
        action="store_true",
        help="Format output directly as markdown agent instructions",
    )

    # Context alias
    context_p = subparsers.add_parser(
        "context",
        parents=[sub_common],
        help="Alias for subgraph --prompt",
    )
    context_p.add_argument("task_id", help="ID of task to inspect")

    # Submit command
    submit_p = subparsers.add_parser(
        "submit",
        parents=[sub_common],
        help="Submit task completion output",
    )
    submit_p.add_argument("task_id", help="ID of completed task")
    submit_p.add_argument(
        "--output",
        default=None,
        help="JSON string or text content representing output payload",
    )
    submit_p.add_argument(
        "--file",
        default=None,
        help="Path to file containing output content to submit",
    )
    submit_p.add_argument(
        "--idempotency-key",
        default=None,
        help="Custom idempotency key for submit operation",
    )

    # Listen command
    listen_p = subparsers.add_parser(
        "listen",
        parents=[sub_common],
        help="Listen for real-time conflict events on WebSocket",
    )
    listen_p.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Seconds to listen before exiting (default: 10.0)",
    )

    # Graph command
    subparsers.add_parser(
        "graph",
        parents=[sub_common],
        help="Retrieve full knowledge graph state",
    )

    # Export command
    export_p = subparsers.add_parser(
        "export",
        parents=[sub_common],
        help="Export full knowledge graph and event history as JSON snapshot",
    )
    export_p.add_argument(
        "file",
        nargs="?",
        default=None,
        help="Destination JSON file path (writes to stdout if omitted)",
    )
    export_p.add_argument(
        "--file",
        dest="file_flag",
        default=None,
        help="Destination JSON file path (alternative to positional file)",
    )

    # Import command
    import_p = subparsers.add_parser(
        "import",
        parents=[sub_common],
        help="Import a JSON snapshot into OmniHarness",
    )
    import_p.add_argument(
        "file",
        help="Path to snapshot JSON file to import",
    )
    import_p.add_argument(
        "--merge",
        action="store_true",
        help="Merge snapshot into existing graph instead of replacing (replace=false)",
    )

    return parser


def _print_error(message: str, status_code: int, as_json: bool) -> None:
    """Emit structured JSON or stderr error output."""
    if as_json:
        print(json.dumps({"error": message, "status_code": status_code}))
    else:
        prefix = f"[ERROR {status_code}]" if status_code else "[ERROR]"
        print(f"{prefix} {message}", file=sys.stderr)


def run_adapter_cli(
    adapter_cls: Type[BaseAgentAdapter],
    default_agent_id: str,
    prog: str,
    description: str,
    argv: Optional[List[str]] = None,
) -> int:
    """Execute the shared CLI workflow for a given adapter class."""
    parser = build_adapter_parser(
        prog=prog,
        description=description,
        default_agent_id=default_agent_id,
    )
    args = parser.parse_args(argv)
    adapter_name = adapter_cls.__name__
    as_json = bool(getattr(args, "json", False))

    resolved_server, resolved_agent_id = resolve_cli_config(
        cli_server=getattr(args, "server", None),
        cli_agent_id=getattr(args, "agent_id", None),
        default_agent_id=default_agent_id,
    )
    if args.command == "connect":
        resolved_server = args.server_url.strip().rstrip("/")

    adapter = adapter_cls(
        agent_id=resolved_agent_id,
        server_url=resolved_server,
        ws_url=getattr(args, "ws_url", None),
    )

    try:
        if args.command == "connect":
            profile_path = save_cli_profile(
                server_url=resolved_server,
                agent_id=resolved_agent_id,
                global_scope=bool(getattr(args, "global_config", False)),
            )
            health_data: Optional[Dict[str, Any]] = None
            health_error: Optional[str] = None
            try:
                res = adapter.client.get("/health")
                res.raise_for_status()
                health_data = res.json()
                try:
                    adapter.join_workspace()
                except Exception:
                    pass
            except Exception as exc:
                health_error = str(exc)

            if as_json:
                print(
                    json.dumps(
                        {
                            "saved": True,
                            "profile_path": str(profile_path),
                            "server": resolved_server,
                            "agent_id": resolved_agent_id,
                            "reachable": health_error is None,
                            "health": health_data,
                            "warning": health_error,
                        },
                        indent=2,
                    )
                )
            else:
                print(f"[{adapter_name}] Saved profile to {profile_path}")
                print(f"Server: {resolved_server} | Agent ID: {resolved_agent_id}")
                if health_error is None and health_data is not None:
                    nodes_count = health_data.get("nodes_count", "?")
                    print(f"Health: {health_data.get('status', 'ok')} (nodes: {nodes_count})")
                else:
                    print(f"[WARN] Profile saved, but /health check failed: {health_error}", file=sys.stderr)
            return 0

        elif args.command == "tasks":
            graph = adapter.get_graph()
            nodes = graph.get("nodes", [])
            ready_ids = set(_get_ready_task_ids(adapter, graph=graph))
            if as_json:
                enriched = [{**n, "ready": n.get("id") in ready_ids} for n in nodes]
                print(json.dumps(enriched, indent=2))
            else:
                print(f"\n[{adapter_name}] Shared Graph ({len(nodes)} nodes):")
                for n in nodes:
                    nid = n.get("id")
                    ready_tag = "[READY] " if nid in ready_ids else ""
                    assignee = f" -> assigned to {n.get('assigned_to')}" if n.get("assigned_to") else ""
                    print(f"  {ready_tag}[{n.get('type')}] {nid}: {n.get('title')} ({n.get('status')}){assignee}")
            return 0

        elif args.command == "users":
            res = adapter.client.get("/api/users")
            res.raise_for_status()
            users = res.json()
            if as_json:
                print(json.dumps(users, indent=2))
            else:
                print(f"\n[{adapter_name}] Workspace Users ({len(users)}):")
                if not users:
                    print("  (none)")
                for u in users:
                    active = ", ".join(u.get("active_tasks") or [])
                    active_note = f" | claiming: {active}" if active else ""
                    host_note = f" @ {u.get('client_host')}" if u.get("client_host") else ""
                    print(
                        f"  - {u.get('display_name')} ({u.get('user_id')}, {u.get('tool_name')}{host_note})"
                        f" [{u.get('status')}]{active_note}"
                    )
            return 0

        elif args.command == "start":
            target_task_id = args.task_id
            if not target_task_id:
                ready_list = _get_ready_task_ids(adapter)
                if not ready_list:
                    _print_error("No ready tasks available to start.", 404, as_json)
                    return 1
                target_task_id = ready_list[0]

            claim_res = adapter.claim_task(
                task_id=target_task_id,
                idempotency_key=args.idempotency_key,
            )
            prompt_text = adapter.format_context_prompt(target_task_id)
            copied = _copy_to_clipboard(prompt_text) if args.copy else False

            if as_json:
                print(
                    json.dumps(
                        {
                            "task_id": target_task_id,
                            "claim": claim_res,
                            "prompt": prompt_text,
                            "copied": copied,
                        },
                        indent=2,
                    )
                )
            else:
                copy_note = " | Prompt copied to clipboard" if copied else ""
                print(
                    f"[{adapter_name}] Started task: {target_task_id} "
                    f"(Status: {claim_res.get('status')} | Assigned to: {claim_res.get('assigned_to')}{copy_note})\n"
                )
                print(prompt_text)
            return 0

        elif args.command == "done":
            output_data = _resolve_submit_output(
                file_path=args.file,
                output_arg=args.output,
                summary_arg=args.summary,
                default_summary=f"Completed {args.task_id}",
            )
            res = adapter.submit_result(
                task_id=args.task_id,
                output=output_data,
                idempotency_key=args.idempotency_key,
            )
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                print(f"[{adapter_name}] Successfully submitted task: {args.task_id}")
                print(f"Status: {res.get('status')} | Version: {res.get('version')}")
            return 0

        elif args.command == "seed":
            params = {"reset": "false"} if args.no_reset else None
            res = adapter.client.post(f"/api/seed/{args.template}", params=params)
            res.raise_for_status()
            data = res.json()
            if as_json:
                print(json.dumps(data, indent=2))
            else:
                ready_str = ", ".join(data.get("ready_tasks", [])) or "none"
                print(
                    f"[{adapter_name}] Seeded '{args.template}' "
                    f"({data.get('nodes_created', 0)} created, {data.get('total_nodes', 0)} total). "
                    f"Ready: {ready_str}"
                )
            return 0

        elif args.command == "reset":
            res = adapter.client.post("/api/reset")
            res.raise_for_status()
            data = res.json()
            if as_json:
                print(json.dumps(data, indent=2))
            else:
                print(f"[{adapter_name}] Graph and event store successfully reset.")
            return 0

        elif args.command == "claim":
            res = adapter.claim_task(
                task_id=args.task_id,
                idempotency_key=args.idempotency_key,
            )
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                print(f"[{adapter_name}] Successfully claimed task: {args.task_id}")
                print(f"Status: {res.get('status')} | Assigned to: {res.get('assigned_to')}")
            return 0

        elif args.command == "subgraph":
            if args.prompt:
                prompt_text = adapter.format_context_prompt(args.task_id)
                print(prompt_text)
            else:
                data = adapter.get_subgraph(args.task_id)
                print(json.dumps(data, indent=2))
            return 0

        elif args.command == "context":
            prompt_text = adapter.format_context_prompt(args.task_id)
            print(prompt_text)
            return 0

        elif args.command == "submit":
            output_data = _resolve_submit_output(
                file_path=args.file,
                output_arg=args.output,
            )
            res = adapter.submit_result(
                task_id=args.task_id,
                output=output_data,
                idempotency_key=args.idempotency_key,
            )
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                print(f"[{adapter_name}] Successfully submitted task: {args.task_id}")
                print(f"Status: {res.get('status')} | Version: {res.get('version')}")
            return 0

        elif args.command == "listen":
            conflicts_received: List[Dict[str, Any]] = []

            def on_conflict(evt: Dict[str, Any]) -> None:
                conflicts_received.append(evt)
                print(f"[ALERT] Conflict detected: {evt.get('node_id')} - {evt.get('data')}")

            target_ws = getattr(args, "ws_url", None) or adapter.ws_url
            listener = adapter.listen_for_conflicts(on_conflict, ws_url=target_ws)
            print(f"[{adapter_name}] Listening for conflicts on {target_ws} for {args.timeout}s...")
            time.sleep(args.timeout)
            listener.stop()
            print(f"[{adapter_name}] Stopped listening. Total conflicts received: {len(conflicts_received)}")
            return 0

        elif args.command == "graph":
            res = adapter.get_graph()
            print(json.dumps(res, indent=2))
            return 0

        elif args.command == "export":
            res = adapter.client.get("/api/export")
            res.raise_for_status()
            data = res.json()
            target_file = getattr(args, "file_flag", None) or getattr(args, "file", None)
            if target_file:
                out_path = Path(target_file)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                if as_json:
                    print(
                        json.dumps(
                            {
                                "exported": True,
                                "file": str(out_path),
                                "nodes": len(data.get("nodes", [])),
                                "edges": len(data.get("edges", [])),
                                "events": len(data.get("events", [])),
                            },
                            indent=2,
                        )
                    )
                else:
                    print(
                        f"[{adapter_name}] Exported snapshot "
                        f"({len(data.get('nodes', []))} nodes, "
                        f"{len(data.get('edges', []))} edges, "
                        f"{len(data.get('events', []))} events) to {out_path}"
                    )
            else:
                print(json.dumps(data, indent=2))
            return 0

        elif args.command == "import":
            in_path = Path(args.file)
            if not in_path.is_file():
                _print_error(f"Snapshot file not found: {in_path}", 0, as_json)
                return 1
            snapshot_payload = json.loads(in_path.read_text(encoding="utf-8"))
            replace_flag = "false" if getattr(args, "merge", False) else "true"
            res = adapter.client.post(
                "/api/import",
                params={"replace": replace_flag},
                json=snapshot_payload,
            )
            res.raise_for_status()
            data = res.json()
            if as_json:
                print(json.dumps(data, indent=2))
            else:
                mode = "merged" if getattr(args, "merge", False) else "imported"
                print(
                    f"[{adapter_name}] Successfully {mode} snapshot from {in_path} "
                    f"({data.get('nodes_imported', 0)} nodes, "
                    f"{data.get('edges_imported', 0)} edges, "
                    f"{data.get('events_imported', 0)} events; "
                    f"total: {data.get('total_nodes', 0)} nodes, {data.get('total_edges', 0)} edges)"
                )
            return 0

    except httpx.HTTPStatusError as http_exc:
        status_code = http_exc.response.status_code
        try:
            detail = http_exc.response.json().get("detail", str(http_exc))
        except Exception:
            detail = http_exc.response.text or str(http_exc)
        _print_error(detail, status_code, as_json)
        if status_code == 409:
            return 2  # Conflict exit code
        return 1
    except Exception as exc:
        _print_error(str(exc), 500 if as_json else 0, as_json)
        return 1
    finally:
        adapter.close()

    return 0
