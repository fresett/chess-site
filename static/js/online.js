(function () {
'use strict';
// Онлайн-игра: лобби (поиск соперника, вызовы, просмотр партий) и сама партия.
// Состояние партии браузер узнаёт опросом сервера раз в секунду (работает на любом хостинге, без WebSocket).
const { Game, sqi, sqn } = Chess;
const $ = s => document.querySelector(s);
const csrf = document.querySelector('meta[name=csrf]').content;
const stage = $('#ostage'), GID = stage.dataset.gid, meName = stage.dataset.me;
const api = (url, body) => fetch(url, body === undefined ? { cache: 'no-store' } : {
  method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body),
}).then(r => r.json().catch(() => ({ error: 'Ошибка сервера' })));
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const REASON = { checkmate: 'мат', stalemate: 'пат', material: 'недостаточно материала', fifty: 'правило 50 ходов',
  repetition: 'троекратное повторение', resign: 'сдача', timeout: 'время вышло', 'timeout-material': 'время вышло, но у соперника не хватает материала',
  agreement: 'по соглашению', aborted: 'партия отменена', left: 'соперник покинул партию', 'left-draw': 'соперник покинул партию — ничья',
  noshow: 'неявка — первый ход не сделан', arbiter: 'решение судьи (тренера)' };
const VAL = { p: 1, n: 3, b: 3, r: 5, q: 9 };
const g = new Game();
let st = null, you = null, ver = -1, pollT = null, clockT = null, sending = false, shownOver = false, viewPly = null, lastSync = 0;
const board = new Board($('#board'), g, {
  canMove: () => !!st && st.status === 'active' && !sending && viewPly === null && g.turn === you,
  onMove,
});
const capOf = m => (m.cap && m.flag !== 'e' ? m.cap : '');
const uciOf = m => sqn(m.from) + sqn(m.to) + (m.promo || '');
board.render();

/* ======================= ЛОББИ ======================= */
if (!GID) {
  let tc = '5+0', searching = false, seekT = null, seekStart = 0, chWatch = null;
  const tcs = $('#tcs');
  if (tcs) tcs.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    tcs.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b)); tc = b.dataset.tc;
  });
  // вкладки лобби
  const lt = $('#ltabs');
  if (lt) lt.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    lt.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
    ['quick', 'friend', 'watch'].forEach(k => $('#t-' + k).classList.toggle('hidden', k !== b.dataset.t));
  });
  if (new URLSearchParams(location.search).get('challenge') && lt) lt.querySelector('[data-t=friend]').click();

  function stats() {
    api('/api/online/status').then(d => {
      if (d.error) return;
      $('#sOnline').textContent = d.online; $('#sSearch').textContent = d.searching; $('#sPlay').textContent = d.playing;
      if (d.game) { location.href = '/online/game/' + d.game; return; }   // вернуться в свою партию
      const inc = $('#incoming');
      if (inc) {
        inc.classList.toggle('hidden', !d.challenges.length);
        inc.innerHTML = d.challenges.map(c => `<a class="chal" href="${esc(c.url)}"><b>⚔ ${esc(c.from)}</b> вызывает: ${esc(c.tc)}${c.rated ? ' · рейтинговая' : ''}<span class="btn">Открыть</span></a>`).join('');
      }
      const live = $('#live');
      if (live) live.innerHTML = d.games.length ? d.games.map(x => `<a class="lrow" href="/online/game/${esc(x.id)}"><span class="lic">👀</span><span class="grow">${esc(x.white)} — ${esc(x.black)}</span><small class="muted">${esc(x.tc)} · ход ${Math.floor(x.moves / 2) + 1}</small></a>`).join('')
        : '<p class="muted empty-state">Сейчас никто не играет. Начни первым!</p>';
    }).catch(() => {});
  }
  stats(); setInterval(stats, 3000);

  function seekTick() {
    api('/api/online/seek', { tc }).then(d => {
      if (!searching) return;
      if (d.game) { stopSeek(); Sfx.play('notify'); location.href = '/online/game/' + d.game; return; }
      if (d.error) { stopSeek(); alert(d.error); }
    }).catch(() => {});
    const s = Math.floor((Date.now() - seekStart) / 1000);
    $('#waited').textContent = Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }
  function startSeek() {
    searching = true; seekStart = Date.now(); $('#seektc').textContent = tc;
    $('#seekbox').classList.add('hidden'); $('#searching').classList.remove('hidden');
    seekTick(); seekT = setInterval(seekTick, 1500);
  }
  function stopSeek(cancel) {
    searching = false; clearInterval(seekT); seekT = null;
    if (cancel) api('/api/online/seek/cancel', {});
    $('#seekbox').classList.remove('hidden'); $('#searching').classList.add('hidden');
  }
  if ($('#find')) $('#find').onclick = startSeek;
  if ($('#cancel')) $('#cancel').onclick = () => stopSeek(true);
  window.addEventListener('pagehide', () => { if (searching) api('/api/online/seek/cancel', {}); });

  // вызов: по нику или ссылкой
  let chColor = 'random';
  const cc = $('#chcolor');
  if (cc) cc.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    cc.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b)); chColor = b.dataset.c;
  });
  const nick = $('#chnick');
  if (nick) {
    let t = null;
    nick.addEventListener('input', () => {
      clearTimeout(t); const q = nick.value.trim(); if (q.length < 2) return;
      t = setTimeout(() => api('/api/users?q=' + encodeURIComponent(q)).then(list => {
        if (!Array.isArray(list)) return;
        const dl = $('#nicklist'); dl.textContent = '';
        list.forEach(n => { const o = document.createElement('option'); o.value = n; dl.appendChild(o); });
      }), 250);
    });
  }
  const chf = $('#chform');
  if (chf) chf.addEventListener('submit', e => {
    e.preventDefault();
    const m = $('#chmsg'); m.textContent = 'Создаю вызов…';
    api('/api/online/challenge', { nick: nick && !nick.disabled ? nick.value.trim() : '', tc: $('#chtc').value, color: chColor, rated: $('#chrated') ? $('#chrated').checked : false })
      .then(d => {
        if (d.error) { m.textContent = '⚠ ' + d.error; return; }
        location.href = d.url;
      }).catch(() => { m.textContent = 'Нет связи с сервером'; });
  });
  if (meName) { $('#bottom').textContent = '👤 ' + meName; $('#top').textContent = 'Соперник'; }
  return;
}

