"""
OpenAI Codex / Cursor Adapter for OmniHarness.
Subclasses BaseAgentAdapter with custom CLI entrypoint and Cursor-compatible prompts.
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


class CodexAdapter(BaseAgentAdapter):
    """
    Adapter specialized for OpenAI Codex / Cursor.
    Enables Codex / Cursor agents to discover, claim, execute, and submit
    tasks within the shared OmniHarness graph.
    """

    def __init__(
        self,
        agent_id: str = "openai-codex-cursor",
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
        "--ws-url",
        default=None,
        help="Custom WebSocket URL for real-time events (default: derived from --server)",
    )
    common.add_argument(
        "--agent-id",
        default="openai-codex-cursor",
        help="Agent identifier for logging and lease ownership",
    )
    common.add_argument(
        "--json",
        action="store_true",
        help="Output results strictly as formatted JSON",
    )

    parser = argparse.ArgumentParser(
        prog="python -m adapters.codex_adapter",
        description="OmniHarness Adapter for OpenAI Codex / Cursor",
        parents=[common],
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Claim command
    claim_p = subparsers.add_parser("claim", parents=[common], help="Claim lease for a task node")
    claim_p.add_argument("task_id", help="ID of task to claim (e.g. TSK-02)")
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

    # Context alias
    context_p = subparsers.add_parser(
        "context", parents=[common], help="Alias for subgraph --prompt"
    )
    context_p.add_argument("task_id", help="ID of task to inspect")

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

    # Graph command
    subparsers.add_parser(
        "graph", parents=[common], help="Retrieve full knowledge graph state"
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    adapter = CodexAdapter(
        agent_id=args.agent_id,
        server_url=args.server,
        ws_url=getattr(args, "ws_url", None),
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
                print(f"[CodexAdapter] Successfully claimed task: {args.task_id}")
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
                print(f"[CodexAdapter] Successfully submitted task: {args.task_id}")
                print(f"Status: {res.get('status')} | Version: {res.get('version')}")
            return 0

        elif args.command == "listen":
            conflicts_received: List[Dict[str, Any]] = []

            def on_conflict(evt: Dict[str, Any]) -> None:
                conflicts_received.append(evt)
                print(f"[ALERT] Conflict detected: {evt.get('node_id')} - {evt.get('data')}")

            target_ws = getattr(args, "ws_url", None) or adapter.ws_url
            listener = adapter.listen_for_conflicts(on_conflict, ws_url=target_ws)
            print(f"[CodexAdapter] Listening for conflicts on {target_ws} for {args.timeout}s...")
            time.sleep(args.timeout)
            listener.stop()
            print(f"[CodexAdapter] Stopped listening. Total conflicts received: {len(conflicts_received)}")
            return 0

        elif args.command == "graph":
            res = adapter.get_graph()
            print(json.dumps(res, indent=2))
            return 0

    except httpx.HTTPStatusError as http_exc:
        status_code = http_exc.response.status_code
        try:
            detail = http_exc.response.json().get("detail", str(http_exc))
        except Exception:
            detail = http_exc.response.text or str(http_exc)
        if getattr(args, "json", False):
            print(json.dumps({"error": detail, "status_code": status_code}))
        else:
            print(f"[ERROR {status_code}] {detail}", file=sys.stderr)
        if status_code == 409:
            return 2  # Conflict exit code
        return 1
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc), "status_code": 500}))
        else:
            print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    finally:
        adapter.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
