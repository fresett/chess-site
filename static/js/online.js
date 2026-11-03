(function () {
'use strict';
// Онлайн-игра: поиск соперника, ходы через сервер (опрос раз в секунду), часы считает сервер.
const { Game, sqi, sqn } = Chess;
const $ = s => document.querySelector(s);
const csrf = document.querySelector('meta[name=csrf]').content;
const stage = $('.stage'), meName = stage.dataset.me;
const api = (url, body) => fetch(url, body === undefined ? {} : {
  method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body),
}).then(r => r.json().catch(() => ({ error: 'Ошибка сервера' })));
const REASON = { checkmate: 'мат', stalemate: 'пат', material: 'недостаточно материала', fifty: 'правило 50 ходов',
  repetition: 'троекратное повторение', resign: 'сдача', timeout: 'время вышло', 'timeout-material': 'время вышло, но у соперника не хватает материала',
  agreement: 'по соглашению', aborted: 'партия отменена' };

let tc = 300, gid = null, st = null, you = null, searching = false, pollT = null, seekT = null, clockT = null, seekStart = 0, sending = false, shownOver = false;
const g = new Game();
const board = new Board($('#board'), g, { canMove: () => !!st && st.status === 'active' && !sending && g.turn === you, onMove });
const capOf = m => (m.cap && m.flag !== 'e' ? m.cap : '');
const uciOf = m => sqn(m.from) + sqn(m.to) + (m.promo || '');

/* ---------- статистика «на сайте» ---------- */
function stats() {
  fetch('/api/online/status').then(r => r.json()).then(d => {
    $('#sOnline').textContent = d.online; $('#sSearch').textContent = d.searching; $('#sPlay').textContent = d.playing;
    if (d.game && !gid) openGame(d.game);           // после перезагрузки страницы возвращаемся в свою партию
  }).catch(() => {});
}
stats(); setInterval(stats, 4000);

/* ---------- поиск соперника ---------- */
const tcs = $('#tcs');
if (tcs) tcs.addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  tcs.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b)); tc = +b.dataset.t;
});
function seekTick() {
  api('/api/online/seek', { tc }).then(d => {
    if (!searching) return;
    if (d.game) { stopSeek(); Sfx.play('notify'); openGame(d.game); return; }
    if (d.error) { stopSeek(); alertMsg(d.error); }
  });
  const s = Math.floor((Date.now() - seekStart) / 1000);
  $('#waited').textContent = Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}
function startSeek() {
  searching = true; seekStart = Date.now();
  $('#seekbox').classList.add('hidden'); $('#searching').classList.remove('hidden');
  seekTick(); seekT = setInterval(seekTick, 1500);
}
function stopSeek(cancel) {
  searching = false; clearInterval(seekT); seekT = null;
  if (cancel) api('/api/online/seek/cancel', {});
  if ($('#seekbox')) { $('#seekbox').classList.remove('hidden'); $('#searching').classList.add('hidden'); }
}
if ($('#find')) $('#find').onclick = startSeek;
if ($('#cancel')) $('#cancel').onclick = () => stopSeek(true);
window.addEventListener('beforeunload', () => { if (searching) navigator.sendBeacon && api('/api/online/seek/cancel', {}); });
function alertMsg(t) { const s = $('#status'); if (s) s.textContent = t; }

