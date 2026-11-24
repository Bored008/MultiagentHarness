# NexHack 2.0 — OmniHarness (Problem Statement 01)
## Active Memory & Project Context

> **Agent Instruction:** This is your primary L1 memory cache. Read this file at the start of any conversation to understand current architecture, recent progress, and active priorities. Do NOT crawl all files unless specifically requested.

---

### 1. Project Identity & Overview
- **Project Name**: `OmniHarness` (Multi-Agent Harness for Shared Team Knowledge Base)
- **Target Event**: NexHack 2.0 — Problem Statement 01 (Generative AI Track)
- **Core Problem**: Two teammates using different AI tools (e.g., Claude Code CLI & OpenAI Codex / Cursor / Antigravity) need a shared, tool-agnostic team knowledge base. Tasks, decisions, and drafts must hand off smoothly without copy-pasting chat histories, withstand mid-execution requirement changes with automated conflict detection, recover idempotently from crashes, and provide an immutable evidence trail.
- **Key Differentiator**: Instead of static markdown files or slow GitHub commits, uses a **Live Dynamic Graphify Knowledge Graph** (In-Memory DAG via NetworkX) with **Realtime WebSockets**, backed by an **SQLite Event Log** for crash idempotency.

---

### 2. Core Architecture & Tech Stack
```
[ User 1: Claude Code CLI ]                [ User 2: Codex / Antigravity ]
            \                                            /
             \ (MCP / REST)                             / (MCP / REST)
              v                                        v
       +------------------------------------------------------+
       |        OmniHarness Backend (Python FastAPI)          |
       |  - In-Memory DAG (NetworkX) for instant blast radius |
       |  - SQLite Event Log (Idempotency keys, crash safe)   |
       |  - WebSocket Event Broadcaster (< 50ms sync)         |
       +--------------------------+---------------------------+
                                  |
                                  v
                    [ Live Visual Dashboard ]
                  React Flow / Cytoscape UI
                  - Realtime node graph
                  - Red pulsing conflict alerts
                  - Clickable audit/lineage trace
```

- **Backend**: Python 3.11+ (FastAPI + WebSockets + Uvicorn)
- **Graph Engine**: `networkx` for DAG traversal, cycle prevention, and downstream blast-radius invalidation
- **Persistence & Idempotency**: SQLite with WAL mode (`task_events`, `idempotency_key`, `node_revisions`)
- **Adapters**:
  - `adapters/claude_adapter.py`: Standard interface for Claude Code CLI / Anthropic API
  - `adapters/codex_adapter.py`: Standard interface for OpenAI Codex / GPT-4 / Cursor
  - Operations: `claim_task`, `get_task_subgraph`, `submit_result`
- **Network Tunnel**: `ngrok` or `Cloudflare Tunnel` for live multi-laptop collaboration
- **Frontend**: React + `@xyflow/react` (React Flow) / Tailwind CSS

---

### 3. Solved Design Decisions & Specifications
- [x] **Graph vs. Markdown**: Validated that an in-memory DAG with revisioned nodes (`REQ-01@v2`) outperforms static GitHub markdown by enabling instant sub-millisecond blast-radius invalidation.
- [x] **Context Optimization**: Replaced bloated full-repo context with scoped subgraphs (~1.5k tokens) containing only the claimed task, active requirement, and immediate parent decision.
- [x] **Live Multi-User Sync**: Architected live WebSocket event broadcasting so two separate laptops update their visual graph and agent state concurrently.
- [x] **Crash Recovery Protocol**: Implemented deterministic event sourcing with `idempotency_key` to ensure interrupted runs apply updates exactly once.
- [x] **Demo Strategy Script**: Formulated a 4-minute judge demonstration covering task claim, interruption recovery, live requirement shift, and visual evidence lineage.

---

