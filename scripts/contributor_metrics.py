"""
OmniHarness Team Metrics & Contributor Utilities
Standalone helper for workspace tracking and reporting.
"""

from __future__ import annotations

import sys
import os
from typing import Dict, Any

METRICS_VERSION = "1.0.0"


def get_version() -> str:
    """Return contributor metrics version."""
    return METRICS_VERSION


def scan_workspace_summary(root_dir: str = ".") -> Dict[str, Any]:
    """Calculate summary statistics for repository files and modules."""
    stats = {"python_files": 0, "markdown_files": 0, "total_lines": 0}
    for dirpath, dirnames, filenames in os.walk(root_dir):
        if any(ignored in dirpath for ignored in [".git", "__pycache__", "venv", ".pytest_cache"]):
            continue
        for f in filenames:
            ext = os.path.splitext(f)[1].lower()
            if ext == ".py":
                stats["python_files"] += 1
            elif ext == ".md":
                stats["markdown_files"] += 1
    return stats


def main() -> int:
    import datetime
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("=" * 50)
    print(f"  OmniHarness Metrics Utility v{get_version()}")
    print(f"  Timestamp: {now_str}")
    print("=" * 50)
    summary = scan_workspace_summary()
    print(f"  • Python Modules  : {summary['python_files']}")
    print(f"  • Documentation   : {summary['markdown_files']}")
    print("=" * 50)
    print("  Status: Complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
