(function (root) {
'use strict';
const { sqn, isW } = root.Chess;
const src = p => root.Design.pieceSrc((isW(p) ? 'w' : 'b') + p.toLowerCase());
const glyph = p => `<img class="pc" draggable="false" alt="" src="${src(p)}">`;
// значки оценки хода от тренера
const BADGE = { best: ['!', 'Превосходный ход'], good: ['★', 'Хороший ход'], book: ['📖', 'Книжный ход'], inacc: ['?', 'Сомнительный ход'], blunder: ['??', 'Грубая ошибка'] };
const badgeHtml = k => `<span class="badge k-${k}" title="${BADGE[k][1]}">${BADGE[k][0]}</span>`;
class Board {
  constructor(el, game, o) {
    this.el = el; this.g = game; this.o = o || {};
    this.flip = false; this.sel = -1; this.targets = []; this.last = null; this.hint = -1; this.pending = null; this.drag = null; this.dropped = false;
    this.badge = null; // {sq, k}: значок оценки хода над фигурой
    this.warn = -1;    // клетка фигуры, которой угрожают (тренер подсвечивает красным)
    el.addEventListener('pointerdown', e => this.down(e));
    root.addEventListener('designchange', () => this.render());
    el.addEventListener('click', e => {
      const pr = e.target.closest('.pr');
      if (pr && this.pending) { const { from, to, dropped } = this.pending; this.pending = null; this.fire(from, to, pr.dataset.p, dropped); }
    });
  }
  down(e) {
    if (e.button !== 0 || this.pending || e.target.closest('.pr')) return;
    const s = e.target.closest('.sq'); if (!s || !this.o.canMove || !this.o.canMove()) return;
    const i = +s.dataset.i, g = this.g, p = g.b[i];
    if (this.sel >= 0) { const ms = this.targets.filter(m => m.to === i); if (ms.length) { this.choose(this.sel, i, ms[0].promo, false); return; } }
    if (p && isW(p) === (g.turn === 'w')) {
      this.sel = i; this.targets = g.legal().filter(m => m.from === i); this.render();
      this.drag = { i, x: e.clientX, y: e.clientY, on: false, p };
      this.mv = ev => this.move(ev); this.up = ev => this.release(ev);
      document.addEventListener('pointermove', this.mv); document.addEventListener('pointerup', this.up); document.addEventListener('pointercancel', this.up);
      e.preventDefault();
    } else { this.sel = -1; this.targets = []; this.render(); }
  }
  move(e) {
    const d = this.drag; if (!d) return;
    if (!d.on) {
      if (Math.hypot(e.clientX - d.x, e.clientY - d.y) < 5) return;
      d.on = true;
      const sq = this.el.querySelector(`.sq[data-i="${d.i}"]`); this.size = sq.getBoundingClientRect().width;
      d.img = document.createElement('img'); d.img.src = src(d.p); d.img.className = 'dragpiece';
      d.img.style.width = d.img.style.height = this.size + 'px'; document.body.appendChild(d.img);
      const pc = sq.querySelector('.pc'); if (pc) pc.style.opacity = '.2';
      document.body.classList.add('grabbing');
    }
    d.img.style.transform = `translate(${e.clientX - this.size / 2}px,${e.clientY - this.size / 2}px) scale(1.12)`;
  }
  release(e) {
    document.removeEventListener('pointermove', this.mv); document.removeEventListener('pointerup', this.up); document.removeEventListener('pointercancel', this.up);
    const d = this.drag; this.drag = null; if (!d || !d.on) return;
    d.img.remove(); document.body.classList.remove('grabbing');
    const t = document.elementFromPoint(e.clientX, e.clientY), sq = t && t.closest && t.closest('.sq');
    const j = sq && this.el.contains(sq) ? +sq.dataset.i : -1, ms = this.targets.filter(m => m.to === j);
    if (ms.length) this.choose(d.i, j, ms[0].promo, true); else this.render();
  }
  choose(from, to, promo, dropped) {
    if (promo) { this.pending = { from, to, dropped }; this.render(); } else this.fire(from, to, undefined, dropped);
  }
  fire(from, to, promo, dropped) { this.sel = -1; this.targets = []; this.hint = -1; this.dropped = !!dropped; this.o.onMove(from, to, promo); }
  // Анимация последнего хода. cap — взятая фигура (если была), чтобы показать, как она исчезает.
  animateLast(cap) {
    const dropped = this.dropped; this.dropped = false;
    if (!this.last) return;
    const [from, to] = this.last, sq = i => this.el.querySelector(`.sq[data-i="${i}"]`);
    const a = sq(from), b = sq(to); if (!b) return;
    // волна по клетке, куда пришла фигура
    const rip = document.createElement('span'); rip.className = 'rip'; b.appendChild(rip);
    rip.addEventListener('animationend', () => rip.remove());
    // взятая фигура тает
    if (cap) {
      const c = document.createElement('img'); c.className = 'pc cap'; c.alt = ''; c.src = src(cap); b.appendChild(c);
      c.animate([{ transform: 'scale(1)', opacity: 1 }, { transform: 'scale(.35) rotate(28deg)', opacity: 0 }], { duration: 340, easing: 'ease-in', fill: 'forwards' }).onfinish = () => c.remove();
    }
    const pc = b.querySelector('.pc:not(.cap)'); if (!pc) return;
    if (dropped || !a) {
      // фигуру перетащили мышью — «приземление» с отскоком
      pc.animate([{ transform: 'scale(1.25)' }, { transform: 'scale(.92)', offset: .6 }, { transform: 'scale(1)' }], { duration: 300, easing: 'ease-out' });
    } else this.slide(a, b, pc, 280);
    // рокировка: ладья тоже едет
    const p = this.g.b[to];
    if (p && p.toLowerCase() === 'k' && Math.abs(to - from) === 2) {
      const rf = to > from ? to + 1 : to - 2, rt = to > from ? to - 1 : to + 1, ra = sq(rf), rb = sq(rt);
      const rp = rb && rb.querySelector('.pc'); if (ra && rp) this.slide(ra, rb, rp, 320);
    }
  }
  // Анимация отмены ходов: фигуры едут обратно, взятые — появляются снова.
  // moves — отменённые ходы (объекты m из движка), от последнего к более раннему.
  animateBack(moves) {
    const sq = i => this.el.querySelector(`.sq[data-i="${i}"]`);
    for (const m of moves) {
      const a = sq(m.to), b = sq(m.from), pc = b && b.querySelector('.pc');
      if (a && pc) this.slide(a, b, pc, 300);
      if (m.cap && m.flag !== 'e' && this.g.b[m.to] === m.cap) {
        const back = a && a.querySelector('.pc');
        if (back) back.animate([{ transform: 'scale(.3)', opacity: 0 }, { transform: 'scale(1.08)', opacity: 1, offset: .7 }, { transform: 'scale(1)', opacity: 1 }], { duration: 340, easing: 'ease-out' });
      }
      if (m.flag === 'c') {
        const rf = m.to > m.from ? m.to - 1 : m.to + 1, rt = m.to > m.from ? m.to + 1 : m.to - 2, ra = sq(rf), rb = sq(rt);
        const rp = rb && rb.querySelector('.pc'); if (ra && rp) this.slide(ra, rb, rp, 320);
      }
    }
  }
  setBadge(i, k) {
    this.badge = k ? { sq: i, k } : null;
    this.el.querySelectorAll('.badge').forEach(x => x.remove());
    if (!k) return;
    const s = this.el.querySelector(`.sq[data-i="${i}"]`); if (s) s.insertAdjacentHTML('beforeend', badgeHtml(k));
  }
  slide(a, b, pc, ms) {
    const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect(), dx = ra.left - rb.left, dy = ra.top - rb.top;
    b.style.zIndex = 6;
    pc.animate([
      { transform: `translate(${dx}px,${dy}px) scale(1.08)` },
      { transform: 'translate(0,0) scale(1.1)', offset: .8 },
      { transform: 'translate(0,0) scale(1)' }
    ], { duration: ms, easing: 'cubic-bezier(.25,.8,.25,1)' }).onfinish = () => { b.style.zIndex = ''; };
  }
  render() {
    const g = this.g, chk = g.inCheck() ? g.k[g.turn] : -1;
    let h = '';
    for (let vr = 0; vr < 8; vr++) for (let vc = 0; vc < 8; vc++) {
      const i = this.flip ? 63 - (vr * 8 + vc) : vr * 8 + vc, p = g.b[i];
      let c = 'sq ' + ((((i >> 3) + (i & 7)) % 2 === 0) ? 'l' : 'd');
      if (this.last && (i === this.last[0] || i === this.last[1])) c += ' last';
      if (i === this.sel) c += ' sel';
      if (i === chk) c += ' chk';
      if (i === this.hint) c += ' hint';
      if (i === this.warn) c += ' warn';
      if (this.targets.some(m => m.to === i)) c += p ? ' ring' : ' dot';
      h += `<div class="${c}" data-i="${i}">` +
        (vc === 0 ? `<span class="co r">${sqn(i)[1]}</span>` : '') + (vr === 7 ? `<span class="co f">${sqn(i)[0]}</span>` : '') +
        (p ? glyph(p) : '') + (this.badge && this.badge.sq === i ? badgeHtml(this.badge.k) : '') + '</div>';
    }
    if (this.pending) {
      const w = g.turn === 'w';
      h += '<div class="promo">' + ['q', 'r', 'b', 'n'].map(x => `<div class="pr" data-p="${x}">${glyph(w ? x.toUpperCase() : x)}</div>`).join('') + '</div>';
    }
    this.el.innerHTML = h;
  }
}
root.Board = Board;
})(this);
