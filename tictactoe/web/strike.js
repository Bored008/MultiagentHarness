/**
 * Dynamic SVG Winning Strike-Through Line Renderer (TSK-04).
 * Animates a glowing neon vector stroke through the center of 3 winning cells.
 */

class StrikeRenderer {
  constructor(svgElement, boardElement) {
    this.svg = svgElement;
    this.board = boardElement;
  }

  clear() {
    this.svg.innerHTML = '';
  }

  /**
   * Draw glowing strike-through line across winning cell indices.
   * @param {Array<number>} winningIndices - e.g. [0, 1, 2] or [0, 4, 8]
   * @param {string} player - "X" or "O"
   */
  drawWinningLine(winningIndices, player = 'X') {
    if (!winningIndices || winningIndices.length < 3) return;

    this.clear();
    const cells = this.board.querySelectorAll('.grid-cell');
    const firstCell = cells[winningIndices[0]];
    const lastCell = cells[winningIndices[2]];

    if (!firstCell || !lastCell) return;

    const boardRect = this.board.getBoundingClientRect();
    const firstRect = firstCell.getBoundingClientRect();
    const lastRect = lastCell.getBoundingClientRect();

    // Center coordinates relative to the board
    const x1 = firstRect.left + firstRect.width / 2 - boardRect.left;
    const y1 = firstRect.top + firstRect.height / 2 - boardRect.top;
    const x2 = lastRect.left + lastRect.width / 2 - boardRect.left;
    const y2 = lastRect.top + lastRect.height / 2 - boardRect.top;

    const color = player === 'X' ? '#06B6D4' : '#D946EF';
    const shadowColor = player === 'X' ? 'rgba(6, 182, 212, 0.8)' : 'rgba(217, 70, 239, 0.8)';

    // Compute stroke length for dash animation
    const length = Math.hypot(x2 - x1, y2 - y1);

    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', x1);
    line.setAttribute('y1', y1);
    line.setAttribute('x2', x2);
    line.setAttribute('y2', y2);
    line.setAttribute('stroke', color);
    line.setAttribute('stroke-width', '8');
    line.setAttribute('stroke-linecap', 'round');
    line.setAttribute('style', `
      filter: drop-shadow(0 0 10px ${shadowColor}) drop-shadow(0 0 20px ${shadowColor});
      stroke-dasharray: ${length};
      stroke-dashoffset: ${length};
      animation: strikeDraw 0.4s ease-out forwards;
    `);

    this.svg.appendChild(line);
  }
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { StrikeRenderer };
}
