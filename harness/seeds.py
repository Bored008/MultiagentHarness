"""
Reusable DAG seed templates for SYNAPSE.
Single source of truth shared by server /api/seed endpoints and CLI seed scripts.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List


SEED_TEMPLATES: Dict[str, List[Dict[str, Any]]] = {
    "tictactoe": [
        {
            "id": "REQ-01",
            "title": "Tic Tac Toe Core Game Specification & Feature Matrix",
            "type": "REQUIREMENT",
            "status": "COMPLETED",
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
                    "PvP (SYNAPSE Remote Sync): Two players on separate laptops synchronized via WebSocket hub",
                ],
                "win_conditions": [
                    "3 Horizontal rows: [0,1,2], [3,4,5], [6,7,8]",
                    "3 Vertical columns: [0,3,6], [1,4,7], [2,5,8]",
                    "2 Diagonals: [0,4,8], [2,4,6]",
                    "Draw: All 9 cells populated with no winning line",
                ],
                "player_symbols": {
                    "X": {"label": "Player 1", "color": "Neon Cyan (#06B6D4)", "glow": "cyan-glow"},
                    "O": {"label": "Player 2 / AI", "color": "Neon Magenta (#D946EF)", "glow": "magenta-glow"},
                },
                "telemetry_and_scoring": [
                    "Real-time score counters: X Wins, O Wins, Draws, Win Streaks",
                    "Move history log with turn timestamps",
                    "Sound FX: move click, victory chime, draw buzz, invalid cell vibration",
                    "Canvas confetti celebration particle animation on victory",
                ],
            },
        },
        {
            "id": "DEC-01",
            "title": "Tic Tac Toe Architecture, State Machine & UI Design System",
            "type": "DECISION",
            "status": "COMPLETED",
            "actor": "lead-architect",
            "idempotency_key": "seed-ttt-dec-01",
            "dependencies": ["REQ-01"],
            "payload": {
                "state_representation": {
                    "board": "Array(9).fill(null) with values X, O, or null",
                    "turn": "X or O (X starts by default)",
                    "game_status": "IN_PROGRESS | WON_X | WON_O | DRAW",
                    "winning_line": "[number, number, number] | null",
                },
                "engine_algorithms": {
                    "win_detection": "8-combination bitmask / constant-time array lookup",
                    "minimax_ai": "Zero-sum recursive game tree with depth weighting (+10 - depth for AI win, depth - 10 for human win) and alpha-beta pruning",
                    "audio_engine": "Procedural Web Audio API oscillator synthesis (zero external mp3 assets)",
                },
                "ui_design_tokens": {
                    "background": "Obsidian radial gradient (#030712 to #0B0F19)",
                    "cell_cards": "Glassmorphic backdrop blur (bg-slate-900/80 border-slate-800 hover:border-slate-700)",
                    "x_token": "Neon Cyan SVG cross with drop-shadow shadow-cyan-500/50",
                    "o_token": "Neon Magenta SVG ring with drop-shadow shadow-fuchsia-500/50",
                    "strike_line": "Dynamic animated SVG stroke over winning cell centers",
                },
                "multi_agent_work_allocation": {
                    "claude-code-cli": "Game engine, win/draw verification, and Minimax AI algorithm",
                    "openai-codex": "UI dashboard, glassmorphism CSS, Web Audio FX, confetti system",
                    "antigravity-agent": "SYNAPSE WebSocket multi-laptop sync and session persistence",
                },
            },
        },
        {
            "id": "TSK-01",
            "title": "Core Board State Engine & 8-Way Win Detection",
            "type": "TASK",
            "status": "PENDING",
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
                    "Locks board state when terminal state is reached",
                ],
            },
        },
        {
            "id": "TSK-02",
            "title": "Unbeatable Minimax AI Engine with Alpha-Beta Pruning",
            "type": "TASK",
            "status": "PENDING",
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
                    "Compute time executes under 15ms per move",
                ],
            },
        },
        {
            "id": "TSK-03",
            "title": "Cyberpunk Glassmorphic UI & Procedural Web Audio FX",
            "type": "TASK",
            "status": "PENDING",
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
                    "Accessible keyboard navigation (numpad 1-9 mapping)",
                ],
            },
        },
        {
            "id": "TSK-04",
            "title": "Dynamic Winning Strike-Through Line & Confetti Burst",
            "type": "TASK",
            "status": "PENDING",
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
                    "Displays modal overlay with round statistics and instant rematch button",
                ],
            },
        },
        {
            "id": "TSK-05",
            "title": "SYNAPSE Multi-Laptop Two-Player Sync Adapter",
            "type": "TASK",
            "status": "PENDING",
            "actor": "antigravity-agent",
            "idempotency_key": "seed-ttt-tsk-05",
            "dependencies": ["TSK-01"],
            "payload": {
                "instructions": "Wire game state to SYNAPSE WebSocket channel (/ws/graph) allowing two developers on different laptops across the Cloudflare tunnel to take turns in real-time.",
                "assigned_agent": "Antigravity Agent (Orchestrator)",
                "deliverable": "WebSocket live multi-laptop synchronization bridge",
                "acceptance_criteria": [
                    "Transmits move payloads over SYNAPSE WebSocket hub in <50ms",
                    "Renders opponent moves in real time without page refresh",
                    "Maintains synchronized board state and score tally across both laptop browsers",
                ],
            },
        },
        {
            "id": "ART-01",
            "title": "Tic Tac Toe Technical Specification & Implementation Blueprint",
            "type": "ARTIFACT",
            "status": "COMPLETED",
            "actor": "product-architect",
            "idempotency_key": "seed-ttt-art-01",
            "dependencies": ["REQ-01", "DEC-01"],
            "payload": {
                "artifact_name": "Tic Tac Toe Multi-Agent Engineering Blueprint",
                "version": "1.0.0",
                "summary": "Full architectural blueprint and execution plan for Tic Tac Toe with Unbeatable Minimax AI, Glassmorphism UI, Procedural Audio, and SYNAPSE Two-Laptop Sync.",
                "architecture_sections": [
                    "1. Requirement Specification & Feature Matrix",
                    "2. Architecture & State Machine Design",
                    "3. Minimax AI Game Tree with Alpha-Beta Pruning",
                    "4. Glassmorphism UI & Web Audio FX Implementation",
                    "5. Multi-Laptop WebSocket Synchronization Protocol",
                ],
            },
        },
    ],
    "auth": [
        {
            "id": "REQ-01",
            "title": "Auth Token Specification",
            "type": "REQUIREMENT",
            "status": "COMPLETED",
            "payload": {"spec": "JWT RS256 with 15min expiry", "algorithm": "RS256"},
            "dependencies": [],
            "actor": "product-architect",
            "idempotency_key": "seed-req-01",
        },
        {
            "id": "DEC-01",
            "title": "JWT Token Architecture Decision",
            "type": "DECISION",
            "status": "COMPLETED",
            "payload": {"decision": "Stateless asymmetric JWT architecture", "crypto": "RS256"},
            "dependencies": ["REQ-01"],
            "actor": "security-lead",
            "idempotency_key": "seed-dec-01",
        },
        {
            "id": "TSK-01",
            "title": "Auth Token Schema & Types",
            "type": "TASK",
            "status": "PENDING",
            "payload": {"instructions": "Define Pydantic token validation schemas"},
            "dependencies": ["DEC-01"],
            "actor": "claude-code-cli",
            "idempotency_key": "seed-tsk-01",
        },
        {
            "id": "TSK-02",
            "title": "Token Verifier Middleware",
            "type": "TASK",
            "status": "PENDING",
            "payload": {"instructions": "Implement FastAPI Bearer token validator"},
            "dependencies": ["TSK-01"],
            "actor": "openai-codex",
            "idempotency_key": "seed-tsk-02",
        },
        {
            "id": "TSK-03",
            "title": "FastAPI Token Verification Route",
            "type": "TASK",
            "status": "PENDING",
            "payload": {"instructions": "Expose /auth/verify verification endpoint"},
            "dependencies": ["DEC-01"],
            "actor": "claude-code-cli",
            "idempotency_key": "seed-tsk-03",
        },
    ],
}


def list_seed_templates() -> List[str]:
    """Return sorted list of available built-in seed template names."""
    return sorted(SEED_TEMPLATES.keys())


def get_seed_nodes(template: str) -> List[Dict[str, Any]]:
    """Return deep copy of node definitions for the given seed template."""
    key = template.strip().lower()
    if key not in SEED_TEMPLATES:
        available = ", ".join(list_seed_templates())
        raise KeyError(f"Unknown seed template '{template}'. Available: {available}")
    return deepcopy(SEED_TEMPLATES[key])
