"""
Pure domain helpers for OmniHarness 4-letter project slug derivation and preview resolution.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Sequence, Set

from harness.models import Node

RESERVED_SLUGS: Set[str] = {
    "api",
    "ws",
    "docs",
    "redo",
    "open",
    "heal",
    "dash",
    "llms",
    "agen",
    "asse",
    "task",
    "node",
    "grap",
    "expo",
    "impo",
    "rese",
    "requ",
    "seed",
    "mcp",
}

IGNORED_PROJECT_DIRS: Set[str] = {
    "harness",
    "adapters",
    "tests",
    "scripts",
    "notes",
    "graphify-out",
    "workflows",
    "auth",
    "__pycache__",
}

_SLUG_RE = re.compile(r"[a-z0-9]{1,4}")


@dataclass(frozen=True)
class ProjectResolution:
    """Resolved preview content for a 4-letter project slug."""

    kind: Literal["html", "pending"]
    slug: str
    html: str
    source_node_id: Optional[str] = None


def derive_project_slug(name: str) -> str:
    """Strip non-[a-z0-9] characters from lowercased name and return up to 4 chars."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())[:4]


def is_valid_project_slug(slug: str) -> bool:
    """Return True if slug is 1..4 lowercase alphanumeric chars and not reserved."""
    return bool(slug and _SLUG_RE.fullmatch(slug) and slug not in RESERVED_SLUGS)


def node_project_slug(node: Node) -> str:
    """Derive the first valid non-reserved 4-letter project slug from a DAG node."""
    payload = node.payload if isinstance(node.payload, dict) else {}
    candidates = (
        payload.get("project_slug"),
        payload.get("slug"),
        payload.get("project"),
        node.title,
        payload.get("app_name"),
        node.id,
    )
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        slug = derive_project_slug(candidate)
        if is_valid_project_slug(slug):
            return slug
    return ""


def _extract_html_from_mapping(mapping: Dict[str, Any]) -> Optional[str]:
    for key in ("html", "index_html"):
        val = mapping.get(key)
        if isinstance(val, str) and val.strip():
            return val

    files = mapping.get("files")
    if isinstance(files, dict):
        index_html = files.get("index.html")
        if isinstance(index_html, str) and index_html.strip():
            return index_html

    content = mapping.get("content")
    if isinstance(content, str):
        lowered = content.lower()
        if "<html" in lowered or "<!doctype" in lowered:
            return content

    return None


def extract_html_from_node(node: Node) -> Optional[str]:
    """Extract rendered HTML from node payload.output or top-level payload."""
    payload = node.payload if isinstance(node.payload, dict) else {}
    output = payload.get("output")
    if isinstance(output, dict):
        found = _extract_html_from_mapping(output)
        if found is not None:
            return found
    return _extract_html_from_mapping(payload)


def _safe_file_in_dir(proj_dir: Path, asset_path: str) -> Optional[Path]:
    if not asset_path or Path(asset_path).is_absolute():
        return None
    proj_root = proj_dir.resolve()
    candidate = (proj_dir / asset_path).resolve()
    if not candidate.is_relative_to(proj_root) or not candidate.is_file():
        return None
    return candidate


def find_disk_project_file(
    root: Path,
    slug: str,
    asset_path: str = "index.html",
) -> Optional[Path]:
    """Locate a safe file inside a project directory matching the 4-letter slug."""
    if not is_valid_project_slug(slug) or not root.exists() or not root.is_dir():
        return None

    direct_dir = root / slug
    if (
        direct_dir.is_dir()
        and not direct_dir.name.startswith(".")
        and direct_dir.name not in IGNORED_PROJECT_DIRS
    ):
        found = _safe_file_in_dir(direct_dir, asset_path)
        if found is not None:
            return found

    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if (
            not entry.is_dir()
            or entry.name.startswith(".")
            or entry.name in IGNORED_PROJECT_DIRS
        ):
            continue
        if derive_project_slug(entry.name) == slug:
            found = _safe_file_in_dir(entry, asset_path)
            if found is not None:
                return found

    return None


