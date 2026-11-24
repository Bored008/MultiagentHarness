import os
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "tictactoe" / "web"


def test_web_files_exist():
    expected_files = ["index.html", "app.js", "audio.js", "confetti.js", "strike.js"]
    for fname in expected_files:
        fpath = WEB_DIR / fname
        assert fpath.is_file(), f"Expected {fname} to exist in {WEB_DIR}"
        assert fpath.stat().st_size > 0, f"Expected {fname} to not be empty"


def test_html_contains_required_elements():
    html_content = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    # TSK-03 elements:
    assert 'id="game-board"' in html_content
    assert 'id="turn-indicator"' in html_content
    assert 'id="score-x"' in html_content
    assert 'id="score-o"' in html_content
    assert 'id="score-draw"' in html_content
    assert 'id="streak-count"' in html_content
    assert 'id="btn-mute"' in html_content
    assert 'id="btn-reset"' in html_content

    # TSK-04 elements:
    assert 'id="strike-svg"' in html_content
    assert 'id="confetti-canvas"' in html_content
    assert 'id="result-modal"' in html_content
    assert 'id="btn-rematch"' in html_content


def test_audio_engine_synthesizers():
    audio_content = (WEB_DIR / "audio.js").read_text(encoding="utf-8")
    assert "class SoundEngine" in audio_content
    assert "playMove" in audio_content
    assert "playInvalid" in audio_content
    assert "playVictory" in audio_content
    assert "playDraw" in audio_content
    assert "playReset" in audio_content
    assert "AudioContext" in audio_content


def test_strike_renderer_methods():
    strike_content = (WEB_DIR / "strike.js").read_text(encoding="utf-8")
    assert "class StrikeRenderer" in strike_content
    assert "drawWinningLine" in strike_content
    assert "stroke-dashoffset" in strike_content
    assert "clear" in strike_content


def test_confetti_engine_methods():
    confetti_content = (WEB_DIR / "confetti.js").read_text(encoding="utf-8")
    assert "class ConfettiEngine" in confetti_content
    assert "burst" in confetti_content
    assert "clear" in confetti_content
    assert "_animate" in confetti_content
