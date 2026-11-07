(function () {
'use strict';
const { Game, sqi } = Chess;
const $ = s => document.querySelector(s);
const csrf = document.querySelector('meta[name=csrf]').content;
const auth = document.body.dataset.auth === '1';

const MV = $('#mapview');
const PRO = MV.dataset.pro === '1', FREE = +MV.dataset.free;
const CHAPTERS = ['Новичок', 'Любитель', 'Игрок клуба', 'Кандидат в мастера', 'Мастер'];
let list = [], cur = 0, tokenAt = 0, PATH_N = 0;   // list = дорожка (0…PATH_N-1) + задачи недели
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
/* ---------- путь задач (карта из кружков) ---------- */
const guestKey = 'psolved';
const isOpen = i => i >= PATH_N || i === 0 || list[i - 1].solved || list[i].solved;       // дорожка — строго по порядку
const proLocked = i => list[i].locked;
function currentIndex() { const i = list.findIndex((p, k) => k < PATH_N && !p.solved); return i < 0 ? PATH_N - 1 : i; }
// змейка: смещение кружка влево-вправо (доля от ширины дорожки)
const WAVE = [0, 0.42, 0.72, 0.42, 0, -0.42, -0.72, -0.42];

function renderMap() {
  const m = $('#pmap');
  let html = '<svg class="ppath" aria-hidden="true"><path class="pp-bg"/><path class="pp-done"/></svg>';
  list.slice(0, PATH_N).forEach((p, i) => {
    if (i % 10 === 0) {
      const ch = i / 10;
      const pro = i >= FREE && !PRO;
      html += `<div class="pchap${pro ? ' pro' : ''}"><small>Глава ${ch + 1}</small><b>${CHAPTERS[ch] || ''}</b>${pro ? '<span class="pro-badge">★ PRO</span>' : ''}</div>`;
    }
    const st = p.solved ? 'done' : proLocked(i) ? 'pro' : isOpen(i) ? 'open' : 'lock';
    const icon = p.solved ? '✓' : st === 'pro' ? '🔒' : st === 'lock' ? '' : p.id;
    html += `<div class="pnode" data-x="${WAVE[i % 8]}"><button type="button" data-i="${i}" class="pnc ${st}${p.pod ? ' pod' : ''}" title="${p.id}. ${p.title}" aria-label="Задача ${p.id}: ${p.title}">`
      + `<span>${icon}</span></button>`
      + `<small>${st === 'lock' || st === 'pro' ? '№' + p.id : p.icon + ' ' + p.title}</small></div>`;
  });
  html += '<div id="ptoken" class="ptoken" aria-hidden="true">♜</div>';
  m.innerHTML = html;
  renderWeekly();
  m.querySelectorAll('.pnode').forEach(n => n.style.setProperty('--x', n.dataset.x));   // CSP запрещает style="" в HTML
  renderStats();
  drawPath(); placeToken(tokenAt);
}

function centerOf(i) {
  const m = $('#pmap'), b = m.querySelector(`.pnc[data-i="${i}"]`);
  if (!b) return { x: 0, y: 0 };
  const mr = m.getBoundingClientRect(), r = b.getBoundingClientRect();
  return { x: r.left - mr.left + r.width / 2, y: r.top - mr.top + r.height / 2 };
}
function drawPath() {
  const m = $('#pmap'), svg = m.querySelector('.ppath');
  if (!svg || !list.length) return;
  svg.setAttribute('width', m.clientWidth); svg.setAttribute('height', m.scrollHeight);
  const pts = list.slice(0, PATH_N).map((_, i) => centerOf(i));
  const d = n => pts.slice(0, n).map((p, k) => {
    if (!k) return `M${p.x},${p.y}`;
    const q = pts[k - 1], my = (q.y + p.y) / 2;
    return `C${q.x},${my} ${p.x},${my} ${p.x},${p.y}`;
  }).join(' ');
  svg.querySelector('.pp-bg').setAttribute('d', d(pts.length));
  svg.querySelector('.pp-done').setAttribute('d', d(Math.min(pts.length, currentIndex() + 1)));
}
const tr = p => `translate(${p.x}px, ${p.y}px)`;
function placeToken(i) { const t = $('#ptoken'); if (t) t.style.transform = tr(centerOf(i)); }
function scrollToNode(i) {
  const b = $(`#pmap .pnc[data-i="${i}"]`);
  if (b) b.scrollIntoView({ block: 'center', behavior: 'smooth' });
}
// ладья прыгает с кружка на кружок
function hopToken(from, to) {
  const t = $('#ptoken'); if (!t) return;
  const a = centerOf(from), b = centerOf(to);
  const mid = { x: (a.x + b.x) / 2, y: Math.min(a.y, b.y) - 50 };
  t.style.transform = tr(b);
  if (matchMedia('(prefers-reduced-motion: reduce)').matches || !t.animate) return;
  t.animate([
    { transform: tr(a) + ' scale(1)' },
    { transform: tr(mid) + ' scale(1.3) rotate(-8deg)', offset: 0.5 },
    { transform: tr(b) + ' scale(.85, 1.1)', offset: 0.85 },
    { transform: tr(b) + ' scale(1)' },
  ], { duration: 950, easing: 'cubic-bezier(.3,.7,.3,1)', delay: 450, fill: 'backwards' }).finished.then(() => {
    const c = $(`#pmap .pnc[data-i="${to}"]`);
    if (c && c.animate) c.animate([{ transform: 'scale(1)' }, { transform: 'scale(1.2)' }, { transform: 'scale(1)' }], { duration: 420 });
    if (window.Sfx) Sfx.play('move');
  }).catch(() => {});
}

function renderWeekly() {
  const w = list.slice(PATH_N);
  $('#pweekly').classList.toggle('hidden', !w.length);
  $('#pwlist').innerHTML = w.map((p, k) => `<button type="button" class="pwcard${p.solved ? ' done' : ''}${p.locked ? ' lock' : ''}" data-i="${PATH_N + k}">`
    + `<em>${p.locked ? '🔒' : p.solved ? '✓' : p.icon}</em><span><b>${k === 0 ? 'Новая: ' : ''}${p.title}</b><small>${p.themeName} · сложность ${p.rating}</small></span></button>`).join('');
}
function renderStats() {
  const path = list.slice(0, PATH_N), solved = path.filter(p => p.solved).length;
  $('#pbar').style.width = (100 * solved / PATH_N) + '%';
  $('#pcount').textContent = `Решено ${solved} из ${PATH_N}`;
  $('#pstreak').textContent = `🔥 Серия: ${streak}` + (best > streak ? ` · рекорд ${best}` : '');
  $('#pstreak').classList.toggle('hot', streak >= 3);
}

function showMap(animateFrom) {
  epoch++;
  $('#solveview').classList.add('hidden'); MV.classList.remove('hidden');
  try { history.replaceState(null, '', location.pathname); } catch (e) {}
  const to = currentIndex();
  const hop = animateFrom != null && animateFrom !== to;
  tokenAt = hop ? animateFrom : to;
  renderMap();
  scrollToNode(to);
  if (hop) { hopToken(animateFrom, to); tokenAt = to; }
}
function openPuzzle(i) {
  if (proLocked(i)) { $('#plnum').textContent = list[i].id; $('#prolock').classList.remove('hidden'); return; }
  if (!isOpen(i)) { msgLock(i); return; }
  MV.classList.add('hidden'); $('#solveview').classList.remove('hidden');
  load(i); scrollTo({ top: 0 });
}
function msgLock(i) {
  const b = $(`#pmap .pnc[data-i="${i}"]`);
  if (b && b.animate) b.animate([{ transform: 'translateX(0)' }, { transform: 'translateX(-6px)' }, { transform: 'translateX(6px)' }, { transform: 'translateX(0)' }], { duration: 300 });
  const t = $('#ptoken'); if (t && t.animate) t.animate([{ filter: 'none' }, { filter: 'drop-shadow(0 0 14px var(--accent))' }, { filter: 'none' }], { duration: 700 });
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
  $('#pnum').textContent = p.weekly ? 'Задача недели' : `Задача ${p.id} из ${PATH_N}`;
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
  board.render();
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
  if (window.Confetti) Confetti.burst(failed ? 40 : 90);
  if (!failed) setStreak(streak + 1);
  msg(failed ? '✅ Решено!' : '✅ Отлично! С первой попытки.', 'good');
  finishCard(p.kind === 'mate' ? 'Мат! Задача решена' : 'Задача решена', 'win');
  p.solved = true;
  if (!auth) { const s = store.get(guestKey, []); if (!s.includes(p.id)) { s.push(p.id); store.set(guestKey, s); } }
  attempt(true);
}

function setStreak(n) {
  streak = n; best = Math.max(best, n);
  store.set('pstreak', streak); store.set('pbest', best);
  renderStats();
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

/* ---------- кнопки ---------- */
$('#next').onclick = () => showMap(cur < PATH_N && list[cur].solved ? cur : null);   // на карту — ладья переезжает дальше
$('#back').onclick = () => showMap(null);
$('#pmap').addEventListener('click', e => { const b = e.target.closest('.pnc'); if (b) openPuzzle(+b.dataset.i); });
$('#pwlist').addEventListener('click', e => { const b = e.target.closest('.pwcard'); if (b) openPuzzle(+b.dataset.i); });
$('#plclose').onclick = () => $('#prolock').classList.add('hidden');
$('#prolock').addEventListener('click', e => { if (e.target.id === 'prolock') $('#prolock').classList.add('hidden'); });
addEventListener('resize', () => { if (!MV.classList.contains('hidden')) { drawPath(); placeToken(tokenAt); } });
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
if ($('#podbtn')) $('#podbtn').onclick = () => {
  const i = list.findIndex(p => p.pod);
  if (i < 0) return;
  MV.classList.add('hidden'); $('#solveview').classList.remove('hidden'); load(i); scrollTo({ top: 0 });
};

fetch('/api/puzzles').then(r => r.json()).then(d => {
  list = d.filter(p => !p.weekly).concat(d.filter(p => p.weekly)); PATH_N = d.filter(p => !p.weekly).length;
  d = list;
  if (!auth) { const s = store.get(guestKey, []); list.forEach(p => { if (s.includes(p.id) && !p.locked) p.solved = true; }); }
  const want = +new URLSearchParams(location.search).get('id');
  const f = want ? d.findIndex(p => p.id === want) : -1;
  if (f >= 0 && !list[f].locked && (isOpen(f) || list[f].pod)) {
    tokenAt = currentIndex(); MV.classList.add('hidden'); $('#solveview').classList.remove('hidden'); load(f);
  } else showMap(null);
});
})();
