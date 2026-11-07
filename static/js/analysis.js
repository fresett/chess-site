(function () {
'use strict';
// Доска анализа и просмотр партий: перемотка, варианты, движок, расстановка, импорт/экспорт PGN и FEN.
const { Game, sqi, sqn, START, isW } = Chess;
const $ = s => document.querySelector(s);
const stage = $('#astage'), VIEW = stage.dataset.view === '1';
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
let startFen = START, line = [], orig = null, ply = 0, editing = false, brush = 'P', engineOn = false, evalSeq = 0;
let g = new Game();
const board = new Board($('#board'), g, {
  canMove: () => !editing,
  onMove,
  editor: () => editing,
});

/* ---------- позиция и ходы ---------- */
function gameAt(n) {
  const t = new Game(startFen);
  for (let i = 0; i < n && i < line.length; i++) {
    const u = line[i]; if (!t.move(sqi(u.slice(0, 2)), sqi(u.slice(2, 4)), u[4])) break;
  }
  return t;
}
function sans() {
  const t = new Game(startFen), out = [];
  for (const u of line) { const r = t.move(sqi(u.slice(0, 2)), sqi(u.slice(2, 4)), u[4]); if (!r) break; out.push(r.san); }
  return out;
}
const uciOf = m => sqn(m.from) + sqn(m.to) + (m.promo || '');
function go(n, animate) {
  ply = Math.max(0, Math.min(line.length, n));
  g = gameAt(ply); board.g = g; board.sel = -1; board.targets = [];
  const t = g.stack[g.stack.length - 1]; board.last = t ? [t.m.from, t.m.to] : null;
  board.render(); if (animate && t) board.animateLast(t.m.cap && t.m.flag !== 'e' ? t.m.cap : '');
  draw(); evaluate();
}
function onMove(from, to, promo) {
  const r = g.move(from, to, promo); if (!r) return;
  const u = uciOf(r.m);
  if (line[ply] !== u) {             // новый вариант: всё, что было после, отбрасываем (партию можно вернуть)
    if (!orig && line.length && !VIEW) orig = { fen: startFen, line: line.slice() };
    line = line.slice(0, ply).concat([u]);
  }
  if (window.Sfx) Sfx.san(r.san);
  go(ply + 1, true);
}
function draw() {
  const s = sans(); let h = '';
  const off = new Game(startFen).turn === 'b' ? 1 : 0, full0 = new Game(startFen).full;
  if (off && s.length) h += `<i>${full0}.</i><span></span>`;
  for (let i = 0; i < s.length; i++) {
    const k = i + off;
    if (k % 2 === 0) h += `<i>${full0 + k / 2}.</i>`;
    h += `<span data-p="${i + 1}" class="${ply === i + 1 ? 'cur' : ''}">${esc(s[i])}</span>`;
  }
  const mv = $('#moves'); mv.innerHTML = h || '<p class="muted">Ходов нет — сделай ход на доске</p>';
  const cur = mv.querySelector('.cur'); if (cur) cur.scrollIntoView({ block: 'nearest' });
  const st = g.status(), turn = g.turn === 'w' ? 'белых' : 'чёрных';
  $('#status').textContent = st === 'checkmate' ? `Мат! Победили ${g.turn === 'w' ? 'чёрные' : 'белые'}` : st === 'stalemate' ? 'Пат — ничья'
    : st ? 'Ничья: ' + ({ material: 'недостаточно материала', fifty: 'правило 50 ходов', repetition: 'троекратное повторение' }[st] || '')
    : `Ход ${turn}${g.inCheck() ? ' — шах!' : ''}`;
  if ($('#restore')) $('#restore').classList.toggle('hidden', !orig);
  if ($('#fen') && document.activeElement !== $('#fen')) $('#fen').value = g.fen();
  const names = $('#names');
  if (names) {
    const top = board.flip ? 'white' : 'black', bot = board.flip ? 'black' : 'white';
    $('#top').textContent = (top === 'white' ? '♔ ' : '♚ ') + names.dataset[top];
    $('#bottom').textContent = (bot === 'white' ? '♔ ' : '♚ ') + names.dataset[bot];
  }
}
$('#moves').addEventListener('click', e => { const s = e.target.closest('span[data-p]'); if (s) go(+s.dataset.p); });
$('#navrow').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  go({ first: 0, prev: ply - 1, next: ply + 1, last: line.length }[b.dataset.n], b.dataset.n === 'next');
});
document.addEventListener('keydown', e => {
  if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
  if (e.key === 'ArrowLeft') go(ply - 1); else if (e.key === 'ArrowRight') go(ply + 1, true);
  else if (e.key === 'ArrowUp' || e.key === 'Home') go(0); else if (e.key === 'ArrowDown' || e.key === 'End') go(line.length);
  else if (e.key === 'f') { board.flip = !board.flip; board.render(); draw(); } else return;
  e.preventDefault();
});
$('#flip').onclick = () => { board.flip = !board.flip; board.render(); draw(); };
if ($('#restore')) $('#restore').onclick = () => { if (!orig) return; startFen = orig.fen; line = orig.line; orig = null; go(line.length); };

