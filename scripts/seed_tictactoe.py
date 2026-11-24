"""
Seed Tic Tac Toe Multi-Agent Plan into OmniHarness.
Connects to OmniHarness REST API, resets graph, and posts full plan DAG
(REQ-01, DEC-01, TSK-01, TSK-02, TSK-03, TSK-04, TSK-05, ART-01).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from harness.seeds import get_seed_nodes

DEFAULT_SERVER = os.environ.get("OMNIHARNESS_SERVER", "https://multiagentharness.onrender.com")


def seed_tictactoe(server_url: str = DEFAULT_SERVER) -> None:
    server_url = server_url.rstrip("/")
    print(f"Connecting to OmniHarness on {server_url}...")

    client = httpx.Client(base_url=server_url, timeout=15.0)

    # Prefer fast single-call server seed endpoint when available
    try:
        fast = client.post("/api/seed/tictactoe")
        if fast.status_code == 200:
            info = fast.json()
            print(f"  ✔ Seeded 'tictactoe' via /api/seed/tictactoe ({info.get('total_nodes')} nodes)")
            print(f"  Ready tasks: {info.get('ready_tasks', [])}")
            print(f"\nLive Dashboard: {server_url}/dashboard")
            return
    except Exception:
        pass

    # Fallback: step-by-step reset + node creation
    try:
        r = client.post("/api/reset")
        if r.status_code == 200:
            print("  [x] Reset graph to clean state.")
        else:
            print(f"  [!] Reset returned status {r.status_code}: {r.text}")
    except Exception as e:
        print(f"  [!] Reset note: {e}")

    for n in get_seed_nodes("tictactoe"):
        res = client.post("/api/nodes", json=n)
        if res.status_code in (200, 201):
            print(f"  ✔ Created [{n['type']}] {n['id']}: {n['title']}")
        else:
            print(f"  ✖ Failed {n['id']}: {res.status_code} {res.text}")

    r_graph = client.get("/api/graph")
    if r_graph.status_code == 200:
        graph_data = r_graph.json()
        stats = graph_data.get("stats", {})
        print(f"\nLive Graph Seeding Complete!")
        print(f"  Total Nodes: {stats.get('total_nodes', len(graph_data.get('nodes', [])))}")
        print(f"  Total Edges: {stats.get('total_edges', len(graph_data.get('edges', [])))}")
        print(f"  Pending Tasks: {stats.get('pending', 0)}")
    print(f"\nLive Dashboard: {server_url}/dashboard")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Tic Tac Toe Multi-Agent Plan into OmniHarness")
    parser.add_argument("--server", default=DEFAULT_SERVER, help="OmniHarness server URL")
    args = parser.parse_args()
    seed_tictactoe(args.server)