### 4. Active Implementation Priorities & Status
- [x] **Scaffold Project Structure**: `harness/` initialized with models, store, graph engine, and server.
- [x] **Implement Graph Core & SQLite Store**: `harness/graph.py` (NetworkX DAG manager, cycle rejection, blast-radius invalidation, scoped subgraphs) and `harness/store.py` (SQLite WAL event log with idempotency constraints).
- [x] **Build FastAPI REST & WebSocket Server**: `harness/server.py` exposing REST endpoints (`/api/nodes`, `/api/tasks`, `/api/requirements`) and WebSocket `/ws/graph` broadcasting (`< 50ms` latency).
- [x] **Develop Universal Tool Adapters**: `adapters/claude_adapter.py` and `adapters/codex_adapter.py` subclassing `BaseAgentAdapter` with real-time WebSocket conflict listening and CLI entrypoints.
- [x] **Visual 3D Depth Live Graph UI**: Merged `origin/feature/frontend-3d-redesign` into `main` featuring [harness/dashboard.html](file:///c:/Project/MultiagentHarness/harness/dashboard.html) with clean light theme, animated flows, 3D depth shadows, ambient floating particles, interactive node drag-and-drop repositioning, and conflict pulse glow rings.
- [x] **Comprehensive Test Verification**: 74 passing unit and integration tests across all test suites (`test_store.py`, `test_graph.py`, `test_server.py`, `test_adapters.py`, `test_auth_middleware.py`).
- [x] **Two-Laptop Testing & Tunnel WSS Auto-Detection**: Added `scripts/seed_demo.py` and `scripts/seed.bat`; updated `dashboard.html` for zero-friction HTTPS/WSS compatibility over Cloudflare tunnels and Render.
- [x] **Render.com Docker Deployment**: Added `Dockerfile`, `.dockerignore`, and `render.yaml`; updated `harness/server.py` (`OMNIHARNESS_DB_PATH`, `PORT`) and `harness/store.py` (auto-mkdir). Deployed live at `https://multiagentharness.onrender.com`.
- [x] **Agent-Native Protocol & One-Command Adapter CLI**: Added `harness/seeds.py` + `POST /api/seed/{template}` + `GET /api/tasks/ready` + `GET /llms.txt` / `GET /agent` in `harness/server.py`; kept `harness/dashboard.html` clean with only the empty-graph reset fix and a zero-UI `OMNIHARNESS AGENT PROTOCOL` HTML comment; extracted shared `adapters/cli.py` with `connect`, `tasks`, `start`, `done`, `seed`, `reset` + `.omniharness.json`/env config resolution across all 3 adapters; documented the 3-line agent workflow in `AGENTS.md`.

- [x] **Figma Dashboard Consistency & Viewability Redesign**: Updated [harness/dashboard.html](file:///c:/Project/MultiagentHarness/harness/dashboard.html) to match Figma frame `7:2` (`tLMMvLQEAJ7u2mNhfUAqXO`): `#fafbfd` dot-grid canvas, 3-row node cards (`188x88px`), orthogonal stepped & dashed skip-level dependency routing, clickable Type/Status sidebar filters, collapsible Right Inspector Drawer, Help modal, `fitGraph()` zoom-to-fit, and dynamic non-destructive `Simulate Blast` preview.
- [x] **Zero-Overhead Snapshot Export/Import & Startup Hydration**: Added `GraphSnapshot` (`harness/models.py`), `list_events` / `export_snapshot` / idempotent `import_snapshot` (`harness/store.py` & `harness/graph.py`), `GET /api/export` + `POST /api/import` + `OMNIHARNESS_SNAPSHOT_FILE` empty-DB startup hydration (`harness/server.py`), and `export` / `import [--merge]` CLI commands (`adapters/cli.py`).

### 5. Next Steps for Demonstration & Deployment
1. **Export Before Pushing (Optional)**: Run `python -m adapters.codex_adapter export snapshot.json` against the live server before `git push` if you want to restore live history after Render rebuilds (`import snapshot.json`), or set `OMNIHARNESS_SNAPSHOT_FILE` to auto-seed an empty ephemeral container on boot.
2. **Push & Deploy to Render**: Push updated `main` so `https://multiagentharness.onrender.com` serves `/llms.txt`, `/agent`, `/api/export`, `/api/import`, `/api/seed/{template}`, `/api/tasks/ready`, and the Figma-aligned `dashboard.html`.
3. **Agent-First Flow**: Point any coding agent at `https://multiagentharness.onrender.com/llms.txt` (or tell it in the repo to claim and complete a task using `AGENTS.md` / `adapters/cli.py`), then watch the live DAG update cleanly on `/dashboard`.

---

### 6. Clean Handoff Snapshot (2026-11-25 02:00 IST)
- **Live Server**: `https://multiagentharness.onrender.com` (`/dashboard`, `/llms.txt`, `/agent`, `/docs`, `/health`)
- **Latest Pushed Commit**: `dc7b5ab` on `origin/main` (local improvements ready to commit & push)
- **Files Created/Modified in Session**: `harness/models.py`, `harness/store.py`, `harness/graph.py`, `harness/server.py`, `adapters/cli.py`, `tests/test_store.py`, `tests/test_server.py`, `tests/test_adapters.py`, `notes/active-context.md`, `notes/daily/2026-11-25.md`
- **Verification**: `python -m pytest -q` (80 passed in 8.45s).
- **Next Actions**: Commit and push local changes to `origin/main` so Render auto-deploys `/llms.txt`, `/api/seed/{template}`, and the clean `dashboard.html`.
