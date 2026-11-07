(function () {
'use strict';
// Тренировка дебютов: просмотр линии ход за ходом и режим «Тренироваться», где ты играешь свою сторону.
const { Game, sqi } = Chess;
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const capOf = m => (m.cap && m.flag !== 'e' ? m.cap : '');
const parse = u => ({ from: sqi(u.slice(0, 2)), to: sqi(u.slice(2, 4)), promo: u[4] });

let data = null, side = 'w', tab = 'top', cur = null, ply = 0, autoT = null;
let practice = false, busy = false, fails = 0;
const g = new Game();
const board = new Board($('#board'), g, { canMove: () => practice && !busy && ply < cur.line.length && g.turn === side, onMove });

/* ---------- список дебютов ---------- */
function families() {
  const map = new Map();
  data.all.forEach((o, i) => {
    const fam = o[1].split(':')[0];
    const f = map.get(fam);
    if (!f) map.set(fam, { fam, name: o[2] ? o[2].split(':')[0] : fam, eco: o[0], n: 1, len: o[3].split(' ').length });
    else { f.n++; }
  });
  return [...map.values()].sort((a, b) => b.n - a.n);
}
let famCache = null, famFilter = '';
function renderList() {
  const box = $('#olist');
  $('#q').classList.toggle('hidden', tab !== 'all');
  if (tab === 'top') {
    box.innerHTML = data.top[side].map((o, i) =>
      `<button data-top="${i}" class="${cur && cur.top === i && cur.side === side ? 'on' : ''}"><span class="num">${i + 1}</span><span class="nm">${esc(o.name)}</span></button>`).join('');
    return;
  }
  const q = $('#q').value.trim().toLowerCase();
  if (famFilter) {   // открыто семейство: показываем только его варианты
    const res = data.all.map((o, i) => i).filter(i => data.all[i][1] === famFilter || data.all[i][1].startsWith(famFilter + ':'));
    box.innerHTML = '<button class="back" data-back="1">← Все семейства</button>' + res.map(i => { const o = data.all[i]; return `<button data-all="${i}" class="${cur && cur.all === i ? 'on' : ''}"><span class="eco">${esc(o[0])}</span><span class="nm">${esc(o[2] || o[1])}</span></button>`; }).join('');
    return;
  }
  if (!q) {
    famCache = famCache || families();
    box.innerHTML = `<p class="muted hint">${data.all.length} лучших дебютов и вариантов. Выбери семейство или воспользуйся поиском.</p>` +
      famCache.map(f => `<button data-fam="${esc(f.fam)}"><span class="eco">${esc(f.eco)}</span><span class="nm">${esc(f.name)}</span><small>${f.n}</small></button>`).join('');
    return;
  }
  const res = [];
  for (let i = 0; i < data.all.length && res.length < 200; i++) {
    const o = data.all[i];
    if (o[0].toLowerCase() === q || o[1].toLowerCase().includes(q) || o[2].toLowerCase().includes(q)) res.push(i);
  }
  box.innerHTML = (res.length ? '' : '<p class="muted hint">Ничего не нашлось.</p>') +
    res.map(i => { const o = data.all[i]; return `<button data-all="${i}" class="${cur && cur.all === i ? 'on' : ''}"><span class="eco">${esc(o[0])}</span><span class="nm">${esc(o[2] || o[1])}${o[2] ? `<small class="en">${esc(o[1])}</small>` : ''}</span></button>`; }).join('') +
    (res.length === 200 ? '<p class="muted hint">Показаны первые 200 — уточни поиск.</p>' : '');
}
$('#olist').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.dataset.back) { famFilter = ''; $('#q').value = ''; renderList(); return; }
  if (b.dataset.fam) { famFilter = b.dataset.fam; $('#q').value = ''; renderList(); $('#olist').scrollTop = 0; return; }
  if (b.dataset.top) { const o = data.top[side][+b.dataset.top]; select({ top: +b.dataset.top, side, name: o.name, en: '', eco: '', note: o.note, uci: o.uci }); }
  if (b.dataset.all) { const o = data.all[+b.dataset.all]; select({ all: +b.dataset.all, name: o[2] || o[1], en: o[2] ? o[1] : '', eco: o[0], note: '', uci: o[3] }); }
  renderList();
});
$('#q').addEventListener('input', () => { famFilter = ''; renderList(); });
$('#tabs').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  tab = b.dataset.t; document.querySelectorAll('#tabs button').forEach(x => x.classList.toggle('on', x === b)); renderList();
});
$('#sides').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  side = b.dataset.s; document.querySelectorAll('#sides button').forEach(x => x.classList.toggle('on', x === b));
  stopPractice(); board.flip = side === 'b'; board.render(); renderList();
});

