(function () {
'use strict';
// Разбор партии (Rookline Pro): движок в фоновом потоке проверяет каждый твой ход —
// сравнивает с лучшим и отмечает неточности, ошибки и зевки. Клик по строке — показать позицию.
const $ = s => document.querySelector(s);
const stage = $('#astage'), PRO = stage.dataset.pro === '1', COLOR = stage.dataset.color === 'b' ? 'b' : 'w';
const btn = $('#revgo');
if (!btn || !window.AN) return;

const KINDS = {
  best: { t: 'Лучший ход', s: '!', c: 'k-best' },
  good: { t: 'Хороший ход', s: '', c: 'k-good' },
  inacc: { t: 'Неточность', s: '?!', c: 'k-inacc' },
  mistake: { t: 'Ошибка', s: '?', c: 'k-mistake' },
  blunder: { t: 'Зевок', s: '??', c: 'k-blunder' },
};
const classify = loss => loss <= 10 ? 'best' : loss < 50 ? 'good' : loss < 120 ? 'inacc' : loss < 300 ? 'mistake' : 'blunder';
const pawns = cp => (cp >= 0 ? '−' : '+') + (Math.abs(cp) / 100).toFixed(1);

if (!PRO) {
  btn.onclick = () => { location.href = '/pro'; };
  btn.title = 'Разбор партии доступен с Rookline Pro';
  return;
}

let worker = null, seq = 0;
const pend = new Map();
function ask(fen, mv) {
  if (!worker) {
    worker = new Worker('/static/js/engine-worker.js');
    worker.onmessage = e => { const f = pend.get(e.data.id); if (f) { pend.delete(e.data.id); f(e.data.res); } };
  }
  return new Promise(res => { const id = ++seq; pend.set(id, res); worker.postMessage({ id, cmd: 'review', fen, mv, opts: { depth: 3, time: 700, tdepth: 1, ttime: 60 } }); });
}

async function run() {
  btn.disabled = true; btn.textContent = 'Альберт разбирает партию…';
  $('#revprog').classList.remove('hidden');
  const ucis = AN.line(), g = new Chess.Game(AN.start()), items = [];
  const mine = [];
  for (let i = 0; i < ucis.length; i++) {
    const legal = g.legal(), u = ucis[i];
    const m = legal.find(x => Chess.sqn(x.from) + Chess.sqn(x.to) + (x.promo || '') === u);
    if (!m) break;
    if (g.turn === COLOR) mine.push({ i, fen: g.fen(), mv: { from: m.from, to: m.to, promo: m.promo }, san: g.san(m, legal), num: Math.floor(i / 2) + 1 });
    g.move(m.from, m.to, m.promo);
  }
  let done = 0;
  for (const x of mine) {
    const r = await ask(x.fen, x.mv);
    done++;
    $('#revbar').style.width = (100 * done / mine.length) + '%';
    $('#revtxt').textContent = `Проверено ходов: ${done} из ${mine.length}`;
    if (!r || r.error || !r.best || !r.mine) continue;
    const loss = Math.max(0, Math.min(1000, r.best.s - r.mine.s));
    items.push({ ...x, loss, kind: classify(loss), best: r.best });
  }
  render(items);
}

function render(items) {
  $('#revprog').classList.add('hidden');
  btn.classList.add('hidden');
  const cnt = { best: 0, good: 0, inacc: 0, mistake: 0, blunder: 0 };
  items.forEach(x => cnt[x.kind]++);
  const acc = items.length ? Math.round(items.reduce((s, x) => s + 100 * Math.exp(-x.loss / 300), 0) / items.length) : 0;
  const bad = items.filter(x => x.kind === 'inacc' || x.kind === 'mistake' || x.kind === 'blunder');
  const chip = k => `<span class="rchip ${KINDS[k].c}"><b>${cnt[k]}</b> ${KINDS[k].t.toLowerCase()}${KINDS[k].s ? ' ' + KINDS[k].s : ''}</span>`;
  let h = `<div class="racc"><b>${acc}%</b><span>точность твоих ходов</span></div>`;
  h += `<div class="rchips">${['best', 'good', 'inacc', 'mistake', 'blunder'].map(chip).join('')}</div>`;
  if (!bad.length) h += '<p class="muted">Ошибок не найдено — отличная партия! 🎉</p>';
  else {
    h += '<div class="rlist">';
    for (const x of bad) {
      const dots = x.i % 2 ? '…' : '.';
      h += `<button type="button" class="ritem ${KINDS[x.kind].c}" data-i="${x.i}" data-f="${x.best.from}" data-t="${x.best.to}">`
        + `<b>${x.num}${dots} ${x.san}${KINDS[x.kind].s}</b><span>${KINDS[x.kind].t} · ${pawns(x.loss)}</span>`
        + `<small>Лучше: ${x.best.san}</small></button>`;
    }
    h += '</div><p class="muted small">Нажми на ход — покажу позицию и лучший ход стрелкой.</p>';
  }
  const out = $('#revout'); out.innerHTML = h; out.classList.remove('hidden');
}

$('#revout').addEventListener('click', e => {
  const b = e.target.closest('.ritem'); if (!b) return;
  document.querySelectorAll('.ritem.on').forEach(x => x.classList.remove('on')); b.classList.add('on');
  AN.go(+b.dataset.i);
  const bd = AN.board;
  bd.shapes = (bd.shapes || []).filter(s => !s.review);
  bd.shapes.push({ from: +b.dataset.f, to: +b.dataset.t, c: 'g', review: true });
  bd.drawShapes();
});

btn.onclick = run;
if (new URLSearchParams(location.search).get('review') === '1') run();
})();