/* ---------- партия ---------- */
function openGame(id) {
  gid = id; shownOver = false; st = null; g.load(Chess.START);
  $('#lobby').classList.add('hidden'); $('#side').classList.remove('hidden');
  $('#again').classList.add('hidden'); closeModal();
  poll(); clearInterval(pollT); pollT = setInterval(poll, 1000);
  clearInterval(clockT); clockT = setInterval(renderClocks, 200);
}
function poll() {
  if (!gid || sending) return;
  fetch('/api/online/game/' + gid).then(r => r.json()).then(d => { if (!d.error) apply(d); }).catch(() => {});
}
// применить состояние с сервера: доиграть недостающие ходы (с анимацией последнего)
function apply(d) {
  const first = !st;
  st = d; st.recv = Date.now();
  if (first) { you = d.you; board.flip = you === 'b'; }
  const have = g.stack.length;
  if (d.moves.length < have) { g.load(Chess.START); }       // рассинхрон — перестраиваем с нуля
  let last = null;
  for (let i = g.stack.length; i < d.moves.length; i++) {
    const u = d.moves[i], r = g.move(sqi(u.slice(0, 2)), sqi(u.slice(2, 4)), u[4]);
    if (!r) { g.load(Chess.START); return apply(d); }
    last = r;
  }
  if (last) {
    const m = last.m; board.last = [m.from, m.to]; board.render(); board.animateLast(capOf(m)); Sfx.san(last.san);
  } else if (first) { const t = g.stack[g.stack.length - 1]; board.last = t ? [t.m.from, t.m.to] : null; board.render(); }
  draw();
  if (d.status === 'over' && !shownOver) showOver();
}
function onMove(from, to, promo) {
  const r = g.move(from, to, promo); if (!r) return;
  board.last = [from, to]; board.render(); board.animateLast(capOf(r.m)); Sfx.san(r.san);
  sending = true; draw();
  api('/api/online/game/' + gid + '/move', { uci: uciOf(r.m), ply: g.stack.length - 1 }).then(d => {
    sending = false;
    const s = d.state || d;
    if (d.error) { g.load(Chess.START); st = null; alertMsg(d.error); }
    if (s && s.moves) apply(s);
  }).catch(() => { sending = false; g.undo(); board.render(); alertMsg('Нет связи с сервером — ход не отправлен'); });
}
function player(c) { return st[c === 'w' ? 'white' : 'black']; }
function who(el, c) {
  const p = player(c); el.textContent = '';
  const e = document.createElement('em'); e.className = 'ava em'; e.textContent = c === 'w' ? '♔' : '♚'; el.appendChild(e);
  el.appendChild(document.createTextNode(p.name + (p.guest ? ' (гость)' : p.rating != null ? ` (${p.rating})` : '') + (c === you ? ' — ты' : '')));
}
function draw() {
  if (!st) return;
  const topC = board.flip ? 'w' : 'b';
  who($('#top'), topC); who($('#bottom'), topC === 'w' ? 'b' : 'w');
  let h = ''; const san = sanList();
  for (let i = 0; i < san.length; i += 2) h += `<i>${i / 2 + 1}.</i><span>${san[i]}</span><span>${san[i + 1] || ''}</span>`;
  const m = $('#moves'); m.innerHTML = h; m.scrollTop = m.scrollHeight;
  const live = st.status === 'active';
  $('#resign').classList.toggle('hidden', !live); $('#draw').classList.toggle('hidden', !live);
  $('#again').classList.toggle('hidden', live);
  // предложение ничьей от соперника
  $('#offer').classList.toggle('hidden', !(live && st.draw_offer && st.draw_offer !== you));
  $('#draw').disabled = !!(st.draw_offer); $('#draw').textContent = st.draw_offer === you ? '🤝 Предложено…' : '🤝 Ничья';
  if (live) $('#status').textContent = sending ? 'Отправляем ход…' : g.turn === you ? 'Твой ход' : 'Ход соперника';
  renderClocks();
}
function sanList() {   // нотация ходов: проигрываем партию на отдельной доске
  const t = new Game(), out = [];
  for (const u of st.moves) { const r = t.move(sqi(u.slice(0, 2)), sqi(u.slice(2, 4)), u[4]); if (!r) break; out.push(r.san); }
  return out;
}
function fmt(ms) {
  ms = Math.max(0, ms);
  if (ms < 10000) return '0:' + (ms / 1000).toFixed(1).padStart(4, '0');
  const s = Math.ceil(ms / 1000); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}