/* ---------- выбранный дебют ---------- */
function select(o) {
  stopAuto(); stopPractice();
  o.line = o.uci.split(' ').map(parse);
  // нотация ходов: проигрываем линию на отдельной доске
  const t = new Game(); o.san = o.line.map(m => t.move(m.from, m.to, m.promo).san);
  cur = o;
  $('#ocard').classList.remove('hidden');
  $('#oname').textContent = o.name; $('#oen').textContent = o.en;
  $('#oeco').textContent = o.eco; $('#oeco').classList.toggle('hidden', !o.eco);
  $('#onote').classList.toggle('hidden', !o.note); $('#onotet').textContent = o.note;
  $('#tstatus').textContent = '';
  goTo(0, false); goTo(o.line.length, true, true); // сразу показываем всю линию с анимацией по ходам
}
function renderMoves() {
  let h = '';
  cur.san.forEach((s, i) => {
    if (i % 2 === 0) h += `<i>${i / 2 + 1}.</i>`;
    h += `<span data-p="${i + 1}" class="${i + 1 === ply ? 'cur' : ''}${i >= ply ? ' fut' : ''}">${esc(s)}</span>`;
  });
  if (cur.san.length % 2) h += '<span></span>';
  $('#omoves').innerHTML = h;
}
$('#omoves').addEventListener('click', e => { const s = e.target.closest('[data-p]'); if (s && !practice) { stopAuto(); goTo(+s.dataset.p, true); } });

// перейти к полуходу n. Шаг на один ход вперёд/назад — с анимацией.
function goTo(n, animate, play) {
  n = Math.max(0, Math.min(cur.line.length, n));
  if (play) { autoplay(n); return; }
  if (animate && n === ply + 1) { stepForward(); return; }
  if (animate && n === ply - 1) { stepBack(); return; }
  while (ply > n) { g.undo(); ply--; }
  while (ply < n) { const m = cur.line[ply]; g.move(m.from, m.to, m.promo); ply++; }
  if (n === 0) g.load(Chess.START);
  board.last = ply ? [cur.line[ply - 1].from, cur.line[ply - 1].to] : null;
  board.sel = -1; board.targets = []; board.hint = -1; board.render(); renderMoves();
}
function stepForward() {
  if (ply >= cur.line.length) return false;
  const m = cur.line[ply], r = g.move(m.from, m.to, m.promo); ply++;
  board.last = [m.from, m.to]; board.render(); board.animateLast(capOf(r.m)); Sfx.san(r.san); renderMoves();
  return true;
}
function stepBack() {
  if (ply <= 0) return;
  const m = g.stack[g.stack.length - 1].m; g.undo(); ply--;
  board.last = ply ? [cur.line[ply - 1].from, cur.line[ply - 1].to] : null;
  board.render(); board.animateBack([m]); Sfx.play('move'); renderMoves();
}
/* скорость показа: сохраняется в браузере */
let speedMs = 1300;
try { speedMs = +localStorage.getItem('openingSpeed') || 1300; } catch (e) {}
function markSpeed() { document.querySelectorAll('#speed button').forEach(b => b.classList.toggle('on', +b.dataset.ms === speedMs)); }
$('#speed').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  speedMs = +b.dataset.ms; markSpeed();
  try { localStorage.setItem('openingSpeed', speedMs); } catch (err) {}
  if (autoT) { const t = autoTarget; stopAuto(); autoplay(t, true); }   // меняем скорость на лету
});
markSpeed();
let autoTarget = 0;
function autoplay(target, keep) {
  stopAuto(); if (!keep && ply >= target) goTo(0, false);
  autoTarget = target;
  $('#auto').textContent = '⏸ Пауза';
  autoT = setInterval(() => { if (ply >= target || !stepForward()) stopAuto(); }, speedMs);
}
function stopAuto() { if (autoT) clearInterval(autoT); autoT = null; $('#auto').textContent = '▶ Показать'; }
$('#first').onclick = () => { if (practice) return; stopAuto(); goTo(0); };
$('#last').onclick = () => { if (practice) return; stopAuto(); goTo(cur.line.length); };
$('#prev').onclick = () => { if (practice) return; stopAuto(); goTo(ply - 1, true); };
$('#next').onclick = () => { if (practice) return; stopAuto(); goTo(ply + 1, true); };
$('#auto').onclick = () => { if (practice) return; if (autoT) stopAuto(); else autoplay(cur.line.length); };
document.addEventListener('keydown', e => {
  if (!cur || practice || e.target.tagName === 'INPUT') return;
  if (e.key === 'ArrowRight') { stopAuto(); goTo(ply + 1, true); }
  if (e.key === 'ArrowLeft') { stopAuto(); goTo(ply - 1, true); }
});

