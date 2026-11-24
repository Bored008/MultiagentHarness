"""
Seed initial demo graph nodes (REQ-01, DEC-01, TSK-01, TSK-02, TSK-03)
into OmniHarness for live two-laptop testing.
"""
from __future__ import annotations

import argparse
import sys
import httpx


def seed_graph(server_url: str = "http://127.0.0.1:8000") -> None:
    server_url = server_url.rstrip("/")
    print(f"Connecting to OmniHarness on {server_url}...")

    client = httpx.Client(base_url=server_url, timeout=10.0)

    # 1. Reset to clean state
    try:
        r = client.post("/api/reset")
        if r.status_code == 200:
            print("  [x] Reset graph to clean state.")
    except Exception as e:
        print(f"  [!] Reset note: {e}")

    # 2. Seed initial nodes
    nodes = [
        {
            "id": "REQ-01",
            "title": "Auth Token Specification",
            "type": "REQUIREMENT",
            "payload": {"spec": "JWT RS256 with 15min expiry", "algorithm": "RS256"},
            "dependencies": [],
            "actor": "product-architect",
            "idempotency_key": "seed-req-01",
        },
        {
            "id": "DEC-01",
            "title": "JWT Token Architecture Decision",
            "type": "DECISION",
            "payload": {"decision": "Stateless asymmetric JWT architecture", "crypto": "RS256"},
            "dependencies": ["REQ-01"],
            "actor": "security-lead",
            "idempotency_key": "seed-dec-01",
        },
        {
            "id": "TSK-01",
            "title": "Auth Token Schema & Types",
            "type": "TASK",
            "payload": {"instructions": "Define Pydantic token validation schemas"},
            "dependencies": ["DEC-01"],
            "actor": "claude-code-cli",
            "idempotency_key": "seed-tsk-01",
        },
        {
            "id": "TSK-02",
            "title": "Token Verifier Middleware",
            "type": "TASK",
            "payload": {"instructions": "Implement FastAPI Bearer token validator"},
            "dependencies": ["TSK-01"],
            "actor": "openai-codex",
            "idempotency_key": "seed-tsk-02",
        },
        {
            "id": "TSK-03",
            "title": "FastAPI Token Verification Route",
            "type": "TASK",
            "payload": {"instructions": "Expose /auth/verify verification endpoint"},
            "dependencies": ["DEC-01"],
            "actor": "claude-code-cli",
            "idempotency_key": "seed-tsk-03",
        },
    ]

    for n in nodes:
        res = client.post("/api/nodes", json=n)
        if res.status_code in (200, 201):
            print(f"  ✔ Created [{n['type']}] {n['id']}: {n['title']}")
        else:
            print(f"  ✖ Failed {n['id']}: {res.status_code} {res.text}")

    print("\nGraph successfully seeded! Ready for live multi-laptop collaboration.")
    print(f"Open dashboard: {server_url}/dashboard")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed initial OmniHarness nodes for live testing")
    parser.add_argument("--server", default="http://127.0.0.1:8000", help="OmniHarness server URL")
    args = parser.parse_args()
    seed_graph(args.server)
