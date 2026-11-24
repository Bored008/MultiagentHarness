/**
 * NeonGrid Tic Tac Toe - Web Application Controller (TSK-03 & TSK-04).
 * Full glassmorphic UI, audio synthesis, Minimax AI, strike-line & confetti celebrations.
 */

const WINNING_LINES = [
  [0, 1, 2], [3, 4, 5], [6, 7, 8],
  [0, 3, 6], [1, 4, 7], [2, 5, 8],
  [0, 4, 8], [2, 4, 6]
];

class TicTacToeApp {
  constructor() {
    this.board = Array(9).fill(null);
    this.turn = 'X';
    this.status = 'IN_PROGRESS';
    this.mode = 'ai-unbeatable'; // 'pvp', 'ai-unbeatable', 'ai-casual'
    this.winningLine = null;

    // Load LocalStorage stats
    this.scores = this.loadScores();

    // Sound, Strike, Confetti engines
    this.sound = new SoundEngine();
    this.strike = new StrikeRenderer(
      document.getElementById('strike-svg'),
      document.getElementById('game-board')
    );
    this.confetti = new ConfettiEngine(
      document.getElementById('confetti-canvas')
    );

    this.initDOM();
    this.bindEvents();
    this.render();
  }

  loadScores() {
    try {
      const saved = localStorage.getItem('neongrid_ttt_scores');
      if (saved) return JSON.parse(saved);
    } catch (e) {
      console.warn('LocalStorage error', e);
    }
    return { x: 0, o: 0, draws: 0, streak: 0, lastWinner: null };
  }

  saveScores() {
    try {
      localStorage.setItem('neongrid_ttt_scores', JSON.stringify(this.scores));
    } catch (e) {
      console.warn('LocalStorage save error', e);
    }
  }

  initDOM() {
    this.boardEl = document.getElementById('game-board');
    this.cells = Array.from(document.querySelectorAll('.grid-cell'));
    this.turnIndicator = document.getElementById('turn-indicator');
    this.scoreXEl = document.getElementById('score-x');
    this.scoreOEl = document.getElementById('score-o');
    this.scoreDrawEl = document.getElementById('score-draw');
    this.streakEl = document.getElementById('streak-count');
    this.modeSelect = document.getElementById('mode-select');
    this.modalEl = document.getElementById('result-modal');
    this.modalTitle = document.getElementById('modal-title');
    this.modalDesc = document.getElementById('modal-desc');
    this.btnRematch = document.getElementById('btn-rematch');
    this.btnReset = document.getElementById('btn-reset');
    this.btnResetStats = document.getElementById('btn-reset-stats');
    this.btnMute = document.getElementById('btn-mute');
  }

