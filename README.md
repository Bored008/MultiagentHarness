# MultiagentHarness

> **OmniHarness**: Tool-Agnostic Multi-Agent Collaboration Harness for Shared Team Knowledge Base  
> Built for **NexHack 2.0 — Problem Statement 01 (Generative AI Track)**

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
   - `adapters/codex_adapter.py`: Seamless integration for OpenAI Codex / Cursor / Antigravity.
   - Standard operations: `claim_task`, `get_task_subgraph`, `submit_result`, and realtime conflict listeners.
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
*(37 unit and integration tests verifying store idempotency, graph blast-radius traversal, REST endpoints, and adapter conflict lifecycles)*

### 3. Launch the OmniHarness Server & Live Dashboard
```bash
python -m uvicorn harness.server:app --host 0.0.0.0 --port 8000 --reload
```
Open your browser to:
- **Live Visual Dashboard**: `http://localhost:8000/dashboard`
- **Interactive OpenAPI Docs**: `http://localhost:8000/docs`

---

## 🧪 Running the Demo Workflow

Execute the deterministic 6-gate evaluation workflow simulating Claude Code CLI and Codex agents collaborating, claiming tasks, handling live requirement shifts, and tracing audit lineage:

```bash
python workflows/omniharness_demo.py
```

---

## 📂 Project Structure

```
MultiagentHarness/
├── adapters/
│   ├── base.py            # BaseAgentAdapter & ConflictListener
│   ├── claude_adapter.py  # Claude Code CLI adapter implementation
│   └── codex_adapter.py   # OpenAI Codex / Cursor adapter implementation
├── harness/
│   ├── models.py          # Pydantic schemas (Node, TaskEvent, Subgraph)
│   ├── store.py           # SQLite WAL event sourcing & idempotency store
│   ├── graph.py           # NetworkX DAG engine & blast-radius invalidation
│   ├── server.py          # FastAPI REST & WebSocket server
│   └── dashboard.html     # Live interactive visual graph dashboard
├── tests/
│   ├── test_adapters.py   # Tests for agent adapters & conflict signals
│   ├── test_graph.py      # Tests for DAG cycles & blast-radius invalidation
│   ├── test_server.py     # Tests for REST endpoints & WebSockets
│   └── test_store.py      # Tests for SQLite event persistence & idempotency
├── workflows/
│   └── omniharness_demo.py # 6-gate end-to-end evaluation demo
├── notes/
│   └── active-context.md  # Architectural context & project memory
├── AGENTS.md              # Project agent guidelines & standards
├── requirements.txt       # Python dependencies
└── README.md              # Project documentation
```

---

## 📜 License
MIT License.