/* ---------- режим «Тренироваться» ---------- */
const say = t => { $('#tstatus').textContent = t; };
function startPractice() {
  stopAuto(); practice = true; fails = 0; busy = false;
  document.querySelector('.training').classList.add('practicing');
  $('#practice').textContent = '✖ Закончить тренировку';
  board.flip = side === 'b'; goTo(0);
  say(side === 'w' ? 'Твой ход за белых. Играй ходы дебюта!' : 'Альберт играет белыми. Отвечай ходами дебюта!');
  replyIfNeeded();
}
function stopPractice() {
  practice = false; busy = false;
  const t = document.querySelector('.training'); if (t) t.classList.remove('practicing');
  $('#practice').textContent = '🎯 Тренироваться';
}
$('#practice').onclick = () => { if (practice) { stopPractice(); say(''); } else startPractice(); };
function replyIfNeeded() {
  if (!practice) return;
  if (ply >= cur.line.length) { done(); return; }
  if (g.turn === side) { renderMoves(); return; }
  busy = true;
  setTimeout(() => { if (!practice) return; stepForward(); busy = false; replyIfNeeded(); }, 550);
}
function onMove(from, to, promo) {
  const exp = cur.line[ply];
  const r = g.move(from, to, promo); if (!r) return;
  if (exp.from === from && exp.to === to && (exp.promo || '') === (r.m.promo || '')) {
    ply++; fails = 0; board.hint = -1;
    board.last = [from, to]; board.render(); board.animateLast(capOf(r.m)); Sfx.san(r.san); renderMoves();
    say('✅ Верно!'); replyIfNeeded();
    return;
  }
  g.undo(); fails++; board.render(); Sfx.play('wrong');
  board.el.animate([{ transform: 'none' }, { transform: 'translateX(-9px)' }, { transform: 'translateX(9px)' }, { transform: 'translateX(-5px)' }, { transform: 'none' }], { duration: 320 });
  if (fails >= 2) { board.hint = exp.from; board.render(); say('Подсказка: ходи фигурой с подсвеченной клетки.'); }
  else say('❌ Ход возможен, но в этом дебюте играют иначе. Попробуй ещё!');
}
function done() {
  practice = false; busy = false;
  document.querySelector('.training').classList.remove('practicing');
  $('#practice').textContent = '🔁 Ещё раз';
  say('🏆 Отлично! Ты сыграл весь дебют. Альберт доволен.'); Sfx.play('win');
}

fetch('/static/openings.json').then(r => r.json()).then(d => {
  data = d; renderList();
  const o = data.top.w[0];
  select({ top: 0, side: 'w', name: o.name, en: '', eco: '', note: o.note, uci: o.uci });
  renderList();
});
board.render();
})();