function renderClocks() {
  if (!st) return;
  const live = st.status === 'active', turn = st.moves.length % 2 ? 'b' : 'w', el = Date.now() - st.recv;
  const t = { w: st.clock.w, b: st.clock.b };
  if (live && st.moves.length) t[turn] -= el;           // до первого хода белых часы стоят
  const topC = board.flip ? 'w' : 'b';
  [['#ctop', topC], ['#cbot', topC === 'w' ? 'b' : 'w']].forEach(([id, c]) => {
    const e = $(id); e.classList.remove('hidden'); e.textContent = fmt(t[c]);
    e.classList.toggle('on', live && turn === c); e.classList.toggle('low', t[c] < 10000);
  });
}
function showOver() {
  shownOver = true; clearInterval(pollT); pollT = null;
  const r = st.result, aborted = r === 'aborted';
  let res = 'draw', title = 'Ничья';
  if (aborted) title = 'Партия отменена';
  else if (r === '1-0' || r === '0-1') { const win = (r === '1-0') === (you === 'w'); res = win ? 'win' : 'loss'; title = win ? 'Победа!' : 'Поражение'; }
  const sub = aborted ? 'белые не сделали первый ход' : REASON[st.reason] || '';
  $('#status').textContent = title + (sub ? ' — ' + sub : '');
  draw();
  const dl = st.delta[you], mr = $('#mrating');
  if (st.rated && dl != null) {
    mr.className = 'mrating ' + (dl > 0 ? 'up' : dl < 0 ? 'down' : ''); mr.textContent = `Рейтинг: ${dl > 0 ? '+' : ''}${dl}`;
  } else { mr.className = 'mrating'; mr.textContent = aborted ? 'Рейтинг не изменился' : 'Партия без рейтинга (играл гость)'; }
  if (!aborted) setTimeout(() => Sfx.play(res), 300);
  setTimeout(() => {
    $('#mbox').className = 'mbox ' + res; $('#mtitle').textContent = title; $('#msub').textContent = sub;
    $('#modal').classList.remove('hidden');
  }, aborted ? 100 : 800);
}
function closeModal() { $('#modal').classList.add('hidden'); }
function backToLobby() {
  closeModal(); gid = null; st = null; clearInterval(pollT); clearInterval(clockT);
  g.load(Chess.START); board.last = null; board.flip = false; board.render();
  $('#side').classList.add('hidden'); $('#lobby').classList.remove('hidden');
  ['#ctop', '#cbot'].forEach(id => $(id).classList.add('hidden')); $('#top').textContent = ''; $('#bottom').textContent = '';
  startSeek();
}
$('#mnew').onclick = $('#again').onclick = backToLobby;
$('#mview').onclick = $('#mclose').onclick = closeModal;
$('#flip').onclick = () => { board.flip = !board.flip; board.render(); draw(); };
$('#draw').onclick = () => { if (gid) api('/api/online/game/' + gid + '/draw', { action: 'offer' }).then(d => d.moves && apply(d)); };
$('#oyes').onclick = () => api('/api/online/game/' + gid + '/draw', { action: 'accept' }).then(d => d.moves && apply(d));
$('#ono').onclick = () => api('/api/online/game/' + gid + '/draw', { action: 'decline' }).then(d => d.moves && apply(d));
const closeConfirm = () => $('#confirm').classList.add('hidden');
$('#resign').onclick = () => {
  $('#ctitle').textContent = st && st.moves.length ? 'Партия будет засчитана как поражение' : 'Ходов ещё не было — партия будет отменена';
  $('#confirm').classList.remove('hidden');
};
$('#cno').onclick = closeConfirm;
$('#cyes').onclick = () => { closeConfirm(); api('/api/online/game/' + gid + '/resign', {}).then(d => d.moves && apply(d)); };
$('#confirm').addEventListener('click', e => { if (e.target.id === 'confirm') closeConfirm(); });

// имена до начала партии
if (meName) { $('#bottom').textContent = '👤 ' + meName; $('#top').textContent = 'Соперник'; }
board.render();
})();
