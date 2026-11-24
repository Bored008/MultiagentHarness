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

### 4. Active Implementation Priorities / Next Up
1. **Scaffold Project Structure**: Initialize `harness/` backend directory, virtual environment, and dependency manifest (`requirements.txt`).
2. **Implement Graph Core & SQLite Store**: Create `harness/graph.py` (NetworkX DAG manager) and `harness/store.py` (SQLite event log with idempotency constraints).
3. **Build FastAPI REST & WebSocket Server**: Expose `POST /task/claim`, `GET /task/{id}/subgraph`, `POST /task/submit`, and `POST /requirement/update` with live WebSocket broadcasting.
4. **Develop Universal Tool Adapters**: Build `adapters/claude_adapter.py` and `adapters/codex_adapter.py` sharing the exact same command contract.
5. **Build Visual Live Graph UI**: Implement a lightweight React Flow dashboard to visualize live node additions, dependency edges, and red conflict alerts.
6. **Rehearse Live Judge Pitch**: Dry-run the 4-minute multi-laptop demonstration script.
