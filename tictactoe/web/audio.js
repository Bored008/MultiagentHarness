/**
 * Procedural Web Audio API Sound Synthesis Engine for NeonGrid Tic Tac Toe.
 * Strictly zero external MP3 assets; synthesized via dynamic oscillators & gain nodes.
 * Adheres to DEC-01 and TSK-03 specifications.
 */

class SoundEngine {
  constructor() {
    this.ctx = null;
    this.muted = false;
    this._initOnInteraction = this._initOnInteraction.bind(this);
    window.addEventListener('click', this._initOnInteraction, { once: true });
    window.addEventListener('keydown', this._initOnInteraction, { once: true });
  }

  _initContext() {
    if (!this.ctx) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) {
        this.ctx = new AudioCtx();
      }
    }
    if (this.ctx && this.ctx.state === 'suspended') {
      this.ctx.resume();
    }
  }

  _initOnInteraction() {
    this._initContext();
  }

  toggleMute() {
    this.muted = !this.muted;
    return this.muted;
  }

  /**
   * Sound 1: Move Click (Player X or O token placement)
   * Crispy dynamic frequency drop with quick exponential gain envelope.
   */
  playMove(player = 'X') {
    if (this.muted) return;
    this._initContext();
    if (!this.ctx) return;

    const osc = this.ctx.createOscillator();
    const gain = this.ctx.createGain();
    const now = this.ctx.currentTime;

    // Distinct pitches: X is higher neon chime, O is slightly deeper resonance
    const baseFreq = player === 'X' ? 880 : 587.33; // A5 vs D5
    osc.type = player === 'X' ? 'triangle' : 'sine';
    osc.frequency.setValueAtTime(baseFreq, now);
    osc.frequency.exponentialRampToValueAtTime(baseFreq * 0.5, now + 0.12);

    gain.gain.setValueAtTime(0.28, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.12);

    osc.connect(gain);
    gain.connect(this.ctx.destination);

    osc.start(now);
    osc.stop(now + 0.13);
  }

  /**
   * Sound 2: Invalid Move Attempt (occupied cell or out of bounds)
   * Low discordant frequency pulse with vibrato buzz.
   */
  playInvalid() {
    if (this.muted) return;
    this._initContext();
    if (!this.ctx) return;

    const osc1 = this.ctx.createOscillator();
    const osc2 = this.ctx.createOscillator();
    const gain = this.ctx.createGain();
    const now = this.ctx.currentTime;

    osc1.type = 'sawtooth';
    osc2.type = 'square';
    osc1.frequency.setValueAtTime(140, now);
    osc2.frequency.setValueAtTime(147, now); // Detuned for dissonance

    gain.gain.setValueAtTime(0.25, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.18);

    osc1.connect(gain);
    osc2.connect(gain);
    gain.connect(this.ctx.destination);

    osc1.start(now);
    osc2.start(now);
    osc1.stop(now + 0.19);
    osc2.stop(now + 0.19);
  }

  /**
   * Sound 3: Victory Chime (Winning match sequence)
   * Triumphant ascending arpeggio (C5 -> E5 -> G5 -> C6) with lingering harmonics.
   */
  playVictory() {
    if (this.muted) return;
    this._initContext();
    if (!this.ctx) return;

    const notes = [523.25, 659.25, 783.99, 1046.50]; // C5, E5, G5, C6
    const now = this.ctx.currentTime;

    notes.forEach((freq, index) => {
      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();
      const startTime = now + index * 0.10;

      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, startTime);

      gain.gain.setValueAtTime(0.28, startTime);
      gain.gain.exponentialRampToValueAtTime(0.001, startTime + 0.45);

      osc.connect(gain);
      gain.connect(this.ctx.destination);

      osc.start(startTime);
      osc.stop(startTime + 0.48);
    });
  }

  /**
   * Sound 4: Draw Buzz (Tie terminal state)
   * Muted descending neutral hum.
   */
  playDraw() {
    if (this.muted) return;
    this._initContext();
    if (!this.ctx) return;

    const osc = this.ctx.createOscillator();
    const gain = this.ctx.createGain();
    const now = this.ctx.currentTime;

    osc.type = 'triangle';
    osc.frequency.setValueAtTime(320, now);
    osc.frequency.linearRampToValueAtTime(180, now + 0.35);

    gain.gain.setValueAtTime(0.25, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.35);

    osc.connect(gain);
    gain.connect(this.ctx.destination);

    osc.start(now);
    osc.stop(now + 0.36);
  }

  /**
   * Sound 5: Board Reset
   */
  playReset() {
    if (this.muted) return;
    this._initContext();
    if (!this.ctx) return;

    const osc = this.ctx.createOscillator();
    const gain = this.ctx.createGain();
    const now = this.ctx.currentTime;

    osc.type = 'sine';
    osc.frequency.setValueAtTime(1200, now);
    osc.frequency.exponentialRampToValueAtTime(600, now + 0.08);

    gain.gain.setValueAtTime(0.2, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.08);

    osc.connect(gain);
    gain.connect(this.ctx.destination);

    osc.start(now);
    osc.stop(now + 0.09);
  }
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { SoundEngine };
}