def _render_pending_status_html(slug: str, matching_nodes: Sequence[Node]) -> str:
    safe_slug = html.escape(slug)
    rows = []
    for node in matching_nodes:
        status_text = node.status.value if hasattr(node.status, "value") else str(node.status)
        node_type = node.type.value if hasattr(node.type, "value") else str(node.type)
        assigned = f"@{node.assigned_to}" if node.assigned_to else "Unassigned"
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(node.id)}</code></td>"
            f"<td>{html.escape(node.title)}</td>"
            f"<td>{html.escape(node_type)}</td>"
            f"<td>{html.escape(status_text)}</td>"
            f"<td>{html.escape(assigned)}</td>"
            "</tr>"
        )
    rows_html = "\n".join(rows)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>/{safe_slug} — Project Preview Pending</title>
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; background: #fafbfd; color: #1f2937; margin: 0; padding: 2rem; }}
    .card {{ max-width: 780px; margin: 0 auto; background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 1.5rem; box-shadow: 0 2px 8px rgba(0,0,0,0.04); }}
    h1 {{ margin: 0 0 0.5rem; font-size: 1.25rem; }}
    p {{ color: #4b5563; line-height: 1.5; }}
    table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 0.875rem; }}
    th, td {{ text-align: left; padding: 0.5rem 0.625rem; border-bottom: 1px solid #e5e7eb; }}
    th {{ color: #6b7280; font-weight: 600; }}
    code, pre {{ background: #f3f4f6; padding: 0.15rem 0.35rem; border-radius: 4px; font-size: 0.85em; }}
    pre {{ padding: 0.75rem; overflow-x: auto; }}
    a {{ color: #2563eb; text-decoration: none; font-weight: 500; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Project <code>/{safe_slug}</code> is awaiting HTML output</h1>
    <p>Matching DAG nodes exist for <code>/{safe_slug}</code>, but no HTML artifact has been submitted or written to disk yet.</p>
    <table>
      <thead>
        <tr><th>ID</th><th>Title</th><th>Type</th><th>Status</th><th>Assigned</th></tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
    <p>Publish a live preview by submitting HTML via an agent adapter or saving a disk file:</p>
    <pre>python -m adapters.codex_adapter done &lt;TASK_ID&gt; --file index.html
# or save &lt;dir&gt;/index.html where the first 4 alphanumeric chars of &lt;dir&gt; are "{safe_slug}"</pre>
    <p><a href="/dashboard">Back to SYNAPSE Dashboard</a></p>
  </div>
</body>
</html>"""


def resolve_project(
    slug: str,
    nodes: Sequence[Node],
    root: Path,
) -> Optional[ProjectResolution]:
    """Resolve a 4-letter project slug to submitted HTML, disk index.html, or a pending status page."""
    if not is_valid_project_slug(slug):
        return None

    matching_nodes = sorted(
        (n for n in nodes if node_project_slug(n) == slug),
        key=lambda n: n.updated_at,
        reverse=True,
    )

    for node in matching_nodes:
        extracted = extract_html_from_node(node)
        if extracted is not None:
            return ProjectResolution(
                kind="html",
                slug=slug,
                html=extracted,
                source_node_id=node.id,
            )

    disk_index = find_disk_project_file(root, slug, "index.html")
    if disk_index is not None:
        return ProjectResolution(
            kind="html",
            slug=slug,
            html=disk_index.read_text(encoding="utf-8"),
        )

    if matching_nodes:
        return ProjectResolution(
            kind="pending",
            slug=slug,
            html=_render_pending_status_html(slug, matching_nodes),
        )

    return None


def list_project_links(nodes: Sequence[Node], root: Path) -> Dict[str, str]:
    """Return sorted mapping of active project slugs to relative preview paths."""
    slugs: Set[str] = set()

    for node in nodes:
        slug = node_project_slug(node)
        if slug:
            slugs.add(slug)

    if root.exists() and root.is_dir():
        for entry in root.iterdir():
            if (
                not entry.is_dir()
                or entry.name.startswith(".")
                or entry.name in IGNORED_PROJECT_DIRS
            ):
                continue
            if (entry / "index.html").is_file():
                slug = derive_project_slug(entry.name)
                if is_valid_project_slug(slug):
                    slugs.add(slug)

    return {slug: f"/{slug}" for slug in sorted(slugs)}
