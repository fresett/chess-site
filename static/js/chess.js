(function (root) {
'use strict';
const START = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
const sqi = s => (8 - +s[1]) * 8 + (s.charCodeAt(0) - 97);
const sqn = i => String.fromCharCode(97 + (i & 7)) + (8 - (i >> 3));
const isW = p => p >= 'A' && p <= 'Z';
const N_OFF = [[-2,-1],[-2,1],[-1,-2],[-1,2],[1,-2],[1,2],[2,-1],[2,1]];
const K_OFF = [[-1,-1],[-1,0],[-1,1],[0,-1],[0,1],[1,-1],[1,0],[1,1]];
const B_DIR = [[-1,-1],[-1,1],[1,-1],[1,1]];
const R_DIR = [[-1,0],[1,0],[0,-1],[0,1]];
const VAL = { p: 100, n: 320, b: 330, r: 500, q: 900, k: 0 };
const MATE = 100000, INF = 1e9, TIMEOUT = { t: 1 };

class Game {
  constructor(fen) { this.load(fen || START); }
  load(fen) {
    const p = fen.trim().split(/\s+/);
    this.b = Array(64).fill('');
    let i = 0;
    for (const ch of p[0]) { if (ch === '/') continue; if (ch >= '1' && ch <= '8') i += +ch; else this.b[i++] = ch; }
    this.turn = p[1] || 'w';
    this.castle = p[2] && p[2] !== '-' ? p[2] : '';
    this.ep = p[3] && p[3] !== '-' ? sqi(p[3]) : -1;
    this.half = +p[4] || 0; this.full = +p[5] || 1;
    this.k = { w: this.b.indexOf('K'), b: this.b.indexOf('k') };
    this.stack = []; this.keys = [this.key()];
  }
  key() { return this.b.map(x => x || '.').join('') + this.turn + this.castle + this.ep; }
  fen() {
    let s = '';
    for (let r = 0; r < 8; r++) {
      let e = 0;
      for (let c = 0; c < 8; c++) { const p = this.b[r * 8 + c]; if (!p) e++; else { if (e) { s += e; e = 0; } s += p; } }
      if (e) s += e; if (r < 7) s += '/';
    }
    return `${s} ${this.turn} ${this.castle || '-'} ${this.ep < 0 ? '-' : sqn(this.ep)} ${this.half} ${this.full}`;
  }
  attacked(s, by) {
    const b = this.b, r = s >> 3, c = s & 7, w = by === 'w';
    const pr = w ? r + 1 : r - 1, pp = w ? 'P' : 'p';
    if (pr >= 0 && pr < 8) for (const dc of [-1, 1]) { const cc = c + dc; if (cc >= 0 && cc < 8 && b[pr * 8 + cc] === pp) return true; }
    const n = w ? 'N' : 'n', k = w ? 'K' : 'k';
    for (const [dr, dc] of N_OFF) { const rr = r + dr, cc = c + dc; if (rr >= 0 && rr < 8 && cc >= 0 && cc < 8 && b[rr * 8 + cc] === n) return true; }
    for (const [dr, dc] of K_OFF) { const rr = r + dr, cc = c + dc; if (rr >= 0 && rr < 8 && cc >= 0 && cc < 8 && b[rr * 8 + cc] === k) return true; }
    const sl = (dirs, set) => {
      for (const [dr, dc] of dirs) {
        let rr = r + dr, cc = c + dc;
        while (rr >= 0 && rr < 8 && cc >= 0 && cc < 8) { const p = b[rr * 8 + cc]; if (p) { if (set.includes(p)) return true; break; } rr += dr; cc += dc; }
      }
      return false;
    };
    return sl(B_DIR, w ? 'BQ' : 'bq') || sl(R_DIR, w ? 'RQ' : 'rq');
  }
  inCheck() { return this.attacked(this.k[this.turn], this.turn === 'w' ? 'b' : 'w'); }
  pseudo() {
    const out = [], b = this.b, w = this.turn === 'w', opp = w ? 'b' : 'w';
    for (let i = 0; i < 64; i++) {
      const p = b[i]; if (!p || isW(p) !== w) continue;
      const r = i >> 3, c = i & 7, t = p.toLowerCase();
      if (t === 'p') {
        const d = w ? -1 : 1, sr = w ? 6 : 1, pr = w ? 0 : 7, f = i + d * 8;
        const add = (to, cap) => { if ((to >> 3) === pr) for (const q of 'qrbn') out.push({ from: i, to, p, cap, promo: q }); else out.push({ from: i, to, p, cap }); };
        if (!b[f]) { add(f, ''); if (r === sr && !b[f + d * 8]) out.push({ from: i, to: f + d * 8, p, cap: '', flag: 'd' }); }
        for (const dc of [-1, 1]) {
          const cc = c + dc; if (cc < 0 || cc > 7) continue;
          const to = f + dc, tp = b[to];
          if (tp && isW(tp) !== w) add(to, tp);
          else if (to === this.ep) out.push({ from: i, to, p, cap: w ? 'p' : 'P', flag: 'e' });
        }
      } else if (t === 'n' || t === 'k') {
        for (const [dr, dc] of (t === 'n' ? N_OFF : K_OFF)) {
          const rr = r + dr, cc = c + dc; if (rr < 0 || rr > 7 || cc < 0 || cc > 7) continue;
          const tp = b[rr * 8 + cc]; if (!tp || isW(tp) !== w) out.push({ from: i, to: rr * 8 + cc, p, cap: tp });
        }
        if (t === 'k' && i === (w ? 60 : 4)) {
          const rk = w ? 'R' : 'r';
          if (this.castle.includes(w ? 'K' : 'k') && !b[i + 1] && !b[i + 2] && b[i + 3] === rk && !this.attacked(i, opp) && !this.attacked(i + 1, opp) && !this.attacked(i + 2, opp))
            out.push({ from: i, to: i + 2, p, cap: '', flag: 'c' });
          if (this.castle.includes(w ? 'Q' : 'q') && !b[i - 1] && !b[i - 2] && !b[i - 3] && b[i - 4] === rk && !this.attacked(i, opp) && !this.attacked(i - 1, opp) && !this.attacked(i - 2, opp))
            out.push({ from: i, to: i - 2, p, cap: '', flag: 'c' });
        }
      } else {
        for (const [dr, dc] of (t === 'b' ? B_DIR : t === 'r' ? R_DIR : B_DIR.concat(R_DIR))) {
          let rr = r + dr, cc = c + dc;
          while (rr >= 0 && rr < 8 && cc >= 0 && cc < 8) {
            const tp = b[rr * 8 + cc];
            if (!tp) out.push({ from: i, to: rr * 8 + cc, p, cap: '' });
            else { if (isW(tp) !== w) out.push({ from: i, to: rr * 8 + cc, p, cap: tp }); break; }
            rr += dr; cc += dc;
          }
        }
      }
    }
    return out;
  }
  make(m) {
    const b = this.b, w = this.turn === 'w', p = b[m.from];
    const u = { m, castle: this.castle, ep: this.ep, half: this.half, full: this.full, kw: this.k.w, kb: this.k.b, epcap: '' };
    b[m.from] = '';
    b[m.to] = m.promo ? (w ? m.promo.toUpperCase() : m.promo) : p;
    if (m.flag === 'e') { const cs = m.to + (w ? 8 : -8); u.epcap = b[cs]; b[cs] = ''; }
    else if (m.flag === 'c') { if (m.to > m.from) { b[m.to - 1] = b[m.to + 1]; b[m.to + 1] = ''; } else { b[m.to + 1] = b[m.to - 2]; b[m.to - 2] = ''; } }
    if (p === 'K') this.k.w = m.to; else if (p === 'k') this.k.b = m.to;
    let c = this.castle;
    if (p === 'K') c = c.replace(/[KQ]/g, ''); else if (p === 'k') c = c.replace(/[kq]/g, '');
    for (const s of [m.from, m.to]) { if (s === 63) c = c.replace('K', ''); else if (s === 56) c = c.replace('Q', ''); else if (s === 7) c = c.replace('k', ''); else if (s === 0) c = c.replace('q', ''); }
    this.castle = c;
    this.ep = m.flag === 'd' ? (m.from + m.to) >> 1 : -1;
    this.half = (p.toLowerCase() === 'p' || m.cap) ? 0 : this.half + 1;
    if (!w) this.full++;
    this.turn = w ? 'b' : 'w';
    this.stack.push(u);
  }
  unmake() {
    const u = this.stack.pop(), m = u.m, b = this.b;
    this.turn = this.turn === 'w' ? 'b' : 'w';
    const w = this.turn === 'w';
    this.castle = u.castle; this.ep = u.ep; this.half = u.half; this.full = u.full; this.k.w = u.kw; this.k.b = u.kb;
    b[m.from] = m.promo ? (w ? 'P' : 'p') : b[m.to];
    if (m.flag === 'e') { b[m.to] = ''; b[m.to + (w ? 8 : -8)] = u.epcap; } else b[m.to] = m.cap || '';
    if (m.flag === 'c') { if (m.to > m.from) { b[m.to + 1] = b[m.to - 1]; b[m.to - 1] = ''; } else { b[m.to - 2] = b[m.to + 1]; b[m.to + 1] = ''; } }
  }
  legal() {
    const out = [], me = this.turn, opp = me === 'w' ? 'b' : 'w';
    for (const m of this.pseudo()) { this.make(m); if (!this.attacked(this.k[me], opp)) out.push(m); this.unmake(); }
    return out;
  }
  san(m, legal) {
    let s;
    if (m.flag === 'c') s = m.to > m.from ? 'O-O' : 'O-O-O';
    else {
      const t = m.p.toUpperCase();
      if (t === 'P') s = (m.cap ? sqn(m.from)[0] + 'x' : '') + sqn(m.to) + (m.promo ? '=' + m.promo.toUpperCase() : '');
      else {
        // другие такие же фигуры, которые тоже могут пойти на эту клетку (сравниваем по клетке, а не по объекту хода)
        s = t; const amb = legal.filter(o => o.from !== m.from && o.p === m.p && o.to === m.to);
        if (amb.length) { if (!amb.some(o => (o.from & 7) === (m.from & 7))) s += sqn(m.from)[0]; else if (!amb.some(o => (o.from >> 3) === (m.from >> 3))) s += sqn(m.from)[1]; else s += sqn(m.from); }
        s += (m.cap ? 'x' : '') + sqn(m.to);
      }
    }
    this.make(m);
    if (this.inCheck()) s += this.legal().length ? '+' : '#';
    this.unmake();
    return s;
  }
  move(from, to, promo) {
    const l = this.legal();
    const m = l.find(x => x.from === from && x.to === to && (!x.promo || x.promo === (promo || 'q')));
    if (!m) return null;
    const san = this.san(m, l);
    this.make(m); this.keys.push(this.key());
    return { m, san };
  }
  undo() { this.unmake(); this.keys.pop(); }
  insufficient() {
    const o = this.b.filter(x => x && x.toLowerCase() !== 'k');
    return o.length === 0 || (o.length === 1 && 'nbNB'.includes(o[0]));
  }
  status() {
    if (!this.legal().length) return this.inCheck() ? 'checkmate' : 'stalemate';
    if (this.insufficient()) return 'material';
    if (this.half >= 100) return 'fifty';
    const k = this.keys[this.keys.length - 1];
    if (this.keys.filter(x => x === k).length >= 3) return 'repetition';
    return null;
  }
}

/* ---------- Bots ---------- */
function pst(p, i) {
  const t = p.toLowerCase(), w = isW(p), r = i >> 3, c = i & 7;
  const cen = 3.5 - Math.max(Math.abs(c - 3.5), Math.abs(r - 3.5));
  const adv = w ? 7 - r : r;
  if (t === 'p') return adv * 6 + (adv > 4 ? (adv - 4) * 8 : 0) + cen * 3;
  if (t === 'n') return cen * 12;
  if (t === 'b') return cen * 5;
  if (t === 'r') return adv === 6 ? 15 : 0;
  if (t === 'q') return cen * 2;
  return 0;
}
// стартовые клетки лёгких фигур: стоять там в миддлгейме — плохо (неразвитость)
const HOME_MINOR = { 57: 'N', 62: 'N', 58: 'B', 61: 'B', 1: 'n', 6: 'n', 2: 'b', 5: 'b' };
function evaluate(g) {
  let s = 0; const b = g.b, end = !b.includes('Q') && !b.includes('q');
  for (let i = 0; i < 64; i++) {
    const p = b[i]; if (!p) continue;
    const t = p.toLowerCase(); let v = VAL[t] + pst(p, i);
    if (t === 'k') {
      const r = i >> 3, c = i & 7, cen = 3.5 - Math.max(Math.abs(c - 3.5), Math.abs(r - 3.5)), home = isW(p) ? r === 7 : r === 0;
      if (end) v += cen * 10;
      else {
        v += home ? 20 : -cen * 8;
        // король спрятан на фланге (после рокировки) — бонус; застрял в центре — штраф
        if (home && (c <= 2 || c >= 6)) v += 30; else if (home && (c === 3 || c === 4)) v -= 15;
      }
    }
    if (!end && HOME_MINOR[i] === p) v -= 12;
    s += isW(p) ? v : -v;
  }
  return g.turn === 'w' ? s : -s;
}
const score = m => (m.cap ? 10 * VAL[m.cap.toLowerCase()] - VAL[m.p.toLowerCase()] / 10 : 0) + (m.promo === 'q' ? 800 : 0);
const order = l => l.sort((a, b) => score(b) - score(a));
function quies(g, a, b, ctx, q) {
  const st = evaluate(g);
  if (st >= b) return b; if (st > a) a = st; if (q >= 6) return a;
  const caps = order(g.pseudo().filter(m => m.cap || m.promo === 'q'));
  for (const m of caps) {
    g.make(m);
    const mover = g.turn === 'w' ? 'b' : 'w';
    if (g.attacked(g.k[mover], g.turn)) { g.unmake(); continue; }
    const s = -quies(g, -b, -a, ctx, q + 1); g.unmake();
    if (s >= b) return b; if (s > a) a = s;
  }
  return a;
}
function search(g, d, a, b, ctx, ext) {
  ext = ext || 0;
  if ((++ctx.n & 1023) === 0 && Date.now() > ctx.end) throw TIMEOUT;
  if (d <= 0) {
    // продление при шахе: не обрываем расчёт посреди атаки на короля (видим маты за жертвами)
    if (ext < 4 && g.inCheck()) { d = 1; ext++; } else return quies(g, a, b, ctx, 0);
  }
  const moves = order(g.legal());
  if (!moves.length) return g.inCheck() ? -MATE - d : 0;
  for (const m of moves) {
    g.make(m); const s = -search(g, d - 1, -b, -a, ctx, ext); g.unmake();
    if (s >= b) return b; if (s > a) a = s;
  }
  return a;
}
const BOTS = [
  { elo: 200,  name: 'Пешка Петя', depth: 1, noise: 400, rand: 0.6,  time: 300 },
  { elo: 600,  name: 'Мария',      depth: 1, noise: 150, rand: 0.12, time: 300 },
  { elo: 1000, name: 'Алексей',    depth: 2, noise: 60,  rand: 0.03, time: 800 },
  { elo: 1500, name: 'Дмитрий',    depth: 3, noise: 15,  rand: 0,    time: 1500 },
  { elo: 2000, name: 'Мастер',     depth: 6, noise: 0,   rand: 0,    time: 2000 },
];
function pickMove(g, cfg) {
  const legal = g.legal(); if (!legal.length) return null;
  if (Math.random() < cfg.rand) return legal[Math.floor(Math.random() * legal.length)];
  const base = g.stack.length, ctx = { n: 0, end: Date.now() + cfg.time };
  let best = null;
  try {
    for (let d = 1; d <= cfg.depth; d++) {
      let bm = null, a = -INF;
      const list = order(legal.slice()); if (best) list.sort((x, y) => (y === best) - (x === best));
      for (const m of list) {
        g.make(m);
        let s = cfg.noise ? -search(g, d - 1, -INF, INF, ctx) : -search(g, d - 1, -INF, -a, ctx);
        g.unmake();
        if (cfg.noise) s += cfg.noise * (Math.random() * 2 - 1);
        if (s > a) { a = s; bm = m; }
      }
      best = bm;
      if (a > MATE / 2) break;
    }
  } catch (e) { if (e !== TIMEOUT) throw e; while (g.stack.length > base) g.unmake(); }
  return best || legal[0];
}

// Оценка каждого хода в позиции (для тренера). Возвращает [{m, s}] от лучшего к худшему,
// s — оценка в сантипешках с точки зрения стороны, которая ходит. Глубина 1 считается всегда,
// дальше — пока хватает времени.
function analyze(g, cfg) {
  const legal = g.legal(); if (!legal.length) return [];
  const base = g.stack.length, ctx = { n: 0, end: Infinity };
  const run = d => {
    const cur = [];
    for (const m of order(legal.slice())) { g.make(m); const s = -search(g, d - 1, -INF, INF, ctx); g.unmake(); cur.push({ m, s }); }
    return cur.sort((a, b) => b.s - a.s);
  };
  let res = run(1);
  ctx.end = Date.now() + (cfg.time || 800);
  try { for (let d = 2; d <= (cfg.depth || 3); d++) res = run(d); }
  catch (e) { if (e !== TIMEOUT) throw e; while (g.stack.length > base) g.unmake(); }
  return res;
}

// Уровни тренера Альберта (сила, с которой он играет против тебя)
const COACH_LEVELS = [
  { elo: 600,  name: 'Тренер Альберт', depth: 1, noise: 120, rand: 0.08, time: 400 },
  { elo: 1200, name: 'Тренер Альберт', depth: 2, noise: 35,  rand: 0.02, time: 700 },
  { elo: 1800, name: 'Тренер Альберт', depth: 4, noise: 5,   rand: 0,    time: 1500 },
];
// Размен на клетке (SEE): сколько материала (в сантипешках) выигрывает сторона, которая ходит,
// если стороны по очереди бьют на клетке sq самой дешёвой фигурой и могут остановиться в любой момент.
function seeSquare(g, sq) {
  const caps = g.legal().filter(m => m.to === sq && m.cap && !m.promo);
  if (!caps.length) return 0;
  caps.sort((a, b) => VAL[a.p.toLowerCase()] - VAL[b.p.toLowerCase()]);
  const m = caps[0], v = VAL[m.cap.toLowerCase()];
  g.make(m); const r = v - seeSquare(g, sq); g.unmake();
  return Math.max(0, r);
}
// чистый выигрыш материала от взятия m с учётом ответных взятий
function seeMove(g, m) {
  if (!m.cap) return 0;
  const v = VAL[m.cap.toLowerCase()];
  g.make(m); const r = v - seeSquare(g, m.to); g.unmake();
  return r;
}
// бьёт ли фигура с клетки from клетку to (геометрия хода с учётом загораживающих фигур)
function hits(b, from, to) {
  const p = b[from]; if (!p || from === to) return false;
  const t = p.toLowerCase(), fr = from >> 3, fc = from & 7, tr = to >> 3, tc = to & 7, dr = tr - fr, dc = tc - fc;
  if (t === 'n') return (Math.abs(dr) === 2 && Math.abs(dc) === 1) || (Math.abs(dr) === 1 && Math.abs(dc) === 2);
  if (t === 'k') return Math.max(Math.abs(dr), Math.abs(dc)) === 1;
  if (t === 'p') return Math.abs(dc) === 1 && dr === (isW(p) ? -1 : 1);
  const diag = Math.abs(dr) === Math.abs(dc), line = dr === 0 || dc === 0;
  if ((t === 'b' && !diag) || (t === 'r' && !line) || (t === 'q' && !diag && !line)) return false;
  const sr = Math.sign(dr), sc = Math.sign(dc);
  for (let r = fr + sr, c = fc + sc; r !== tr || c !== tc; r += sr, c += sc) if (b[r * 8 + c]) return false;
  return true;
}
// Вилка: фигура на клетке sq одновременно нападает на 2+ ценные фигуры соперника
// (короля, или фигуру дороже нападающей, или незащищённую). Возвращает список типов целей.
function forkTargets(g, sq) {
  const b = g.b, p = b[sq]; if (!p) return [];
  const w = isW(p), victim = w ? 'b' : 'w', out = [];
  for (let i = 0; i < 64; i++) {
    const q = b[i]; if (!q || isW(q) === w || q.toLowerCase() === 'p') continue;
    if (!hits(b, sq, i)) continue;
    const t = q.toLowerCase();
    if (t === 'k' || VAL[t] > VAL[p.toLowerCase()] || !g.attacked(i, victim)) out.push(t);
  }
  return out.length >= 2 ? out : [];
}
// есть ли у стороны, которая ходит, мат в один ход
function mateInOne(g) {
  for (const m of g.legal()) { g.make(m); const mate = g.inCheck() && !g.legal().length; g.unmake(); if (mate) return m; }
  return null;
}
// Полный разбор хода для тренера (вызывается в фоновом потоке engine-worker.js).
// fen — позиция ДО хода, mv — сыгранный ход {from,to,promo}.
function review(fen, mv, o) {
  o = o || {};
  const g = new Game(fen), legal = g.legal();
  const an = analyze(g, { depth: o.depth || 4, time: o.time || 1500 });
  const plain = (x, lg) => x && { from: x.m.from, to: x.m.to, promo: x.m.promo, cap: x.m.cap, flag: x.m.flag, p: x.m.p, s: x.s, san: g.san(x.m, lg), see: seeMove(g, x.m) };
  const best = plain(an[0], legal), second = plain(an[1], legal);
  const mineA = an.find(x => x.m.from === mv.from && x.m.to === mv.to && (x.m.promo || '') === (mv.promo || ''));
  if (!mineA) return null;
  const mine = plain(mineA, legal);
  const bestMate1 = !!mateInOne(g);              // был ли у тебя мат в один ход
  g.make(mineA.m);
  const legal2 = g.legal();
  const tan = legal2.length ? analyze(g, { depth: o.tdepth || 3, time: o.ttime || 700 }) : [];
  const threat = tan.length ? plain(tan[0], legal2) : null;
  if (threat) {   // ставит ли лучший ответ соперника вилку
    g.make(tan[0].m); threat.fork = forkTargets(g, tan[0].m.to); g.unmake();
  }
  const m1 = legal2.length ? mateInOne(g) : null;  // может ли соперник сразу поставить мат
  // выгодные взятия соперника после твоего хода (что ты подставил), от самого ценного
  const hangs = [];
  for (const m of legal2) if (m.cap) { const s = seeMove(g, m); if (s > 0) hangs.push({ to: m.to, cap: m.cap, p: m.p, see: s, san: g.san(m, legal2) }); }
  hangs.sort((a, b) => b.see - a.see);
  return { best, second, mine, threat, n: legal.length, bestMate1, mate1: m1 ? g.san(m1, legal2) : '', hangs };
}
const api = { Game, BOTS, COACH: COACH_LEVELS[1], COACH_LEVELS, MATE, pickMove, analyze, review, evaluate, sqi, sqn, isW, START };
if (typeof module !== 'undefined') module.exports = api; else root.Chess = api;
})(this);