/* ======================= ПАРТИЯ ======================= */
function poll() {
  if (sending) return;
  api('/api/online/game/' + GID + (st && st.status === 'active' ? '?v=' + ver : '')).then(d => {
    if (d.error) { $('#status').textContent = d.error; return; }
    lastSync = Date.now();
    if (d.same) {               // ничего не изменилось — только присутствие соперника
      st.white.online = d.white; st.black.online = d.black; st.oppGone = d.oppGone; st.canClaim = d.canClaim; st.startWait = d.startWait;
      gone(); return;
    }
    apply(d);
  }).catch(() => { $('#status').textContent = 'Нет связи с сервером… переподключаемся'; });
}
function schedule() {
  clearInterval(pollT);
  if (!st) { pollT = setInterval(poll, 1000); return; }
  // после конца партии опрашиваем реже — ждём ответа на реванш
  pollT = setInterval(poll, st.status === 'active' ? 1000 : 2500);
}
// применить состояние с сервера: доиграть недостающие ходы (с анимацией последнего)
function apply(d) {
  const first = !st, wasActive = st && st.status === 'active';
  st = d; st.recv = Date.now(); ver = d.ver;
  if (first) { you = d.you; board.flip = you === 'b'; schedule(); }
  if (wasActive && d.status !== 'active') schedule();
  if (d.rematch) { location.href = '/online/game/' + d.rematch; return; }
  let synced = g.stack.length <= d.moves.length;
  for (let i = 0; synced && i < g.stack.length; i++) if (uciOf(g.stack[i].m) !== d.moves[i]) synced = false;
  if (!synced) g.load(Chess.START);
  let last = null;
  for (let i = g.stack.length; i < d.moves.length; i++) {
    const u = d.moves[i], r = g.move(sqi(u.slice(0, 2)), sqi(u.slice(2, 4)), u[4]);
    if (!r) { g.load(Chess.START); break; }
    last = r;
  }
  if (viewPly === null) {
    if (last) {
      const m = last.m; board.last = [m.from, m.to]; board.render(); board.animateLast(capOf(m)); Sfx.san(last.san);
    } else { const t = g.stack[g.stack.length - 1]; board.last = t ? [t.m.from, t.m.to] : null; board.render(); }
  }
  draw();
  if (d.status === 'over' && !shownOver) showOver(!first);
}
function onMove(from, to, promo) {
  const r = g.move(from, to, promo); if (!r) return;
  board.last = [from, to]; board.render(); board.animateLast(capOf(r.m)); Sfx.san(r.san);
  sending = true; draw();
  api('/api/online/game/' + GID + '/move', { uci: uciOf(r.m), ply: g.stack.length - 1 }).then(d => {
    sending = false;
    const s = d.state || d;
    if (d.error) { $('#status').textContent = d.error; }
    if (s && s.moves) apply(s);
  }).catch(() => { sending = false; g.undo(); board.render(); $('#status').textContent = 'Нет связи с сервером — ход не отправлен'; });
}

