(function () {
'use strict';
const { Game, sqi } = Chess;
const $ = s => document.querySelector(s);
const csrf = document.querySelector('meta[name=csrf]').content;
const auth = document.body.dataset.auth === '1';

const FILTERS = { all: 'Все', mate: 'Маты', tactic: 'Тактика', opening: 'Ловушки', endgame: 'Эндшпиль', todo: 'Нерешённые' };
let list = [], cur = 0, filter = 'all';
let g, board, step = 0, done = false, busy = false, failed = false, hintLevel = 0, epoch = 0;

const uci = m => Chess.sqn(m.from) + Chess.sqn(m.to) + (m.promo || '');
const mateNow = x => !x.legal().length && x.inCheck();
const capOf = m => (m.cap && m.flag !== 'e' ? m.cap : '');
const msg = (t, cls) => { const s = $('#status'); s.textContent = t; s.className = 'status' + (cls ? ' ' + cls : ''); };
const store = {
  get(k, d) { try { const v = localStorage.getItem('ladya.' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('ladya.' + k, JSON.stringify(v)); } catch (e) {} },
};
let streak = store.get('pstreak', 0), best = store.get('pbest', 0);

function stars(r) {
  const n = r < 900 ? 1 : r < 1150 ? 2 : r < 1400 ? 3 : r < 1650 ? 4 : 5;
  return '★★★★★'.slice(0, n) + '☆☆☆☆☆'.slice(0, 5 - n);
}
function inFilter(p) {
  if (filter === 'all') return true;
  if (filter === 'todo') return !p.solved;
  return p.group === filter;
}

/* ---------- список и прогресс ---------- */
function renderList() {
  $('#plist').innerHTML = list.map((p, i) => inFilter(p) ? `<button data-i="${i}" class="${i === cur ? 'on' : ''}${p.solved ? ' ok' : p.tried ? ' bad' : ''}${p.pod ? ' pod' : ''}" title="${p.title}${p.pod ? ' — задача дня' : ''}"><span>${p.id}</span><em>${p.icon}</em></button>` : '').join('')
    || '<p class="muted">Здесь всё решено! 🎉</p>';
  const solved = list.filter(p => p.solved).length;
  $('#pbar').style.width = (100 * solved / list.length) + '%';
  $('#pcount').textContent = `Решено ${solved} из ${list.length}`;
  $('#pstreak').textContent = `🔥 Серия: ${streak}` + (best > streak ? ` · рекорд ${best}` : '');
  $('#pstreak').classList.toggle('hot', streak >= 3);
}
function renderFilters() {
  $('#pfilters').innerHTML = Object.entries(FILTERS).map(([k, v]) => `<button type="button" data-f="${k}" class="${k === filter ? 'on' : ''}">${v}</button>`).join('');
}

/* ---------- одна задача ---------- */
function load(i) {
  epoch++;
  cur = i; const p = list[i]; g = new Game(p.fen);
  step = 0; done = false; busy = false; failed = false; hintLevel = 0;
  try { history.replaceState(null, '', '?id=' + p.id); } catch (e) {}
  if (!board) board = new Board($('#board'), g, { canMove: () => !done && !busy, onMove });
  board.g = g; board.sel = -1; board.targets = []; board.last = null; board.hint = -1; board.pending = null; board.dropped = false;
  board.shapes = [];
  board.flip = g.turn === 'b';
  $('#pnum').textContent = `Задача ${p.id} из ${list.length}`;
  $('#ptitle').textContent = p.title;
  $('#pstars').textContent = stars(p.rating);
  $('#pstars').title = `Сложность ${p.rating}`;
  $('#ptheme').textContent = `${p.icon} ${p.themeName}`;
  const side = $('#pside');
  side.textContent = g.turn === 'w' ? 'Ход белых' : 'Ход чёрных';
  side.className = 'pill turn ' + (g.turn === 'w' ? 'tw' : 'tb');
  $('#pgoal').textContent = p.goal;
  $('#presult').classList.add('hidden');
  $('#hint').disabled = false; $('#show').disabled = false;
  msg('Твой ход');
  board.render(); renderList();
  const on = $('#plist .on'); if (on && on.scrollIntoView) on.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

function play(u) {   // сыграть ход в формате UCI (ответ соперника или показ решения)
  const m = g.legal().find(x => uci(x) === u);
  if (!m) return null;
  const r = g.move(m.from, m.to, m.promo);
  board.last = [m.from, m.to]; board.render(); board.animateLast(capOf(r.m)); Sfx.san(r.san);
  return r;
}

function opponentReply() {
  const p = list[cur], my = epoch;
  busy = true; msg('Верно! Соперник отвечает…', 'good busy');
  setTimeout(() => {
    if (my !== epoch) return;
    play(p.sol[step]); step++; busy = false;
    msg(p.kind === 'mate' && step === p.sol.length - 1 ? 'Последний ход — найди мат!' : 'Хорошо! Продолжай.', 'good');
  }, 650);
}

function onMove(from, to, promo) {
  const p = list[cur], r = g.move(from, to, promo); if (!r) return;
  const last = step === p.sol.length - 1;
  const ok = uci(r.m) === p.sol[step] || (p.kind === 'mate' && last && mateNow(g));
  if (!ok) { wrong(r); return; }
  board.last = [from, to]; board.hint = -1; board.render(); board.animateLast(capOf(r.m)); Sfx.san(r.san);
  step++;
  if (step >= p.sol.length) solved(); else opponentReply();
}

function wrong(r) {
  if (!failed) { failed = true; attempt(false); setStreak(0); }
  const mated = mateNow(g);
  g.undo(); board.last = null; board.dropped = false; board.render();
  msg(mated ? '❌ Это мат, но не тот — ищи другой путь' : '❌ Не то. Попробуй ещё раз', 'bad');
  Sfx.play('wrong');
  board.el.animate([{ transform: 'none' }, { transform: 'translateX(-9px)' }, { transform: 'translateX(9px)' }, { transform: 'translateX(-5px)' }, { transform: 'none' }], { duration: 320 });
}

function finishCard(title, cls) {
  const p = list[cur];
  done = true;
  $('#rtitle').textContent = title;
  $('#ridea').textContent = p.idea;
  $('#rline').textContent = 'Решение: ' + p.line;
  const box = $('#presult'); box.className = 'presult ' + cls;
  $('#hint').disabled = true; $('#show').disabled = true;
}

function solved() {
  const p = list[cur];
  Sfx.play('win');
  if (!failed) setStreak(streak + 1);
  msg(failed ? '✅ Решено!' : '✅ Отлично! С первой попытки.', 'good');
  finishCard(p.kind === 'mate' ? 'Мат! Задача решена' : 'Задача решена', 'win');
  p.solved = true; renderList();
  attempt(true);
}

function setStreak(n) {
  streak = n; best = Math.max(best, n);
  store.set('pstreak', streak); store.set('pbest', best);
  renderList();
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
      const s = $('#status'); s.textContent += ` (рейтинг задач ${d.delta >= 0 ? '+' : ''}${d.delta})`;
    }).catch(() => {});
}

function nextIndex() {
  for (let k = 1; k <= list.length; k++) {
    const i = (cur + k) % list.length;
    if (inFilter(list[i]) && !list[i].solved) return i;
  }
  for (let k = 1; k <= list.length; k++) { const i = (cur + k) % list.length; if (inFilter(list[i])) return i; }
  return (cur + 1) % list.length;
}

/* ---------- кнопки ---------- */
$('#plist').addEventListener('click', e => { const b = e.target.closest('button'); if (b) load(+b.dataset.i); });
$('#pfilters').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  filter = b.dataset.f; renderFilters(); renderList();
  if (!inFilter(list[cur])) { const i = list.findIndex(inFilter); if (i >= 0) load(i); }
});
$('#next').onclick = () => load(nextIndex());
$('#reset').onclick = () => load(cur);
$('#hint').onclick = () => {
  if (done || busy) return;
  const p = list[cur];
  if (!failed) { failed = true; attempt(false); setStreak(0); }   // подсказка = задача не решена с первой попытки
  hintLevel++;
  if (hintLevel === 1 && p.hint) { msg('💡 ' + p.hint, 'tip'); return; }
  board.hint = sqi(p.sol[step].slice(0, 2)); board.render();
  msg('💡 Ходи этой фигурой', 'tip');
};
$('#show').onclick = () => {
  if (done || busy) return;
  const p = list[cur], my = epoch;
  if (!failed) { failed = true; attempt(false); setStreak(0); }
  busy = true; msg('Смотри решение…', 'tip busy');
  const tick = () => {
    if (my !== epoch) return;
    if (step >= p.sol.length) { busy = false; msg('Вот как это решается. Попробуй следующую!', 'tip'); finishCard('Решение', 'show'); return; }
    play(p.sol[step]); step++; setTimeout(tick, 900);
  };
  tick();
};
if ($('#podbtn')) $('#podbtn').onclick = () => { const i = list.findIndex(p => p.pod); if (i >= 0) { filter = 'all'; renderFilters(); load(i); } };

fetch('/api/puzzles').then(r => r.json()).then(d => {
  list = d;
  renderFilters();
  const want = +new URLSearchParams(location.search).get('id');
  let f = want ? d.findIndex(p => p.id === want) : -1;
  if (f < 0) f = d.findIndex(p => !p.solved);
  load(f < 0 ? 0 : f);
});
})();
