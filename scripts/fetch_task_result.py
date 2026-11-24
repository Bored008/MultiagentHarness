"""
Fetch and display the completed task output from OmniHarness on Laptop 1.
"""
from __future__ import annotations

import argparse
import json
import urllib.request
import urllib.error

SERVER_URL = "https://jewel-untitled-respective-devel.trycloudflare.com"


def fetch_node(node_id: str, server_url: str = SERVER_URL) -> None:
    url = f"{server_url.rstrip('/')}/nodes/{node_id}"
    print(f"Fetching {node_id} from {url}...\n")
    try:
        with urllib.request.urlopen(url) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"Error {e.code}: {e.read().decode('utf-8')}")
        return

    print(f"==================================================")
    print(f"Node ID:       {data.get('id')}")
    print(f"Title:         {data.get('title')}")
    print(f"Type:          {data.get('type')}")
    print(f"Status:        {data.get('status')}")
    print(f"Assigned To:   {data.get('assigned_to')}")
    print(f"Version:       {data.get('version')}")
    print(f"==================================================\n")

    payload = data.get("payload", {})
    output = payload.get("output")

    if output:
        print("🎉 Output submitted by 2nd Laptop:")
        print(json.dumps(output, indent=2))
    else:
        print("⏳ Task not yet completed. Current payload:")
        print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch task result from OmniHarness")
    parser.add_argument("task_id", nargs="?", default="TSK-04", help="Task ID (default: TSK-04)")
    parser.add_argument("--server", default=SERVER_URL, help="OmniHarness server URL")
    args = parser.parse_args()
    fetch_node(args.task_id, args.server)