/* ---------- движок (в фоновом потоке) ---------- */
let worker = null;
function evaluate() {
  if (!engineOn || editing) return;
  const seq = ++evalSeq, fen = g.fen();
  if (g.status()) { setEval(null, g.status() === 'checkmate' ? (g.turn === 'w' ? -1e5 : 1e5) : 0); return; }
  $('#evaltext').textContent = 'считаю…';
  if (!worker) {
    try { worker = new Worker('/static/js/engine-worker.js'); } catch (e) { $('#evaltext').textContent = 'движок недоступен в этом браузере'; return; }
    worker.onmessage = ev => { if (ev.data.id === evalSeq) showEval(ev.data.res); };
  }
  worker.postMessage({ id: seq, cmd: 'best', fen, opts: { depth: 5, time: 1500 } });
}
function showEval(res) {
  if (!res || res.error) { $('#evaltext').textContent = 'нет оценки'; return; }
  const white = g.turn === 'w' ? res.s : -res.s;          // оценка с точки зрения белых
  setEval(res, white);
}
function setEval(res, white) {
  const mate = Math.abs(white) > 50000;
  const txt = mate ? (white > 0 ? 'мат у белых' : 'мат у чёрных') : (white >= 0 ? '+' : '') + (white / 100).toFixed(2);
  $('#evaltext').textContent = res ? `${txt} · лучший ход: ${res.san}` : txt;
  const pct = mate ? (white > 0 ? 100 : 0) : 50 + 50 * (2 / (1 + Math.exp(-white / 250)) - 1);
  $('#evalbar').style.width = Math.max(2, Math.min(98, pct)) + '%';
  board.shapes = board.shapes.filter(s => !s.engine);
  if (res) board.shapes.push({ from: res.from, to: res.to, c: 'b', engine: true });
  board.drawShapes();
}
$('#engine') && ($('#engine').onchange = e => {
  engineOn = e.target.checked;
  if (!engineOn) { $('#evaltext').textContent = 'выключен'; $('#evalbar').style.width = '50%'; board.shapes = board.shapes.filter(s => !s.engine); board.drawShapes(); evalSeq++; }
  else evaluate();
});

/* ---------- вкладки ---------- */
const tabs = $('#atabs');
if (tabs) tabs.addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  tabs.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
  ['moves', 'setup', 'io'].forEach(k => $('#t-' + k).classList.toggle('hidden', k !== b.dataset.t));
  setEditing(b.dataset.t === 'setup');
});

