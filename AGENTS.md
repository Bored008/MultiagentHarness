# Project Guidelines: OmniHarness (MultiagentHarness)
NexHack 2.0 — Problem Statement 01: Multi-Agent Harness for Shared Team Knowledge Base

## 1. Project Overview & Architecture
- **Goal**: Enable seamless collaboration between heterogeneous AI agent tools (Claude Code CLI, OpenAI Codex / Cursor, Antigravity) working on a shared project.
- **Three Pillars**:
  1. **In-Memory DAG (NetworkX)**: Instant graph-based dependency resolution, blast-radius invalidation, and cycle detection.
  2. **SQLite Event Sourcing**: Append-only event log with `idempotency_key` ensuring crash recovery and zero duplicate state mutations.
  3. **Realtime WebSocket Hub**: Live bidirectional sync (<50ms) across multiple laptops and agents, broadcasting node additions, claim states, and conflict alerts.
- **Visual Interface**: React Flow / Cytoscape dashboard showing real-time task nodes, dependency links, conflict badges, and execution lineage.

## 2. Tech Stack & Standards
- **Backend**: Python 3.10+ with FastAPI, Uvicorn, WebSockets, NetworkX, Pydantic, SQLite (WAL mode).
- **Frontend / Dashboard**: React + `@xyflow/react` (React Flow) + Tailwind CSS + Lucide Icons.
- **Adapters**: Clean, standardized interfaces for Claude Code CLI and Codex/Antigravity submitting and claiming tasks.
- **Testing**: Deterministic unit tests with `pytest` verifying crash recovery, blast radius invalidation, and idempotency.

## 3. Memory & Protocol
- **L1 Context**: `notes/active-context.md` (via Obsidian junction `C:\Users\Bored\Documents\Obsidian Vault\NexHack-OmniHarness`).
- **Daily Logs**: `notes/daily/YYYY-MM-DD.md`.
- **Knowledge Graph**: Keep codebase mapped via `graphify .`.

## 4. Coding Agent Task Workflow (Zero-Ceremony)
When asked to work on a task from OmniHarness (local or `https://multiagentharness.onrender.com`):
- **Discover live state & protocol**: `GET /llms.txt` (or `/agent` / `/api/tasks/ready`).
- **One-time laptop setup**: `python -m adapters.<claude|codex|agy>_adapter connect <SERVER_URL> --agent-id <AGENT_ID>` (saves `.omniharness.json`).
- **Claim & pull scoped context**: `python -m adapters.<claude|codex|agy>_adapter start [TASK_ID]` (auto-picks next ready task if ID omitted).
- **Submit completed work**: `python -m adapters.<claude|codex|agy>_adapter done <TASK_ID> "<summary>"` (or `--file path/to/output.json`).
