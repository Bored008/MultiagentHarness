"""
Seed initial demo graph nodes (REQ-01, DEC-01, TSK-01, TSK-02, TSK-03)
into SYNAPSE for live two-laptop testing.
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

DEFAULT_SERVER = os.environ.get("SYNAPSE_SERVER", "http://127.0.0.1:8000")


def seed_graph(server_url: str = DEFAULT_SERVER) -> None:
    server_url = server_url.rstrip("/")
    print(f"Connecting to SYNAPSE on {server_url}...")

    client = httpx.Client(base_url=server_url, timeout=10.0)

    try:
        fast = client.post("/api/seed/auth")
        if fast.status_code == 200:
            info = fast.json()
            print(f"  ✔ Seeded 'auth' via /api/seed/auth ({info.get('total_nodes')} nodes)")
            print(f"  Ready tasks: {info.get('ready_tasks', [])}")
            print(f"\nOpen dashboard: {server_url}/dashboard")
            return
    except Exception:
        pass

    try:
        r = client.post("/api/reset")
        if r.status_code == 200:
            print("  [x] Reset graph to clean state.")
    except Exception as e:
        print(f"  [!] Reset note: {e}")

    for n in get_seed_nodes("auth"):
        res = client.post("/api/nodes", json=n)
        if res.status_code in (200, 201):
            print(f"  ✔ Created [{n['type']}] {n['id']}: {n['title']}")
        else:
            print(f"  ✖ Failed {n['id']}: {res.status_code} {res.text}")

    print("\nGraph successfully seeded! Ready for live multi-laptop collaboration.")
    print(f"Open dashboard: {server_url}/dashboard")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed initial SYNAPSE nodes for live testing")
    parser.add_argument("--server", default=DEFAULT_SERVER, help="SYNAPSE server URL")
    args = parser.parse_args()
    seed_graph(args.server)
