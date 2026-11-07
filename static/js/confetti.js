(function (root) {
'use strict';
// Конфетти при победе — бонус Rookline Pro. Рисуется на холсте поверх страницы и сам исчезает.
const COLORS = ['#f6cf73', '#e9a531', '#ff3ea5', '#22e4ff', '#5dff6a', '#b45cff', '#ffffff'];

function burst(count) {
  if (document.body.dataset.pro !== '1') return;
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const cv = document.createElement('canvas');
  cv.className = 'confetti';
  const dpr = Math.min(2, root.devicePixelRatio || 1);
  const W = innerWidth, H = innerHeight;
  cv.width = W * dpr; cv.height = H * dpr;
  document.body.appendChild(cv);
  const c = cv.getContext('2d'); c.scale(dpr, dpr);
  const n = count || 140, parts = [];
  for (let i = 0; i < n; i++) {
    const fromLeft = i % 2 === 0;
    parts.push({
      x: fromLeft ? -10 : W + 10, y: H * (0.55 + Math.random() * 0.3),
      vx: (fromLeft ? 1 : -1) * (5 + Math.random() * 9), vy: -(11 + Math.random() * 10),
      w: 6 + Math.random() * 6, h: 8 + Math.random() * 8, r: Math.random() * 6.28, vr: (Math.random() - 0.5) * 0.4,
      col: COLORS[i % COLORS.length], round: Math.random() < 0.25,
    });
  }
  const t0 = performance.now();
  (function frame(now) {
    const t = now - t0;
    c.clearRect(0, 0, W, H);
    for (const p of parts) {
      p.vy += 0.32; p.vx *= 0.985; p.vy *= 0.99; p.x += p.vx; p.y += p.vy; p.r += p.vr;
      c.save(); c.translate(p.x, p.y); c.rotate(p.r);
      c.globalAlpha = Math.max(0, 1 - Math.max(0, t - 2200) / 900);
      c.fillStyle = p.col;
      if (p.round) { c.beginPath(); c.arc(0, 0, p.w / 2, 0, 6.28); c.fill(); }
      else c.fillRect(-p.w / 2, -p.h / 2, p.w, p.h * Math.abs(Math.cos(p.r * 1.7)));
      c.restore();
    }
    if (t < 3100) requestAnimationFrame(frame); else cv.remove();
  })(t0);
}
root.Confetti = { burst };
})(this);
