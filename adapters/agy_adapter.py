"""
Antigravity (AGY) Agent Adapter for OmniHarness.
Subclasses BaseAgentAdapter with custom CLI entrypoint and Antigravity-specific prompt structures.
Enables Antigravity agents running across multiple laptops to discover, claim, execute, and submit
tasks within the shared OmniHarness graph.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx

from adapters.base import BaseAgentAdapter


class AntigravityAdapter(BaseAgentAdapter):
    """
    Adapter specialized for Google Antigravity (AGY).
    Enables Antigravity agents to discover, claim, execute, and submit
    tasks within the shared OmniHarness graph.
    """

    def __init__(
        self,
        agent_id: str = "antigravity-agent",
        server_url: str = "http://localhost:8000",
        ws_url: Optional[str] = None,
        client: Optional[httpx.Client] = None,
    ):
        super().__init__(
            agent_id=agent_id,
            server_url=server_url,
            ws_url=ws_url,
            client=client,
        )


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--server",
        default="http://localhost:8000",
        help="OmniHarness REST server base URL (default: http://localhost:8000)",
    )
    common.add_argument(
        "--agent-id",
        default="antigravity-agent",
        help="Agent identifier for logging and lease ownership (e.g. agy-laptop-1, agy-laptop-2)",
    )
    common.add_argument(
        "--json",
        action="store_true",
        help="Output results strictly as formatted JSON",
    )

    parser = argparse.ArgumentParser(
        prog="python -m adapters.agy_adapter",
        description="OmniHarness Adapter for Google Antigravity (AGY)",
        parents=[common],
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Claim command
    claim_p = subparsers.add_parser("claim", parents=[common], help="Claim lease for a task node")
    claim_p.add_argument("task_id", help="ID of task to claim (e.g. TSK-01, TSK-02)")
    claim_p.add_argument(
        "--idempotency-key",
        default=None,
        help="Custom idempotency key for deterministic replay",
    )

    # Subgraph command
    subgraph_p = subparsers.add_parser(
        "subgraph", parents=[common], help="Retrieve scoped upstream context for a task"
    )
    subgraph_p.add_argument("task_id", help="ID of task to inspect")
    subgraph_p.add_argument(
        "--prompt",
        action="store_true",
        help="Format output directly as markdown agent instructions",
    )

    # Submit command
    submit_p = subparsers.add_parser("submit", parents=[common], help="Submit task completion output")
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

    # List tasks command
    subparsers.add_parser("tasks", parents=[common], help="List all graph nodes and tasks")

    # Reset command
    subparsers.add_parser("reset", parents=[common], help="Reset knowledge graph and event log")

    # Listen command
    listen_p = subparsers.add_parser(
        "listen", parents=[common], help="Listen for real-time conflict events on WebSocket"
    )
    listen_p.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Seconds to listen before exiting (default: 10.0)",
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    adapter = AntigravityAdapter(
        agent_id=args.agent_id,
        server_url=args.server,
    )

    try:
        if args.command == "claim":
            res = adapter.claim_task(
                task_id=args.task_id,
                idempotency_key=args.idempotency_key,
            )
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"[AntigravityAdapter] Successfully claimed task: {args.task_id}")
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

        elif args.command == "submit":
            output_data: Dict[str, Any] = {}
            if args.file:
                content = Path(args.file).read_text(encoding="utf-8")
                try:
                    output_data = json.loads(content)
                except Exception:
                    output_data = {"content": content}
            elif args.output:
                try:
                    output_data = json.loads(args.output)
                except Exception:
                    output_data = {"summary": args.output}
            else:
                output_data = {"completed": True, "timestamp": time.time()}

            res = adapter.submit_result(
                task_id=args.task_id,
                output=output_data,
                idempotency_key=args.idempotency_key,
            )
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"[AntigravityAdapter] Successfully submitted task: {args.task_id}")
                print(f"Status: {res.get('status')} | Version: {res.get('version')}")
            return 0

        elif args.command == "tasks":
            graph = adapter.get_graph()
            nodes = graph.get("nodes", [])
            if args.json:
                print(json.dumps(nodes, indent=2))
            else:
                print(f"\n[AntigravityAdapter] Shared Graph ({len(nodes)} nodes):")
                for n in nodes:
                    assignee = f" -> assigned to {n.get('assigned_to')}" if n.get('assigned_to') else ""
                    print(f"  [{n.get('type')}] {n.get('id')}: {n.get('title')} ({n.get('status')}){assignee}")
            return 0

        elif args.command == "reset":
            res = adapter.client.post("/api/reset")
            res.raise_for_status()
            print("[AntigravityAdapter] Graph and event store successfully reset.")
            return 0

        elif args.command == "listen":
            conflicts_received: List[Dict[str, Any]] = []

            def on_conflict(evt: Dict[str, Any]) -> None:
                conflicts_received.append(evt)
                print(f"[ALERT] Conflict detected: {evt.get('node_id')} - {evt.get('data')}")

            listener = adapter.listen_for_conflicts(on_conflict)
            print(f"[AntigravityAdapter] Listening for conflicts on {adapter.ws_url} for {args.timeout}s...")
            time.sleep(args.timeout)
            listener.stop()
            print(f"[AntigravityAdapter] Stopped listening. Total conflicts received: {len(conflicts_received)}")
            return 0

    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    finally:
        adapter.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
