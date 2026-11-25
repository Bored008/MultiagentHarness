# MultiagentHarness

> **OmniHarness**: Tool-Agnostic Multi-Agent Collaboration Harness for Shared Team Knowledge Base  
> Built for **NexHack 2.0 — Problem Statement 01 (Generative AI Track)**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com)
[![NetworkX](https://img.shields.io/badge/NetworkX-DAG_Engine-orange.svg)](https://networkx.org/)
[![SQLite](https://img.shields.io/badge/SQLite-WAL_Event_Log-003B57.svg)](https://sqlite.org)
[![Supabase](https://img.shields.io/badge/Supabase-Auth_%26_Postgres-3ECF8E.svg)](https://supabase.com)
[![Live on Render](https://img.shields.io/badge/Live_Demo-Render-46E3B7.svg)](https://multiagentharness.onrender.com/dashboard)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

### 🌐 Live Deployment
- **SYNAPSE Dashboard**: [https://multiagentharness.onrender.com/dashboard](https://multiagentharness.onrender.com/dashboard)
- **Hosted MCP Endpoint**: `https://multiagentharness.onrender.com/mcp` (`Authorization: Bearer <BOARD_AGENT_KEY>`)
- **API Docs**: [https://multiagentharness.onrender.com/docs](https://multiagentharness.onrender.com/docs)
- **Health Check**: [https://multiagentharness.onrender.com/health](https://multiagentharness.onrender.com/health)

---

## 🌟 Overview

When multiple engineers work with different AI developer tools (e.g., **Claude Code CLI**, **OpenAI Codex / Cursor**, or **Antigravity**), collaborating on a shared project often results in fragmented context, duplicate effort, and costly coordination overhead.

**OmniHarness** provides a live, tool-agnostic team knowledge base and execution harness that:
- **Maintains a Dynamic Knowledge Graph (In-Memory DAG via NetworkX)** for instant dependency resolution and sub-millisecond blast-radius invalidation.
- **Ensures Crash Recovery & Zero-Loss State Mutations** via an SQLite append-only event sourcing log with strict `idempotency_key` guarantees.
- **Broadcasts Realtime Updates (<50ms latency)** across distributed laptops and tools via a bidirectional WebSocket event hub.
- **Visualizes Live Execution** in a realtime interactive dashboard displaying task nodes, dependency edges, amber in-flight claim leases, and pulsing red conflict badges.

---

## 🏗️ Architecture

```
[ User 1: Claude Code CLI ]                [ User 2: Codex / Antigravity ]
            \                                            /
             \ (REST / MCP)                             / (REST / MCP)
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
                  Interactive DAG Visualization
                  - Real-time node state transitions
                  - Amber in-flight lease badges
                  - Red pulsing conflict alerts
                  - Full audit & lineage history
```

---

## 🚀 Key Features

1. **Tool-Agnostic Agent Adapters**:
   - `adapters/claude_adapter.py`: Seamless integration for Claude Code CLI.
   - `adapters/codex_adapter.py`: Seamless integration for OpenAI Codex / Cursor.
   - `adapters/agy_adapter.py`: First-class integration for Google Antigravity (AGY).
   - `adapters/cli.py`: Symmetrical CLI runner, profile persistence, and auto-claim engine.
   - Standard operations: `connect`, `start`, `done`, `claim`, `context`, `submit`, `seed`, and real-time conflict listeners.
2. **Deterministic Conflict Invalidation**:
   - When a shared requirement or upstream task changes (`REQ-01@v2`), OmniHarness calculates the exact downstream blast radius in `< 1ms` and invalidates affected in-flight tasks with conflict notifications.
3. **Scoped Subgraphs (~1.5k tokens)**:
   - Eliminates context-window blowout by feeding agents only their claimed task, upstream requirement, and immediate parent decisions rather than raw full-repository contexts.
4. **Crash-Resilient Event Sourcing**:
   - Backed by SQLite in WAL mode. Interrupted network operations or crashed processes resume cleanly with zero duplicate task executions.

---

## 📦 Installation & Setup

### Prerequisites
- Python 3.10+
- Git

### 1. Clone & Install Dependencies
```bash
git clone https://github.com/Bored008/MultiagentHarness.git
cd MultiagentHarness
python -m pip install -r requirements.txt
```

### 2. Run the Verification Test Suite
```bash
python -m pytest
```
*(Comprehensive unit and integration tests verifying SQLite store idempotency, NetworkX DAG blast-radius traversal, REST endpoints, auth token verifiers, and multi-agent adapter conflict lifecycles)*

### 3. Launch the OmniHarness Server & Live Dashboard
```bash
python -m uvicorn harness.server:app --host 0.0.0.0 --port 8000 --reload
```
Open your browser to:
- **Live Visual Dashboard**: `http://localhost:8000/dashboard`
- **Interactive OpenAPI Docs**: `http://localhost:8000/docs`

---

## ⚡ Zero-Ceremony Agent Workflow

OmniHarness provides a streamlined, zero-ceremony command workflow for coding agents (Claude Code CLI, OpenAI Codex / Cursor, Antigravity) running across separate laptops:

### 1. One-Time Laptop Setup
Connect your local environment to the OmniHarness instance (local, Cloudflare Tunnel, or Render):
```bash
python -m adapters.<claude|codex|agy>_adapter connect https://multiagentharness.onrender.com --agent-id my-agent-id
```
*(Saves connection parameters to `.omniharness.json` so you never have to re-type `--server` or `--agent-id`)*

### 2. Discover Ready Tasks
View the real-time shared DAG and highlight unblocked, ready-to-claim tasks:
```bash
python -m adapters.<claude|codex|agy>_adapter tasks
```

### 3. Claim & Pull Scoped Prompt
Claim a specific task, or omit the task ID to automatically claim the next ready task and retrieve its ~1.5k token scoped upstream prompt:
```bash
python -m adapters.<claude|codex|agy>_adapter start [TASK_ID]
```

### 4. Submit Completed Work
Submit task output directly from the command line or from an artifact file:
```bash
# Submit with an inline summary
python -m adapters.<claude|codex|agy>_adapter done <TASK_ID> "Implemented and verified component"

# Or submit with an artifact payload file
python -m adapters.<claude|codex|agy>_adapter done <TASK_ID> --file output.json
```

---

## 🔌 REST & WebSocket API Reference

OmniHarness exposes a clean, fast REST API paired with a real-time WebSocket hub:

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/dashboard` | `GET` | Interactive visual DAG dashboard with real-time WebSocket sync |
| `/llms.txt` | `GET` | Machine-readable agent discovery protocol and active DAG state |
| `/api/graph` | `GET` | Full in-memory knowledge graph representation (nodes & edges) |
| `/api/tasks/ready` | `GET` | List of unblocked, claimable task IDs whose dependencies are met |
| `/api/tasks/{id}/claim` | `POST` | Atomically claim lease on a task node with idempotency key |
| `/api/tasks/{id}/subgraph` | `GET` | Scoped (~1.5k token) upstream lineage and requirement prompt |
| `/api/tasks/{id}/submit` | `POST` | Submit completed task artifact with deterministic version bump |
| `/api/requirements/{id}/update` | `POST` | Update specification and trigger automated blast-radius invalidation |
| `/api/seed/{template}` | `POST` | Seed graph with pre-configured DAG templates (`tictactoe`, `auth`) |
| `/api/reset` | `POST` | Reset graph and event store for demo repeatability |
| `/ws/graph` | `WS` | Real-time bidirectional WebSocket event broadcaster (<50ms sync) |

---

## 🧪 Running the Demo Workflow

Execute the deterministic 6-gate evaluation workflow simulating Claude Code CLI and Codex agents collaborating, claiming tasks, handling live requirement shifts, and tracing audit lineage:

```bash
python workflows/omniharness_demo.py
```

---

## 🌐 Multi-Laptop Collaboration & Deployment

OmniHarness is designed from the ground up for multi-engineer, multi-laptop hackathon demonstrations:

### Option A: Hosted Cloud Instance (Render)
- Live deployment: **https://multiagentharness.onrender.com**
- Connect agents from any machine without local server setup:
  ```bash
  python -m adapters.agy_adapter connect https://multiagentharness.onrender.com --agent-id my-laptop-agent
  ```

### Option B: Local Network / Cloudflare Tunnel
1. **Start the Uvicorn Server**:
   ```bash
   # Windows launcher
   scripts\server.bat
   # Or Unix shell
   python -m uvicorn harness.server:app --host 0.0.0.0 --port 8000 --reload
   ```
2. **Expose via Cloudflare Tunnel** (allows remote teammates to connect with SSL and WebSocket support):
   ```bash
   scripts\tunnel.bat
   # Or manually
   cloudflared tunnel --url http://127.0.0.1:8000
   ```
3. **One-Click Seed Demonstration**:
   ```bash
   scripts\seed.bat
   ```

---

## 📂 Project Structure

```
MultiagentHarness/
├── adapters/
│   ├── base.py            # BaseAgentAdapter & ConflictListener
│   ├── cli.py             # Symmetrical CLI parser & profile resolution
│   ├── claude_adapter.py  # Claude Code CLI adapter implementation
│   ├── codex_adapter.py   # OpenAI Codex / Cursor adapter implementation
│   └── agy_adapter.py     # Google Antigravity (AGY) adapter implementation
├── auth/
│   ├── middleware.py      # FastAPI HTTPBearer token validator
│   ├── schemas.py         # Pydantic v2 JWT claim schemas
│   └── verifier.py        # RS256 token verification logic
├── harness/
│   ├── models.py          # Pydantic schemas (Node, TaskEvent, Subgraph)
│   ├── store.py           # SQLite WAL event sourcing & idempotency store
│   ├── graph.py           # NetworkX DAG engine & blast-radius invalidation
│   ├── server.py          # FastAPI REST & WebSocket server
│   ├── seeds.py           # Pre-configured DAG templates (tictactoe, auth)
│   └── dashboard.html     # Live interactive visual graph dashboard
├── tests/
│   ├── test_adapters.py   # Tests for agent adapters & conflict signals
│   ├── test_auth_middleware.py # Tests for RS256 token verifier
│   ├── test_graph.py      # Tests for DAG cycles & blast-radius invalidation
│   ├── test_server.py     # Tests for REST endpoints & WebSockets
│   └── test_store.py      # Tests for SQLite event persistence & idempotency
├── workflows/
│   └── omniharness_demo.py # 6-gate end-to-end evaluation demo
├── notes/
│   ├── active-context.md  # Architectural context & project memory
│   └── daily/             # Daily engineering logs & decisions
├── AGENTS.md              # Zero-ceremony coding agent protocol
├── requirements.txt       # Python dependencies
└── README.md              # Project documentation
```

---

## 🎮 Built-In Demonstration: NeonGrid Tic Tac Toe

OmniHarness includes a full end-to-end multi-agent demonstration showcasing collaborative task execution:
- **`TSK-01`**: Deterministic 3x3 game state engine with 8-way win evaluation and draw locking.
- **`TSK-02`**: Unbeatable Minimax AI solver with alpha-beta pruning and depth-weighted scoring (< 5ms response time).
- **`TSK-03`**: Cyberpunk glassmorphic UI, LocalStorage scoreboard, and procedural Web Audio API sound synthesis.
- **`TSK-04`**: Dynamic SVG vector strike-through animation, canvas confetti celebration engine, and rematch modal.
- **`TSK-05`**: OmniHarness WebSocket live two-player synchronization bridge.

---

## 👥 Team & Project Contributors

Built for **NexHack 2.0 (Problem Statement 01: Multi-Agent Harness for Shared Team Knowledge Base)**:

- **Himanshu Dahiya** ([@Bored008](https://github.com/Bored008)) — In-Memory DAG architecture (NetworkX), SQLite WAL event store, real-time WebSocket hub, and React Flow visual dashboard.
- **Mohit Jangra** ([@mohitjangra997](https://github.com/mohitjangra997)) — Google Antigravity adapter, zero-ceremony CLI workflow, multi-agent game state machine, and comprehensive test suite validation.
- **Rahul Bhukal** ([@bhukalrahul9745](mailto:bhukalrahul9745@gmail.com)) — Real-time conflict resolution protocols, multi-laptop LAN/tunnel orchestration, environment verification, and benchmark validation.

---

## 📜 License
MIT License.
