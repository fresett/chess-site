(function () {
'use strict';
const { Game, sqi } = Chess;
const $ = s => document.querySelector(s);
const csrf = document.querySelector('meta[name=csrf]').content;
const auth = document.body.dataset.auth === '1';
let list = [], cur = 0, g, board, step = 0, done = false, busy = false, failed = false;
const uci = m => Chess.sqn(m.from) + Chess.sqn(m.to) + (m.promo || '');
const mateNow = x => !x.legal().length && x.inCheck();
function forcedMate1(x) {
  const r = x.legal(); if (!r.length) return false;
  for (const a of r) {
    x.make(a); let ok = false;
    for (const m of x.legal()) { x.make(m); if (mateNow(x)) ok = true; x.unmake(); if (ok) break; }
    x.unmake(); if (!ok) return false;
  }
  return true;
}
const msg = t => { $('#status').textContent = t; };
function load(i) {
  cur = i; const p = list[i]; g = new Game(p.fen); step = 0; done = false; busy = false; failed = false;
  try { history.replaceState(null, '', '?id=' + p.id); } catch (e) {}
  if (!board) board = new Board($('#board'), g, { canMove: () => !done && !busy, onMove });
  board.g = g; board.sel = -1; board.targets = []; board.last = null; board.hint = -1; board.pending = null; board.dropped = false;
  board.flip = g.turn === 'b';
  $('#ptitle').textContent = `Задача ${p.id}: ${p.title}`;
  msg(`${g.turn === 'w' ? 'Ход белых' : 'Ход чёрных'} — мат в ${p.mate} ${p.mate === 1 ? 'ход' : 'хода'}`);
  board.render(); renderList();
}
function renderList() { $('#plist').innerHTML = list.map((p, i) => `<button data-i="${i}" class="${i === cur ? 'on' : ''} ${p.solved ? 'ok' : ''} ${p.pod ? 'pod' : ''}" title="${p.pod ? 'Задача дня' : ''}">${p.id}</button>`).join(''); }
const capOf = m => (m.cap && m.flag !== 'e' ? m.cap : '');
function solved(r) {
  done = true; board.render(); board.animateLast(capOf(r.m)); msg('✅ Верно! Мат.'); Sfx.play('win');
  list[cur].solved = true; renderList();
  attempt(true);
}
// первая попытка по задаче идёт в рейтинг задач (сервер сам учитывает только первую)
function attempt(ok) {
  const p = list[cur];
  if (!auth || (p.tried && !ok)) return;
  p.tried = true;
  fetch('/api/puzzle-attempt', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify({ id: p.id, ok }) })
    .then(r => r.json()).then(d => {
      if (d.rating == null) return;
      const el = $('#prating'); if (el) el.textContent = d.rating;
      msg($('#status').textContent + ` (рейтинг задач ${d.delta >= 0 ? '+' : ''}${d.delta})`);
    }).catch(() => {});
}
function onMove(from, to, promo) {
  const p = list[cur], r = g.move(from, to, promo); if (!r) return;
  board.last = [from, to];
  if (mateNow(g)) { solved(r); return; }
  if (p.mate === 2 && step === 0 && forcedMate1(g)) {
    busy = true; board.render(); board.animateLast(capOf(r.m)); Sfx.san(r.san); msg('Хорошо! Теперь поставь мат.');
    setTimeout(() => {
      const l = g.legal(), m = l.find(x => uci(x) === p.sol[1]) || l[0];
      const rr = g.move(m.from, m.to, m.promo); board.last = [m.from, m.to]; step = 1; busy = false; board.render(); board.animateLast(capOf(rr.m)); Sfx.san(rr.san);
    }, 650);
    return;
  }
  if (!failed) { failed = true; attempt(false); }
  g.undo(); board.last = null; board.dropped = false; msg('❌ Неверно, попробуй ещё'); Sfx.play('wrong'); board.render();
  board.el.animate([{ transform: 'none' }, { transform: 'translateX(-9px)' }, { transform: 'translateX(9px)' }, { transform: 'translateX(-5px)' }, { transform: 'none' }], { duration: 320 });
}
$('#plist').addEventListener('click', e => { const b = e.target.closest('button'); if (b) load(+b.dataset.i); });
$('#next').onclick = () => load((cur + 1) % list.length);
$('#reset').onclick = () => load(cur);
$('#hint').onclick = () => {
  if (done || busy) return;
  if (!failed) { failed = true; attempt(false); }   // подсказка = задача не решена с первой попытки
  if (step === 0) { board.hint = sqi(list[cur].sol[0].slice(0, 2)); board.render(); } else msg('Ищи мат в один ход');
};
if ($('#podbtn')) $('#podbtn').onclick = () => { const i = list.findIndex(p => p.pod); if (i >= 0) load(i); };
fetch('/api/puzzles').then(r => r.json()).then(d => {
  list = d;
  const want = +new URLSearchParams(location.search).get('id');
  let f = want ? d.findIndex(p => p.id === want) : -1;
  if (f < 0) f = d.findIndex(p => !p.solved);
  load(f < 0 ? 0 : f);
});
})();