  bindEvents() {
    // Cell clicks
    this.cells.forEach((cell, idx) => {
      cell.addEventListener('click', () => this.handleCellClick(idx));
    });

    // Keyboard navigation (Keys 1-9 & Numpad 1-9)
    window.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;

      // Numpad mapping (standard layout: 7 8 9 top, 4 5 6 mid, 1 2 3 bot)
      const numpadMap = {
        'Numpad7': 0, 'Numpad8': 1, 'Numpad9': 2,
        'Numpad4': 3, 'Numpad5': 4, 'Numpad6': 5,
        'Numpad1': 6, 'Numpad2': 7, 'Numpad3': 8
      };
      if (e.code in numpadMap) {
        this.handleCellClick(numpadMap[e.code]);
        return;
      }

      // Standard top-row number keys (1 to 9 sequentially)
      if (e.key >= '1' && e.key <= '9') {
        const idx = parseInt(e.key, 10) - 1;
        this.handleCellClick(idx);
      } else if (e.key.toLowerCase() === 'r') {
        this.resetGame();
      }
    });

    // Mode dropdown
    this.modeSelect.addEventListener('change', (e) => {
      this.mode = e.target.value;
      this.resetGame();
    });

    // Rematch & Reset buttons
    this.btnRematch.addEventListener('click', () => {
      this.closeModal();
      this.resetGame();
    });
    this.btnReset.addEventListener('click', () => this.resetGame());
    this.btnResetStats.addEventListener('click', () => {
      this.scores = { x: 0, o: 0, draws: 0, streak: 0, lastWinner: null };
      this.saveScores();
      this.renderScores();
    });

    // Mute toggle
    this.btnMute.addEventListener('click', () => {
      const isMuted = this.sound.toggleMute();
      this.btnMute.textContent = isMuted ? '🔇 Audio Off' : '🔊 Audio On';
      this.btnMute.classList.toggle('text-slate-500', isMuted);
      this.btnMute.classList.toggle('text-sky-400', !isMuted);
    });
  }

  handleCellClick(index) {
    if (this.status !== 'IN_PROGRESS' || this.board[index] !== null) {
      if (this.status === 'IN_PROGRESS') {
        this.sound.playInvalid();
        this.shakeCell(index);
      }
      return;
    }

    // Make human move
    this.makeMove(index, this.turn);

    // If vs AI and game still in progress, trigger AI turn
    if (this.status === 'IN_PROGRESS' && this.mode !== 'pvp' && this.turn === 'O') {
      // Disable interaction briefly for realistic AI feel
      this.boardEl.classList.add('pointer-events-none');
      setTimeout(() => {
        this.playAITurn();
        this.boardEl.classList.remove('pointer-events-none');
      }, 260);
    }
  }

  shakeCell(index) {
    const cell = this.cells[index];
    if (!cell) return;
    cell.classList.add('animate-shake');
    setTimeout(() => cell.classList.remove('animate-shake'), 300);
  }

  makeMove(index, player) {
    this.board[index] = player;
    this.sound.playMove(player);

    const check = this.checkWinner(this.board);
    if (check.winner) {
      this.status = check.winner === 'X' ? 'WON_X' : 'WON_O';
      this.winningLine = check.line;
      this.onVictory(check.winner, check.line);
    } else if (check.draw) {
      this.status = 'DRAW';
      this.onDraw();
    } else {
      this.turn = player === 'X' ? 'O' : 'X';
    }

    this.render();
  }

  playAITurn() {
    if (this.status !== 'IN_PROGRESS') return;
    const aiMove = this.getMinimaxMove(this.board, 'O', this.mode === 'ai-unbeatable');
    if (aiMove !== -1) {
      this.makeMove(aiMove, 'O');
    }
  }

  checkWinner(b) {
    for (const [x, y, z] of WINNING_LINES) {
      if (b[x] && b[x] === b[y] && b[x] === b[z]) {
        return { winner: b[x], line: [x, y, z] };
      }
    }
    if (b.every(cell => cell !== null)) {
      return { draw: true };
    }
    return { winner: null, draw: false };
  }

  onVictory(winner, line) {
    this.sound.playVictory();
    this.strike.drawWinningLine(line, winner);

    // Update scoreboard
    if (winner === 'X') {
      this.scores.x++;
      this.scores.streak = (this.scores.lastWinner === 'X') ? this.scores.streak + 1 : 1;
      this.scores.lastWinner = 'X';
      this.confetti.burst(140);
    } else {
      this.scores.o++;
      this.scores.streak = (this.scores.lastWinner === 'O') ? this.scores.streak + 1 : 1;
      this.scores.lastWinner = 'O';
      if (this.mode === 'pvp') {
        this.confetti.burst(140);
      }
    }
    this.saveScores();

    // Show victory modal overlay (TSK-04)
    setTimeout(() => {
      this.showModal(
        `${winner === 'X' ? 'Player 1 (X)' : (this.mode === 'pvp' ? 'Player 2 (O)' : 'AI (O)')} Wins!`,
        `Winning line [${line.map(i => i + 1).join(', ')}] matched with precision neon glow. Current streak: ${this.scores.streak}`
      );
    }, 700);
  }

  onDraw() {
    this.sound.playDraw();
    this.scores.draws++;
    this.scores.streak = 0;
    this.scores.lastWinner = null;
    this.saveScores();

    setTimeout(() => {
      this.showModal('Draw Game!', 'All 9 cells occupied with zero winning vector lines. A perfectly matched round.');
    }, 500);
  }

  showModal(title, desc) {
    this.modalTitle.textContent = title;
    this.modalDesc.textContent = desc;
    this.modalEl.classList.remove('hidden');
    this.modalEl.classList.add('flex');
  }

  closeModal() {
    this.modalEl.classList.add('hidden');
    this.modalEl.classList.remove('flex');
  }

  resetGame() {
    this.board = Array(9).fill(null);
    this.turn = 'X';
    this.status = 'IN_PROGRESS';
    this.winningLine = null;
    this.strike.clear();
    this.confetti.clear();
    this.closeModal();
    this.sound.playReset();
    this.render();
  }

  render() {
    // Render cells
    this.cells.forEach((cell, idx) => {
      const val = this.board[idx];
      cell.innerHTML = '';
      cell.classList.remove('occupied-x', 'occupied-o', 'win-cell');

      if (val === 'X') {
        cell.classList.add('occupied-x');
        cell.innerHTML = `
          <svg class="w-12 h-12 md:w-16 md:h-16 text-cyan-400 drop-shadow-[0_0_12px_rgba(6,182,212,0.8)] animate-scaleIn" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        `;
      } else if (val === 'O') {
        cell.classList.add('occupied-o');
        cell.innerHTML = `
          <svg class="w-12 h-12 md:w-16 md:h-16 text-fuchsia-400 drop-shadow-[0_0_12px_rgba(217,70,239,0.8)] animate-scaleIn" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round">
            <circle cx="12" cy="12" r="8"></circle>
          </svg>
        `;
      }

      if (this.winningLine && this.winningLine.includes(idx)) {
        cell.classList.add('win-cell');
      }
    });

    // Render turn indicator
    if (this.status === 'IN_PROGRESS') {
      const isX = this.turn === 'X';
      this.turnIndicator.innerHTML = `
        <span class="inline-flex items-center gap-2 font-mono text-sm uppercase tracking-wider ${isX ? 'text-cyan-400' : 'text-fuchsia-400'}">
          <span class="w-2.5 h-2.5 rounded-full ${isX ? 'bg-cyan-400 shadow-[0_0_8px_#22d3ee]' : 'bg-fuchsia-400 shadow-[0_0_8px_#e879f9]'} animate-pulse"></span>
          Turn: Player ${this.turn} ${(!isX && this.mode !== 'pvp') ? '(AI Thinking...)' : ''}
        </span>
      `;
    } else if (this.status === 'DRAW') {
      this.turnIndicator.innerHTML = `<span class="font-mono text-sm text-slate-400 uppercase tracking-wider">Game Over: Draw</span>`;
    } else {
      const winner = this.status === 'WON_X' ? 'X' : 'O';
      this.turnIndicator.innerHTML = `<span class="font-mono text-sm uppercase tracking-wider font-bold ${winner === 'X' ? 'text-cyan-400' : 'text-fuchsia-400'}">Victory: Player ${winner}!</span>`;
    }

    this.renderScores();
  }

  renderScores() {
    this.scoreXEl.textContent = this.scores.x;
    this.scoreOEl.textContent = this.scores.o;
    this.scoreDrawEl.textContent = this.scores.draws;
    this.streakEl.textContent = this.scores.streak;
  }

  // --- Client-Side Minimax AI Engine ---
  getMinimaxMove(board, aiPlayer = 'O', isUnbeatable = true) {
    const avail = board.map((c, i) => c === null ? i : null).filter(c => c !== null);
    if (avail.length === 0) return -1;
    if (avail.length === 1) return avail[0];

    // Casual error injection (28%)
    if (!isUnbeatable && Math.random() < 0.28) {
      return avail[Math.floor(Math.random() * avail.length)];
    }

    let bestScore = -Infinity;
    let bestMove = avail[0];
    const humanPlayer = aiPlayer === 'O' ? 'X' : 'O';

    for (const move of avail) {
      board[move] = aiPlayer;
      const score = this._minimax(board, 0, false, aiPlayer, humanPlayer, -Infinity, Infinity);
      board[move] = null;
      if (score > bestScore) {
        bestScore = score;
        bestMove = move;
      }
    }
    return bestMove;
  }

  _minimax(board, depth, isMaximizing, aiPlayer, humanPlayer, alpha, beta) {
    const check = this.checkWinner(board);
    if (check.winner === aiPlayer) return 10 - depth;
    if (check.winner === humanPlayer) return depth - 10;
    if (check.draw) return 0;

    const avail = board.map((c, i) => c === null ? i : null).filter(c => c !== null);

    if (isMaximizing) {
      let maxEval = -Infinity;
      for (const move of avail) {
        board[move] = aiPlayer;
        const evaluation = this._minimax(board, depth + 1, false, aiPlayer, humanPlayer, alpha, beta);
        board[move] = null;
        maxEval = Math.max(maxEval, evaluation);
        alpha = Math.max(alpha, evaluation);
        if (beta <= alpha) break;
      }
      return maxEval;
    } else {
      let minEval = Infinity;
      for (const move of avail) {
        board[move] = humanPlayer;
        const evaluation = this._minimax(board, depth + 1, true, aiPlayer, humanPlayer, alpha, beta);
        board[move] = null;
        minEval = Math.min(minEval, evaluation);
        beta = Math.min(beta, evaluation);
        if (beta <= alpha) break;
      }
      return minEval;
    }
  }
}

// Bootstrap on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  window.app = new TicTacToeApp();
});
