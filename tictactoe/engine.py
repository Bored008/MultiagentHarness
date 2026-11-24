"""
NeonGrid Tic Tac Toe - Core Game Engine & State Machine
Problem Statement 01: Multi-Agent Shared Knowledge Base
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class GameStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    WON_X = "WON_X"
    WON_O = "WON_O"
    DRAW = "DRAW"


class Player(str, Enum):
    X = "X"
    O = "O"


# 8 winning lines: 3 rows, 3 columns, 2 diagonals
WINNING_LINES: List[Tuple[int, int, int]] = [
    (0, 1, 2),  # Row 1
    (3, 4, 5),  # Row 2
    (6, 7, 8),  # Row 3
    (0, 3, 6),  # Column 1
    (1, 4, 7),  # Column 2
    (2, 5, 8),  # Column 3
    (0, 4, 8),  # Diagonal \
    (2, 4, 6),  # Diagonal /
]


class TicTacToeEngine:
    """
    Deterministic 3x3 Tic Tac Toe game state machine.
    Adheres strictly to REQ-01 and DEC-01.
    """

    def __init__(self) -> None:
        self.board: List[Optional[str]] = [None] * 9
        self.turn: str = Player.X.value
        self.game_status: GameStatus = GameStatus.IN_PROGRESS
        self.winning_line: Optional[List[int]] = None
        self.move_history: List[Dict[str, Any]] = []

    def validate_move(self, index: int) -> Tuple[bool, Optional[str]]:
        """
        Validate whether move at `index` is permitted.
        Returns (is_valid, error_reason).
        """
        if self.game_status != GameStatus.IN_PROGRESS:
            return False, f"Game is already over with status: {self.game_status.value}."

        if not isinstance(index, int) or index < 0 or index > 8:
            return False, f"Invalid cell index '{index}'. Must be an integer between 0 and 8."

        if self.board[index] is not None:
            return False, f"Cell {index} is already occupied by '{self.board[index]}'."

        return True, None

    def make_move(self, index: int, player: Optional[str] = None) -> bool:
        """
        Execute move at `index`. If `player` is not specified, uses `self.turn`.
        Returns True if move succeeded, False otherwise.
        """
        is_valid, _ = self.validate_move(index)
        if not is_valid:
            return False

        current_player = player if player in (Player.X.value, Player.O.value) else self.turn
        self.board[index] = current_player

        # Record move telemetry
        self.move_history.append({
            "cell": index,
            "player": current_player,
            "timestamp": time.time(),
            "move_number": len(self.move_history) + 1,
        })

        # Check winner or draw
        status, win_line = self.check_winner()
        self.game_status = status
        self.winning_line = win_line

        # Alternate turn if still in progress
        if self.game_status == GameStatus.IN_PROGRESS:
            self.turn = Player.O.value if current_player == Player.X.value else Player.X.value

        return True

    def check_winner(self) -> Tuple[GameStatus, Optional[List[int]]]:
        """
        Inspect 8 winning combinations in constant time.
        Returns (GameStatus, Optional[List[int]]).
        """
        b = self.board
        for line in WINNING_LINES:
            i, j, k = line
            if b[i] is not None and b[i] == b[j] == b[k]:
                winner_status = GameStatus.WON_X if b[i] == Player.X.value else GameStatus.WON_O
                return winner_status, list(line)

        # Check draw: all 9 cells populated
        if all(cell is not None for cell in b):
            return GameStatus.DRAW, None

        return GameStatus.IN_PROGRESS, None

    def reset(self) -> None:
        """Reset game state to clean initial configuration."""
        self.board = [None] * 9
        self.turn = Player.X.value
        self.game_status = GameStatus.IN_PROGRESS
        self.winning_line = None
        self.move_history = []

    def get_available_moves(self) -> List[int]:
        """Return list of unoccupied cell indices."""
        return [i for i, cell in enumerate(self.board) if cell is None]

    def to_dict(self) -> Dict[str, Any]:
        """Export state representation adhering to DEC-01."""
        return {
            "board": list(self.board),
            "turn": self.turn,
            "game_status": self.game_status.value,
            "winning_line": self.winning_line,
            "moves_count": len(self.move_history),
            "is_terminal": self.game_status != GameStatus.IN_PROGRESS,
            "available_moves": self.get_available_moves(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TicTacToeEngine:
        """Reconstruct engine from dictionary."""
        engine = cls()
        engine.board = list(data.get("board", [None] * 9))
        engine.turn = data.get("turn", Player.X.value)
        engine.game_status = GameStatus(data.get("game_status", GameStatus.IN_PROGRESS.value))
        engine.winning_line = data.get("winning_line")
        return engine
