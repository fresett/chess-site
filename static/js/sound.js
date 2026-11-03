(function (root) {
'use strict';
// Звуки синтезируются прямо в браузере (Web Audio), без файлов.
// Три набора (настройка «Звуки» в окне «Дизайн»): classic — мягкие тоны, wood — стук дерева, chime — колокольчики.
let ctx = null, muted = false;
try { muted = localStorage.getItem('mute') === '1'; } catch (e) {}
function ac() { if (!ctx) { const A = root.AudioContext || root.webkitAudioContext; if (!A) return null; ctx = new A(); } if (ctx.state === 'suspended') ctx.resume(); return ctx; }
function tone(f, t, d, type, vol, f2) {
  const a = ac(); if (!a) return; const o = a.createOscillator(), g = a.createGain(), s = a.currentTime + t;
  o.type = type || 'sine'; o.frequency.setValueAtTime(f, s); if (f2) o.frequency.exponentialRampToValueAtTime(f2, s + d);
  g.gain.setValueAtTime(0.0001, s); g.gain.exponentialRampToValueAtTime(vol, s + 0.006); g.gain.exponentialRampToValueAtTime(0.0001, s + d);
  o.connect(g); g.connect(a.destination); o.start(s); o.stop(s + d + 0.03);
}
// короткий шумовой щелчок через полосовой фильтр — звук дерева
function knock(t, vol, freq, q, len) {
  const a = ac(); if (!a) return; const n = Math.floor(a.sampleRate * (len || 0.07)), b = a.createBuffer(1, n, a.sampleRate), d = b.getChannelData(0);
  for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / n, 6);
  const s = a.createBufferSource(), f = a.createBiquadFilter(), g = a.createGain();
  s.buffer = b; f.type = 'bandpass'; f.frequency.value = freq || 900; f.Q.value = q || 3; g.gain.value = vol;
  s.connect(f); f.connect(g); g.connect(a.destination); s.start(a.currentTime + t);
}
// колокольчик: основной тон + негармонические обертоны с долгим затуханием
function bell(f, t, vol, d) { tone(f, t, d || 0.9, 'sine', vol); tone(f * 2.76, t, (d || 0.9) * 0.5, 'sine', vol * 0.35); tone(f * 5.4, t, (d || 0.9) * 0.25, 'sine', vol * 0.12); }

const SETS = {
  classic: {
    move() { tone(660, 0, 0.11, 'sine', 0.2, 520); tone(1320, 0, 0.06, 'sine', 0.05); },
    capture() { tone(520, 0, 0.14, 'sine', 0.26, 330); tone(780, 0.03, 0.1, 'triangle', 0.08); },
    castle() { tone(600, 0, 0.1, 'sine', 0.2, 500); tone(760, 0.12, 0.1, 'sine', 0.2, 620); },
    check() { tone(700, 0, 0.12, 'sine', 0.22); tone(940, 0.1, 0.2, 'sine', 0.22); },
  },
  wood: {
    move() { knock(0, 0.9, 1100, 4, 0.05); tone(180, 0, 0.06, 'sine', 0.18, 120); },
    capture() { knock(0, 1, 800, 3, 0.08); knock(0.045, 0.7, 1300, 5, 0.05); tone(150, 0, 0.09, 'sine', 0.22, 90); },
    castle() { knock(0, 0.85, 1100, 4, 0.05); knock(0.13, 0.85, 950, 4, 0.05); },
    check() { knock(0, 1, 1500, 6, 0.05); knock(0.09, 0.9, 1800, 6, 0.05); tone(880, 0.09, 0.15, 'triangle', 0.06); },
  },
  chime: {
    move() { bell(1046, 0, 0.11, 0.5); },
    capture() { bell(784, 0, 0.13, 0.6); bell(1175, 0.05, 0.08, 0.5); },
    castle() { bell(880, 0, 0.1, 0.45); bell(1175, 0.12, 0.1, 0.5); },
    check() { bell(1319, 0, 0.12, 0.6); bell(1568, 0.1, 0.1, 0.6); },
  },
};
const COMMON = {
  start() { tone(392, 0, 0.14, 'triangle', 0.18); tone(523, 0.12, 0.2, 'triangle', 0.18); },
  win() { [523, 659, 784, 1047].forEach((f, i) => tone(f, i * 0.12, 0.35, 'triangle', 0.2)); },
  loss() { [392, 349, 311, 262].forEach((f, i) => tone(f, i * 0.16, 0.4, 'sawtooth', 0.07)); },
  draw() { tone(440, 0, 0.25, 'triangle', 0.16); tone(440, 0.22, 0.3, 'triangle', 0.16); },
  wrong() { tone(220, 0, 0.18, 'square', 0.08, 150); },
  ok() { tone(660, 0, 0.12, 'triangle', 0.15); tone(880, 0.1, 0.18, 'triangle', 0.15); },
  notify() { tone(880, 0, 0.12, 'sine', 0.15); tone(1175, 0.1, 0.18, 'sine', 0.13); },
};
const set = () => SETS[document.documentElement.dataset.sound] || SETS.classic;
root.Sfx = {
  play(n) { if (muted) return; const f = set()[n] || COMMON[n]; if (f) try { f(); } catch (e) {} },
  san(s) { this.play(/[+#]/.test(s) ? 'check' : s.startsWith('O-O') ? 'castle' : s.includes('x') ? 'capture' : 'move'); },
  toggle() { muted = !muted; try { localStorage.setItem('mute', muted ? '1' : '0'); } catch (e) {} return muted; },
  muted() { return muted; },
};
})(this);