/* ---------- расстановка ---------- */
let setupTurn = 'w';
const PAL = ['K', 'Q', 'R', 'B', 'N', 'P', 'k', 'q', 'r', 'b', 'n', 'p'];
function setEditing(on) {
  if (!$('#palette')) return;
  editing = on; $('#palette').classList.toggle('hidden', !on);
  if (on) {
    g = new Game(g.fen()); board.g = g; board.last = null; board.sel = -1; board.targets = [];
    setupTurn = g.turn; markTurn(); renderPalette(); board.render();
  } else go(ply);
}
function renderPalette() {
  const p = $('#palette'); p.textContent = '';
  for (const c of PAL.concat(['x'])) {
    const b = document.createElement('button'); b.type = 'button'; b.dataset.p = c; b.className = c === brush ? 'on' : '';
    if (c === 'x') b.textContent = '🧽'; else { const i = document.createElement('img'); i.alt = c; i.src = Design.pieceSrc((isW(c) ? 'w' : 'b') + c.toLowerCase()); b.appendChild(i); }
    b.title = c === 'x' ? 'Стереть' : ''; p.appendChild(b);
  }
}
if ($('#palette')) {
  $('#palette').addEventListener('click', e => { const b = e.target.closest('button'); if (b) { brush = b.dataset.p; renderPalette(); } });
  $('#board').addEventListener('pointerdown', e => {
    if (!editing || e.button !== 0) return;
    const s = e.target.closest('.sq'); if (!s) return;
    const i = +s.dataset.i;
    g.b[i] = brush === 'x' || g.b[i] === brush ? '' : brush;
    board.render();
  });
  $('#side2move').addEventListener('click', e => { const b = e.target.closest('button'); if (b) { setupTurn = b.dataset.c; markTurn(); } });
  $('#sstart').onclick = () => { g.load(START); board.render(); setupTurn = 'w'; markTurn(); };
  $('#sclear').onclick = () => { g.b = Array(64).fill(''); board.render(); };
  $('#sdone').onclick = () => {
    const fen = setupFen(), err = validate(fen);
    if (err) { $('#setupmsg').textContent = '⚠ ' + err; return; }
    $('#setupmsg').textContent = '';
    loadPosition(fen, []);
    tabs.querySelector('[data-t=moves]').click();
  };
}
function markTurn() { document.querySelectorAll('#side2move button').forEach(x => x.classList.toggle('on', x.dataset.c === setupTurn)); }
function setupFen() {
  const b = g.b;
  let cas = '';
  if (b[60] === 'K' && b[63] === 'R') cas += 'K'; if (b[60] === 'K' && b[56] === 'R') cas += 'Q';
  if (b[4] === 'k' && b[7] === 'r') cas += 'k'; if (b[4] === 'k' && b[0] === 'r') cas += 'q';
  const t = new Game(START); t.b = b.slice(); t.turn = setupTurn; t.castle = cas; t.ep = -1; t.half = 0; t.full = 1;
  return t.fen();
}
function validate(fen) {
  const pos = fen.split(' ')[0];
  const wk = (pos.match(/K/g) || []).length, bk = (pos.match(/k/g) || []).length;
  if (wk !== 1 || bk !== 1) return 'На доске должно быть ровно по одному королю.';
  const ranks = pos.split('/');
  if (/[pP]/.test(ranks[0] + ranks[7])) return 'Пешки не могут стоять на первой и последней горизонтали.';
  const t = new Game(fen);
  t.turn = t.turn === 'w' ? 'b' : 'w';
  if (t.inCheck()) return 'Король стороны, которая не ходит, под шахом — такой позиции не бывает.';
  return null;
}
function loadPosition(fen, moves) {
  startFen = fen; line = moves; orig = null; editing = false;
  if ($('#palette')) $('#palette').classList.add('hidden');
  go(moves.length);
}

