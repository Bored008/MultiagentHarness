"""
Seed Tic Tac Toe Multi-Agent Plan into OmniHarness.
Connects to OmniHarness REST API, resets graph, and posts full plan DAG
(REQ-01, DEC-01, TSK-01, TSK-02, TSK-03, TSK-04, TSK-05, ART-01).
"""
from __future__ import annotations

import argparse
import sys
import httpx


def seed_tictactoe(server_url: str = "https://jewel-untitled-respective-devel.trycloudflare.com") -> None:
    server_url = server_url.rstrip("/")
    print(f"Connecting to OmniHarness on {server_url}...")

    client = httpx.Client(base_url=server_url, timeout=15.0)

    # 1. Reset board to clean state
    try:
        r = client.post("/api/reset")
        if r.status_code == 200:
            print("  [x] Reset graph to clean state.")
        else:
            print(f"  [!] Reset returned status {r.status_code}: {r.text}")
    except Exception as e:
        print(f"  [!] Reset note: {e}")

    # 2. Define Tic Tac Toe Plan Nodes
    nodes = [
        {
            "id": "REQ-01",
            "title": "Tic Tac Toe Core Game Specification & Feature Matrix",
            "type": "REQUIREMENT",
            "actor": "product-architect",
            "idempotency_key": "seed-ttt-req-01",
            "dependencies": [],
            "payload": {
                "game_title": "NeonGrid Tic Tac Toe",
                "board_geometry": "3x3 Responsive Grid (9 cells, coordinates [0,0] to [2,2], index 0..8)",
                "game_modes": [
                    "PvP (Local Pass & Play): Two human players alternating turns on one screen",
                    "PvAI (Unbeatable Minimax): Single player vs zero-mistake optimal AI engine",
                    "PvAI (Casual Mode): Single player vs AI with 30% error injection for casual play",
                    "PvP (OmniHarness Remote Sync): Two players on separate laptops synchronized via WebSocket hub"
                ],
                "win_conditions": [
                    "3 Horizontal rows: [0,1,2], [3,4,5], [6,7,8]",
                    "3 Vertical columns: [0,3,6], [1,4,7], [2,5,8]",
                    "2 Diagonals: [0,4,8], [2,4,6]",
                    "Draw: All 9 cells populated with no winning line"
                ],
                "player_symbols": {
                    "X": {"label": "Player 1", "color": "Neon Cyan (#06B6D4)", "glow": "cyan-glow"},
                    "O": {"label": "Player 2 / AI", "color": "Neon Magenta (#D946EF)", "glow": "magenta-glow"}
                },
                "telemetry_and_scoring": [
                    "Real-time score counters: X Wins, O Wins, Draws, Win Streaks",
                    "Move history log with turn timestamps",
                    "Sound FX: move click, victory chime, draw buzz, invalid cell vibration",
                    "Canvas confetti celebration particle animation on victory"
                ]
            }
        },
        {
            "id": "DEC-01",
            "title": "Tic Tac Toe Architecture, State Machine & UI Design System",
            "type": "DECISION",
            "actor": "lead-architect",
            "idempotency_key": "seed-ttt-dec-01",
            "dependencies": ["REQ-01"],
            "payload": {
                "state_representation": {
                    "board": "Array(9).fill(null) with values X, O, or null",
                    "turn": "X or O (X starts by default)",
                    "game_status": "IN_PROGRESS | WON_X | WON_O | DRAW",
                    "winning_line": "[number, number, number] | null"
                },
                "engine_algorithms": {
                    "win_detection": "8-combination bitmask / constant-time array lookup",
                    "minimax_ai": "Zero-sum recursive game tree with depth weighting (+10 - depth for AI win, depth - 10 for human win) and alpha-beta pruning",
                    "audio_engine": "Procedural Web Audio API oscillator synthesis (zero external mp3 assets)"
                },
                "ui_design_tokens": {
                    "background": "Obsidian radial gradient (#030712 to #0B0F19)",
                    "cell_cards": "Glassmorphic backdrop blur (bg-slate-900/80 border-slate-800 hover:border-slate-700)",
                    "x_token": "Neon Cyan SVG cross with drop-shadow shadow-cyan-500/50",
                    "o_token": "Neon Magenta SVG ring with drop-shadow shadow-fuchsia-500/50",
                    "strike_line": "Dynamic animated SVG stroke over winning cell centers"
                },
                "multi_agent_work_allocation": {
                    "claude-code-cli": "Game engine, win/draw verification, and Minimax AI algorithm",
                    "openai-codex": "UI dashboard, glassmorphism CSS, Web Audio FX, confetti system",
                    "antigravity-agent": "OmniHarness WebSocket multi-laptop sync and session persistence"
                }
            }
        },
        {
            "id": "TSK-01",
            "title": "Core Board State Engine & 8-Way Win Detection",
            "type": "TASK",
            "actor": "claude-code-cli",
            "idempotency_key": "seed-ttt-tsk-01",
            "dependencies": ["DEC-01"],
            "payload": {
                "instructions": "Implement deterministic game state machine: 9-cell array, turn alternating, legal move validation, 8-line win detection, draw detection, and reset lifecycle.",
                "assigned_agent": "Claude Code CLI (Laptop 1)",
                "deliverable": "Game state machine class with validateMove(), makeMove(), checkWinner(), and reset()",
                "acceptance_criteria": [
                    "Rejects moves on already occupied cells or outside 0..8 bounds",
                    "Alternates turn between X and O after valid move",
                    "Identifies all 8 winning lines and returns winning cell indices",
                    "Detects draw state when 9 moves made without a winner",
                    "Locks board state when terminal state is reached"
                ]
            }
        },
        {
            "id": "TSK-02",
            "title": "Unbeatable Minimax AI Engine with Alpha-Beta Pruning",
            "type": "TASK",
            "actor": "claude-code-cli",
            "idempotency_key": "seed-ttt-tsk-02",
            "dependencies": ["TSK-01"],
            "payload": {
                "instructions": "Implement recursive Minimax engine for AI turns with depth weighting (+10 - depth for AI win, depth - 10 for human win) and difficulty toggle (Unbeatable vs Casual).",
                "assigned_agent": "Claude Code CLI (Laptop 1)",
                "deliverable": "Minimax AI solver module with getBestMove(board, player, difficulty)",
                "acceptance_criteria": [
                    "Unbeatable AI mode mathematically guarantees win or draw across all starting moves",
                    "Depth-weighted scoring prioritizes fastest victory paths and longest resistance paths",
                    "Casual mode introduces controlled 25-30% error injection for human victory possibilities",
                    "Compute time executes under 15ms per move"
                ]
            }
        },
        {
            "id": "TSK-03",
            "title": "Cyberpunk Glassmorphic UI & Procedural Web Audio FX",
            "type": "TASK",
            "actor": "openai-codex",
            "idempotency_key": "seed-ttt-tsk-03",
            "dependencies": ["DEC-01"],
            "payload": {
                "instructions": "Build responsive 3x3 interactive gameboard using Tailwind CSS glassmorphism, animated turn indicators, scoreboard, and procedural Web Audio API sound effects.",
                "assigned_agent": "OpenAI Codex / Cursor (Laptop 2)",
                "deliverable": "Single-page responsive UI with audio synthesis engine",
                "acceptance_criteria": [
                    "Fluid responsive 3x3 layout with glowing tokens for X and O",
                    "Real-time scoreboard tracking X Wins, O Wins, Draws, and Streaks in LocalStorage",
                    "Web Audio API sound synthesis for clicks, invalid moves, and terminal states",
                    "Accessible keyboard navigation (numpad 1-9 mapping)"
                ]
            }
        },
        {
            "id": "TSK-04",
            "title": "Dynamic Winning Strike-Through Line & Confetti Burst",
            "type": "TASK",
            "actor": "openai-codex",
            "idempotency_key": "seed-ttt-tsk-04",
            "dependencies": ["TSK-01", "TSK-03"],
            "payload": {
                "instructions": "Implement SVG vector stroke animation connecting the centers of the 3 winning cells, plus canvas particle confetti celebration on player win.",
                "assigned_agent": "OpenAI Codex / Cursor (Laptop 2)",
                "deliverable": "SVG line renderer and Canvas confetti celebration engine",
                "acceptance_criteria": [
                    "Draws animated glowing neon vector line across winning cells (horizontal, vertical, or diagonal)",
                    "Triggers confetti particle explosion on player win",
                    "Displays modal overlay with round statistics and instant rematch button"
                ]
            }
        },
        {
            "id": "TSK-05",
            "title": "OmniHarness Multi-Laptop Two-Player Sync Adapter",
            "type": "TASK",
            "actor": "antigravity-agent",
            "idempotency_key": "seed-ttt-tsk-05",
            "dependencies": ["TSK-01"],
            "payload": {
                "instructions": "Wire game state to OmniHarness WebSocket channel (/ws/graph) allowing two developers on different laptops across the Cloudflare tunnel to take turns in real-time.",
                "assigned_agent": "Antigravity Agent (Orchestrator)",
                "deliverable": "WebSocket live multi-laptop synchronization bridge",
                "acceptance_criteria": [
                    "Transmits move payloads over OmniHarness WebSocket hub in <50ms",
                    "Renders opponent moves in real time without page refresh",
                    "Maintains synchronized board state and score tally across both laptop browsers"
                ]
            }
        },
        {
            "id": "ART-01",
            "title": "Tic Tac Toe Technical Specification & Implementation Blueprint",
            "type": "ARTIFACT",
            "actor": "product-architect",
            "idempotency_key": "seed-ttt-art-01",
            "dependencies": ["REQ-01", "DEC-01"],
            "payload": {
                "artifact_name": "Tic Tac Toe Multi-Agent Engineering Blueprint",
                "version": "1.0.0",
                "summary": "Full architectural blueprint and execution plan for Tic Tac Toe with Unbeatable Minimax AI, Glassmorphism UI, Procedural Audio, and OmniHarness Two-Laptop Sync.",
                "architecture_sections": [
                    "1. Requirement Specification & Feature Matrix",
                    "2. Architecture & State Machine Design",
                    "3. Minimax AI Game Tree with Alpha-Beta Pruning",
                    "4. Glassmorphism UI & Web Audio FX Implementation",
                    "5. Multi-Laptop WebSocket Synchronization Protocol"
                ]
            }
        }
    ]

    for n in nodes:
        res = client.post("/api/nodes", json=n)
        if res.status_code in (200, 201):
            print(f"  ✔ Created [{n['type']}] {n['id']}: {n['title']}")
        else:
            print(f"  ✖ Failed {n['id']}: {res.status_code} {res.text}")

    # 3. Verify Graph State
    r_graph = client.get("/api/graph")
    if r_graph.status_code == 200:
        graph_data = r_graph.json()
        stats = graph_data.get("stats", {})
        print(f"\nLive Graph Seeding Complete!")
        print(f"  Total Nodes: {stats.get('total_nodes', len(graph_data.get('nodes', [])))}")
        print(f"  Total Edges: {stats.get('total_edges', len(graph_data.get('edges', [])))}")
        print(f"  Pending Tasks: {stats.get('pending', 0)}")
    print(f"\nLive Dashboard: {server_url}/dashboard")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Tic Tac Toe Multi-Agent Plan into OmniHarness")
    parser.add_argument("--server", default="https://jewel-untitled-respective-devel.trycloudflare.com", help="OmniHarness server URL")
    args = parser.parse_args()
    seed_tictactoe(args.server)
