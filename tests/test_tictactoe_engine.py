import pytest
from tictactoe.engine import TicTacToeEngine, GameStatus, Player, WINNING_LINES


def test_initial_state():
    engine = TicTacToeEngine()
    state = engine.to_dict()
    assert state["board"] == [None] * 9
    assert state["turn"] == "X"
    assert state["game_status"] == "IN_PROGRESS"
    assert state["winning_line"] is None
    assert len(state["available_moves"]) == 9


def test_valid_moves_and_turn_alternation():
    engine = TicTacToeEngine()
    assert engine.make_move(0) is True
    assert engine.board[0] == "X"
    assert engine.turn == "O"

    assert engine.make_move(4) is True
    assert engine.board[4] == "O"
    assert engine.turn == "X"


def test_reject_occupied_cell():
    engine = TicTacToeEngine()
    engine.make_move(0)
    is_valid, err = engine.validate_move(0)
    assert is_valid is False
    assert "already occupied" in err
    assert engine.make_move(0) is False


def test_reject_out_of_bounds():
    engine = TicTacToeEngine()
    for bad_idx in [-1, 9, 100]:
        is_valid, err = engine.validate_move(bad_idx)
        assert is_valid is False
        assert "Invalid cell index" in err
        assert engine.make_move(bad_idx) is False


def test_all_8_winning_lines():
    for line in WINNING_LINES:
        engine = TicTacToeEngine()
        a, b, c = line
        # Play moves so X takes a, b, c
        other_cells = [i for i in range(9) if i not in line]
        # X: a, O: other[0], X: b, O: other[1], X: c
        engine.make_move(a)
        engine.make_move(other_cells[0])
        engine.make_move(b)
        engine.make_move(other_cells[1])
        engine.make_move(c)

        assert engine.game_status == GameStatus.WON_X
        assert engine.winning_line == list(line)
        # Verify terminal lock: further moves rejected
        assert engine.make_move(other_cells[2]) is False


def test_draw_detection():
    # Board layout for draw:
    # X O X
    # X O O
    # O X X
    # [0]=X, [1]=O, [2]=X, [3]=X, [4]=O, [5]=O, [6]=O, [7]=X, [8]=X
    moves = [
        (0, "X"), (1, "O"), (2, "X"),
        (4, "O"), (3, "X"), (5, "O"),
        (7, "X"), (6, "O"), (8, "X"),
    ]
    engine = TicTacToeEngine()
    for idx, p in moves:
        assert engine.make_move(idx, player=p) is True

    assert engine.game_status == GameStatus.DRAW
    assert engine.winning_line is None
    assert engine.to_dict()["is_terminal"] is True


def test_reset_lifecycle():
    engine = TicTacToeEngine()
    engine.make_move(0)
    engine.make_move(1)
    engine.reset()
    assert engine.board == [None] * 9
    assert engine.turn == "X"
    assert engine.game_status == GameStatus.IN_PROGRESS
    assert engine.move_history == []