/* ---------- FEN и PGN ---------- */
const iomsg = t => { if ($('#iomsg')) $('#iomsg').textContent = t; };
if ($('#fenload')) $('#fenload').onclick = () => {
  const f = $('#fen').value.trim();
  if (!/^([pnbrqkPNBRQK1-8]+\/){7}[pnbrqkPNBRQK1-8]+ [wb] (-|[KQkq]+) (-|[a-h][36])( \d+ \d+)?$/.test(f)) { iomsg('⚠ Это не похоже на FEN'); return; }
  const err = validate(f); if (err) { iomsg('⚠ ' + err); return; }
  loadPosition(f, []); iomsg('✔ Позиция загружена');
};
if ($('#fencopy')) $('#fencopy').onclick = () => { const v = g.fen(); (navigator.clipboard ? navigator.clipboard.writeText(v) : Promise.reject()).then(() => iomsg('✔ FEN скопирован'), () => iomsg(v)); };
function parsePgn(text) {
  const head = {}; text.replace(/\[(\w+)\s+"([^"]*)"\]/g, (_, k, v) => { head[k] = v; return ''; });
  let body = text.replace(/\[[^\]]*\]/g, ' ').replace(/\{[^}]*\}/g, ' ').replace(/;[^\n]*/g, ' ');
  while (/\([^()]*\)/.test(body)) body = body.replace(/\([^()]*\)/g, ' ');       // варианты пропускаем
  const toks = body.replace(/\$\d+/g, ' ').replace(/\d+\.(\.\.)?/g, ' ').split(/\s+/).filter(x => x && !/^(1-0|0-1|1\/2-1\/2|\*)$/.test(x));
  const fen = head.FEN || START, t = new Game(fen), out = [];
  const norm = s => s.replace(/[+#!?]/g, '').replace(/^0-0-0$/, 'O-O-O').replace(/^0-0$/, 'O-O').replace(/=?([QRBN])$/, '=$1');
  for (const tok of toks) {
    const want = norm(tok), legal = t.legal();
    const m = legal.find(x => norm(t.san(x, legal)) === want) ||
      (/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(tok) && legal.find(x => uciOf(x) === tok));
    if (!m) return { error: `Не понял ход «${tok}» (ход ${Math.floor(out.length / 2) + 1})`, fen, moves: out };
    t.move(m.from, m.to, m.promo); out.push(uciOf(m));
  }
  return { fen, moves: out, head };
}
function importPgn(text) {
  const r = parsePgn(text);
  if (r.error && !r.moves.length) { iomsg('⚠ ' + r.error); return; }
  loadPosition(r.fen, r.moves);
  iomsg(r.error ? `⚠ ${r.error}. Загружено ${r.moves.length} полуходов.` : `✔ Загружено ${r.moves.length} полуходов`);
}
if ($('#pgnload')) $('#pgnload').onclick = () => importPgn($('#pgn').value);
if ($('#pgnfile')) $('#pgnfile').addEventListener('change', e => {
  const f = e.target.files[0]; e.target.value = ''; if (!f) return;
  if (f.size > 512 * 1024) { iomsg('⚠ Файл слишком большой'); return; }
  f.text().then(t => { $('#pgn').value = t; importPgn(t); });
});
function makePgn() {
  const s = sans(), t = new Game(startFen); let body = '', k = t.turn === 'b' ? 1 : 0;
  if (k && s.length) body += `${t.full}... `;
  s.forEach((x, i) => { if ((i + k) % 2 === 0) body += `${t.full + (i + k) / 2}. `; body += x + ' '; });
  const d = new Date(), date = `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}`;
  let head = `[Event "Анализ"]\n[Site "${location.host}"]\n[Date "${date}"]\n[White "?"]\n[Black "?"]\n[Result "*"]\n`;
  if (startFen !== START) head += `[SetUp "1"]\n[FEN "${startFen}"]\n`;
  return head + '\n' + body + '*\n';
}
if ($('#pgnsave')) $('#pgnsave').onclick = () => {
  const p = makePgn(); $('#pgn').value = p;
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([p], { type: 'application/x-chess-pgn' }));
  a.download = 'ladya-analysis.pgn'; a.setAttribute('download', 'ladya-analysis.pgn'); document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
};

/* ---------- старт ---------- */
let initial = [];
try { initial = JSON.parse($('#amoves').textContent || '[]'); } catch (e) { initial = []; }
const qfen = stage.dataset.fen;
if (qfen && !validate(qfen)) startFen = qfen;
line = Array.isArray(initial) ? initial : [];
go(VIEW ? 0 : line.length);
window.AN = { go, board, line: () => line, start: () => startFen };
})();
