import random
import time
import pytest
from tictactoe.engine import TicTacToeEngine, GameStatus, Player
from tictactoe.minimax import MinimaxAI, Difficulty


def test_immediate_win_taken():
    ai = MinimaxAI(ai_player="O", human_player="X")
    # Board: O at 0, 1; O should play 2 to win
    board = [
        "O", "O", None,
        "X", "X", None,
        None, None, None
    ]
    move = ai.get_best_move(board, player="O", difficulty=Difficulty.UNBEATABLE)
    assert move == 2


def test_immediate_block_taken():
    ai = MinimaxAI(ai_player="O", human_player="X")
    # Board: X at 0, 1; O must block at 2
    board = [
        "X", "X", None,
        "O", None, None,
        None, None, None
    ]
    move = ai.get_best_move(board, player="O", difficulty=Difficulty.UNBEATABLE)
    assert move == 2


def test_depth_weighting_fastest_win():
    ai = MinimaxAI(ai_player="O", human_player="X")
    # O can win immediately at 2, or take other moves leading to longer games
    board = [
        "O", "O", None,
        "X", None, None,
        "X", None, None
    ]
    move = ai.get_best_move(board, player="O", difficulty=Difficulty.UNBEATABLE)
    assert move == 2


def test_execution_time_under_15ms():
    ai = MinimaxAI(ai_player="O", human_player="X")
    # Empty board or opening
    board = ["X", None, None, None, None, None, None, None, None]
    t0 = time.perf_counter()
    move = ai.get_best_move(board, player="O", difficulty=Difficulty.UNBEATABLE)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert 0 <= move <= 8
    assert elapsed_ms < 15.0, f"Move took {elapsed_ms:.2f}ms which exceeds 15ms threshold"


def test_unbeatable_vs_random_player():
    """AI (as Player O) should never lose across 30 games against random opponent."""
    ai = MinimaxAI(ai_player="O", human_player="X")

    for game_i in range(30):
        engine = TicTacToeEngine()
        while engine.game_status == GameStatus.IN_PROGRESS:
            if engine.turn == "X":
                # Random human move
                avail = engine.get_available_moves()
                engine.make_move(random.choice(avail))
            else:
                # Unbeatable AI move
                move = ai.get_best_move(engine.board, player="O", difficulty=Difficulty.UNBEATABLE)
                engine.make_move(move)

        # AI must NEVER lose!
        assert engine.game_status in (GameStatus.WON_O, GameStatus.DRAW), f"Game {game_i} lost by AI!"


def test_unbeatable_ai_first():
    """AI (as Player X) should never lose when going first across 30 games."""
    ai = MinimaxAI(ai_player="X", human_player="O")

    for game_i in range(30):
        engine = TicTacToeEngine()
        while engine.game_status == GameStatus.IN_PROGRESS:
            if engine.turn == "X":
                move = ai.get_best_move(engine.board, player="X", difficulty=Difficulty.UNBEATABLE)
                engine.make_move(move)
            else:
                avail = engine.get_available_moves()
                engine.make_move(random.choice(avail))

        assert engine.game_status in (GameStatus.WON_X, GameStatus.DRAW), f"Game {game_i} lost by AI!"


def test_casual_mode_injection():
    ai = MinimaxAI(ai_player="O", human_player="X", casual_error_rate=1.0)
    # With 100% error rate in casual mode, it picks from available moves
    board = ["X", "X", None, None, None, None, None, None, None]
    moves = {ai.get_best_move(board, player="O", difficulty=Difficulty.CASUAL) for _ in range(20)}
    # Should see random choices beyond just the optimal block
    assert len(moves) > 1