/* ---------- отрисовка ---------- */
function player(c) { return st[c === 'w' ? 'white' : 'black']; }
function who(el, c) {
  const p = player(c); el.textContent = '';
  const e = document.createElement('em'); e.className = 'ava em'; e.textContent = c === 'w' ? '♔' : '♚'; el.appendChild(e);
  const dot = document.createElement('i'); dot.className = 'pres' + (p.online ? ' on' : ''); dot.title = p.online ? 'на связи' : 'не на связи'; el.appendChild(dot);
  const n = document.createElement(p.guest ? 'span' : 'a'); n.textContent = p.name; if (p.neon) n.className = 'neon neon-' + p.neon; if (!p.guest) n.href = '/u/' + encodeURIComponent(p.name); el.appendChild(n);
  if (p.pro) { const b = document.createElement('span'); b.className = 'pro-mini'; b.textContent = 'PRO'; el.appendChild(b); }
  const sm = document.createElement('small'); sm.className = 'muted';
  sm.textContent = (p.guest ? ' гость' : p.rating != null ? ` ${p.rating}` : '') + (c === you ? ' · ты' : ''); el.appendChild(sm);
}
// взятые фигуры и перевес в материале
function material(game) {
  const cnt = { w: { p: 0, n: 0, b: 0, r: 0, q: 0 }, b: { p: 0, n: 0, b: 0, r: 0, q: 0 } };
  for (const p of game.b) if (p && p.toLowerCase() !== 'k') cnt[p === p.toUpperCase() ? 'w' : 'b'][p.toLowerCase()]++;
  const start = { p: 8, n: 2, b: 2, r: 2, q: 1 }, out = { w: [], b: [] };
  let sc = 0;
  for (const t of 'qrbnp') {
    for (let i = 0; i < Math.max(0, start[t] - cnt.b[t]); i++) out.w.push('b' + t);   // белые взяли чёрные фигуры
    for (let i = 0; i < Math.max(0, start[t] - cnt.w[t]); i++) out.b.push('w' + t);
    sc += VAL[t] * (cnt.w[t] - cnt.b[t]);
  }
  return { cap: out, diff: sc };
}
function matEl(el, c, m) {
  el.textContent = '';
  for (const code of m.cap[c]) { const i = document.createElement('img'); i.src = Design.pieceSrc(code); i.alt = ''; el.appendChild(i); }
  const d = c === 'w' ? m.diff : -m.diff;
  if (d > 0) { const s = document.createElement('b'); s.textContent = '+' + d; el.appendChild(s); }
}
function viewGame() {
  if (viewPly === null) return g;
  const t = new Game();
  for (let i = 0; i < viewPly; i++) { const u = st.moves[i]; t.move(sqi(u.slice(0, 2)), sqi(u.slice(2, 4)), u[4]); }
  return t;
}
function draw() {
  if (!st) return;
  const topC = board.flip ? 'w' : 'b', botC = topC === 'w' ? 'b' : 'w';
  who($('#top'), topC); who($('#bottom'), botC);
  const m = material(viewGame()); matEl($('#mtop'), topC, m); matEl($('#mbot'), botC, m);
  let h = ''; const san = sanList(), cur = viewPly === null ? san.length : viewPly;
  for (let i = 0; i < san.length; i += 2) {
    h += `<i>${i / 2 + 1}.</i><span data-p="${i + 1}" class="${cur === i + 1 ? 'cur' : ''}">${esc(san[i])}</span>` +
      (san[i + 1] ? `<span data-p="${i + 2}" class="${cur === i + 2 ? 'cur' : ''}">${esc(san[i + 1])}</span>` : '<span></span>');
  }
  const mv = $('#moves'); mv.innerHTML = h || '<p class="muted">Ходов пока нет</p>'; if (viewPly === null) mv.scrollTop = mv.scrollHeight;
  const live = st.status === 'active', player_ = !!you;
  $('#liverow').classList.toggle('hidden', !live || !player_);
  $('#overrow').classList.toggle('hidden', live || !player_);
  $('#report').classList.toggle('hidden', !player_);
  const early = st.moves.length < 2;
  $('#resign').textContent = early && !st.tournament ? '✕ Отменить' : '🏳 Сдаться';
  $('#draw').disabled = early || !!st.draw_offer;
  $('#draw').textContent = st.draw_offer === you ? '🤝 Предложено…' : '🤝 Ничья';
  $('#offer').classList.toggle('hidden', !(live && st.draw_offer && st.draw_offer !== you));
  // реванш
  const ro = st.rematch_offer, canRematch = !live && player_ && !st.tournament;
  $('#rematch').classList.toggle('hidden', !canRematch || ro === 'x');
  $('#rematch').disabled = ro === you; $('#rematch').textContent = ro === you ? '🔁 Ждём ответа…' : '🔁 Реванш';
  $('#mrematch').classList.toggle('hidden', !canRematch || ro === 'x');
  // в окне итога: если соперник уже предложил реванш — кнопка сразу принимает его
  const theyOffer = canRematch && ro && ro !== you && ro !== 'x';
  $('#mrematch').textContent = theyOffer ? '✔ Принять реванш' : ro === you ? '🔁 Ждём ответа…' : '🔁 Реванш';
  $('#mrematch').disabled = ro === you;
  $('#roffer').classList.toggle('hidden', !(canRematch && ((ro && ro !== you) )));
  if (ro === 'x') { $('#rofftext').textContent = 'Реванш отклонён'; $('#rrow').classList.add('hidden'); }
  else { $('#rofftext').textContent = '🔁 Соперник предлагает реванш'; $('#rrow').classList.remove('hidden'); }
  // турнир
  const tb = $('#tban');
  if (st.tournament) {
    tb.classList.remove('hidden'); tb.textContent = '';
    const a = document.createElement('a'); a.href = st.tournament.url; a.textContent = `🏆 ${st.tournament.name} · тур ${st.tournament.round}`; tb.appendChild(a);
  }
  if (live) {
    let s;
    if (!player_) s = `👀 Наблюдаешь · ${g.turn === 'w' ? 'ход белых' : 'ход чёрных'}`;
    else if (sending) s = 'Отправляем ход…';
    else s = g.turn === you ? 'Твой ход' : 'Ход соперника';
    if (early && st.startWait != null) s += ` · на первый ход ${st.startWait} с`;
    $('#status').textContent = s + ` · ${st.tcLabel}${st.rated ? ' · рейтинговая' : ''}`;
  }
  gone();
  renderClocks();
}
function gone() {
  const box = $('#gone');
  const show = st.status === 'active' && st.oppGone > 0;
  box.classList.toggle('hidden', !show);
  if (show) {
    $('#gonetext').textContent = st.canClaim ? `📡 Соперник не на связи уже ${st.oppGone} с. Можно забрать победу или согласиться на ничью.`
      : `📡 Соперник не на связи (${st.oppGone} с). Ждём…`;
    $('#claimrow').classList.toggle('hidden', !st.canClaim);
  }
  if (st) { who($('#top'), board.flip ? 'w' : 'b'); who($('#bottom'), board.flip ? 'b' : 'w'); }
}
function sanList() {   // нотация ходов: проигрываем партию на отдельной доске
  const t = new Game(), out = [];
  for (const u of st.moves) { const r = t.move(sqi(u.slice(0, 2)), sqi(u.slice(2, 4)), u[4]); if (!r) break; out.push(r.san); }
  return out;
}
function fmt(ms) {
  ms = Math.max(0, ms);
  if (ms < 10000) return '0:' + (ms / 1000).toFixed(1).padStart(4, '0');
  const s = Math.ceil(ms / 1000), h = Math.floor(s / 3600);
  if (h) return h + ':' + String(Math.floor(s % 3600 / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}
function renderClocks() {
  if (!st) return;
  const live = st.status === 'active', turn = st.moves.length % 2 ? 'b' : 'w', el = Date.now() - st.recv;
  const t = { w: st.clock.w, b: st.clock.b };
  if (live && st.running) t[turn] -= el;           // часы идут после первого хода чёрных
  const topC = board.flip ? 'w' : 'b';
  [['#ctop', topC], ['#cbot', topC === 'w' ? 'b' : 'w']].forEach(([id, c]) => {
    const e = $(id); e.classList.remove('hidden'); e.textContent = fmt(t[c]);
    e.classList.toggle('on', live && turn === c); e.classList.toggle('low', t[c] < 10000);
  });
  // своё время кончилось — сразу спрашиваем сервер, не ждём следующего опроса
  if (live && st.running && t[turn] <= 0 && Date.now() - lastSync > 400) { lastSync = Date.now(); poll(); }
}
clockT = setInterval(renderClocks, 100);

function showOver(animate) {
  shownOver = true; board.sel = -1; board.targets = []; board.render();
  const r = st.result, aborted = r === 'aborted';
  let res = 'draw', title = 'Ничья';
  if (aborted) title = 'Партия отменена';
  else if (r === '1-0' || r === '0-1') {
    if (you) { const win = (r === '1-0') === (you === 'w'); res = win ? 'win' : 'loss'; title = win ? 'Победа!' : 'Поражение'; }
    else title = r === '1-0' ? 'Белые победили' : 'Чёрные победили';
  }
  const sub = REASON[st.reason] || '';
  $('#status').textContent = title + (sub ? ' — ' + sub : '');
  draw();
  if (!animate) return;              // открыли уже законченную партию — без окна и звуков
  if (res === 'win' && window.Confetti) setTimeout(() => Confetti.burst(), 500);
  const dl = you && st.delta[you], mr = $('#mrating');
  if (st.rated && dl != null) {
    mr.className = 'mrating ' + (dl > 0 ? 'up' : dl < 0 ? 'down' : ''); mr.textContent = `Рейтинг: ${dl > 0 ? '+' : ''}${dl}`;
  } else { mr.className = 'mrating'; mr.textContent = aborted ? 'Рейтинг не изменился' : st.rated ? 'Рейтинг не изменился' : 'Партия без рейтинга'; }
  if (!aborted && you) setTimeout(() => Sfx.play(res), 300);
  setTimeout(() => {
    $('#mbox').className = 'mbox ' + res; $('#mtitle').textContent = title; $('#msub').textContent = sub;
    $('#modal').classList.remove('hidden');
  }, aborted ? 100 : 800);
}
const closeModal = () => $('#modal').classList.add('hidden');
$('#mview').onclick = $('#mclose').onclick = closeModal;
$('#flip').onclick = () => { board.flip = !board.flip; board.render(); draw(); };
const act = (path, body) => api('/api/online/game/' + GID + '/' + path, body || {}).then(d => { const s = d.state || d; if (d.error) $('#status').textContent = d.error; if (s.moves) apply(s); });
$('#draw').onclick = () => act('draw', { action: 'offer' });
$('#oyes').onclick = () => act('draw', { action: 'accept' });
$('#ono').onclick = () => act('draw', { action: 'decline' });
$('#rematch').onclick = () => act('rematch', { action: 'offer' });
$('#mrematch').onclick = () => act('rematch', { action: 'offer' });     // окно не закрываем — там видно ответ
$('#ryes').onclick = () => act('rematch', { action: 'accept' });
$('#rno').onclick = () => act('rematch', { action: 'decline' });
$('#claimwin').onclick = () => act('claim', { action: 'win' });
$('#claimdraw').onclick = () => act('claim', { action: 'draw' });
const closeConfirm = () => $('#confirm').classList.add('hidden');
$('#resign').onclick = () => {
  const early = st && st.moves.length < 2 && !st.tournament;
  $('#cfh').textContent = early ? 'Отменить партию?' : 'Сдаться?';
  $('#ctitle').textContent = early ? 'Ходов ещё почти не было — партия отменится без рейтинга' : 'Партия будет засчитана как поражение';
  $('#cyes').textContent = early ? '✕ Отменить' : '🏳 Сдаться';
  $('#confirm').classList.remove('hidden');
};
$('#cno').onclick = closeConfirm;
$('#cyes').onclick = () => { closeConfirm(); act('resign'); };
$('#confirm').addEventListener('click', e => { if (e.target.id === 'confirm') closeConfirm(); });
// жалоба
$('#report').onclick = () => { $('#rtext').value = ''; $('#rmodal').classList.remove('hidden'); $('#rtext').focus(); };
$('#rcancel').onclick = () => $('#rmodal').classList.add('hidden');
$('#rsend').onclick = () => api('/api/online/game/' + GID + '/report', { reason: $('#rtext').value }).then(d => {
  if (d.error) { alert(d.error); return; }
  $('#rmodal').classList.add('hidden'); $('#status').textContent = '✔ Жалоба отправлена администратору';
});
// перемотка партии: клик по ходу, кнопки и стрелки клавиатуры
function goto(p) {
  if (!st) return;
  const n = st.moves.length;
  p = Math.max(0, Math.min(n, p));
  viewPly = p === n ? null : p;
  const vg = viewGame(); board.g = vg; board.sel = -1; board.targets = [];
  const t = vg.stack[vg.stack.length - 1]; board.last = t ? [t.m.from, t.m.to] : null;
  board.render(); draw();
}
$('#moves').addEventListener('click', e => { const s = e.target.closest('span[data-p]'); if (s) goto(+s.dataset.p); });
$('#navrow').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b || !st) return;
  const cur = viewPly === null ? st.moves.length : viewPly;
  goto({ first: 0, prev: cur - 1, next: cur + 1, last: st.moves.length }[b.dataset.n]);
});
document.addEventListener('keydown', e => {
  if (!st || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
  const cur = viewPly === null ? st.moves.length : viewPly;
  if (e.key === 'ArrowLeft') goto(cur - 1); else if (e.key === 'ArrowRight') goto(cur + 1);
  else if (e.key === 'ArrowUp') goto(0); else if (e.key === 'ArrowDown') goto(st.moves.length); else return;
  e.preventDefault();
});
poll(); schedule();
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
})();
