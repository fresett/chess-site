(function () {
'use strict';
const { Game, BOTS, COACH_LEVELS, MATE, pickMove, evaluate, sqn } = Chess;
const $ = s => document.querySelector(s);
const csrf = document.querySelector('meta[name=csrf]').content;
const auth = document.body.dataset.auth === '1';
const stage = $('.stage'), name = stage.dataset.name, myAva = stage.dataset.avatar, albertAva = stage.dataset.albert;
let myRating = stage.dataset.rating ? +stage.dataset.rating : null;   // рейтинг игрока (только для вошедших)
const AV = ['🐣', '🙂', '🧑‍🎓', '🧔', '👑'];
const SAY = {
  win: ['Отличная партия! Ты меня обыграл.', 'Браво! Красивый мат.', 'Сегодня ты сильнее. Реванш?'],
  loss: ['Хорошая попытка! Сыграем ещё?', 'Не расстраивайся, в следующий раз получится.', 'Ты был близок. Давай реванш!'],
  draw: ['Ничья — достойный результат.', 'Силы были равны. Ещё партию?'],
};
const COACH_SAY = {
  win: ['Блестяще! Ты меня обыграл — горжусь учеником!', 'Вот это игра! Мне есть чему у тебя поучиться.'],
  loss: ['Не беда — каждая партия делает тебя сильнее. Ещё одну?', 'Хорошая борьба! Внимательнее к моим угрозам — и победа твоя.'],
  draw: ['Ничья! Ты держался уверенно.', 'Равная борьба. Отличная практика!'],
};
const MODES = {
  bot: 'Классическая партия против одного из пяти ботов.',
  coach: 'Тренер Альберт оценивает каждый твой ход: 📖 книжный, ★ хороший, ! превосходный, ? неточность, ?? ошибка. При угрозе остановит игру и даст переходить.',
  local: 'Игра вдвоём на одном устройстве: ходите по очереди за белых и за чёрных.',
};
// склонения для реплик тренера
const YOUR = { q: 'твоего ферзя', r: 'твою ладью', b: 'твоего слона', n: 'твоего коня', p: 'твою пешку' };
const PIECE = { q: 'ферзя', r: 'ладью', b: 'слона', n: 'коня', p: 'пешку' };
const MY = { p: 'моя пешка', n: 'мой конь', b: 'мой слон', r: 'моя ладья', q: 'мой ферзь', k: 'мой король' };
const VAL = { p: 1, n: 3, b: 3, r: 5, q: 9, k: 0 };
const DRAWN = { stalemate: 'пат', repetition: 'троекратное повторение', fifty: 'правило 50 ходов', material: 'недостаточно материала' };

let mode = 'bot', tc = 600, clock = { w: 0, b: 0 }, tick = null, lastT = 0;
let bot = BOTS[0], coachLvl = 1, pick = 'w', user = 'w', hist = [], ucis = [], over = false, thinking = false, started = false;
let allowUndo = true, autoFlip = false, waitChoice = false, reviewing = false, pendingFinish = null;
let epoch = 0;                 // растёт при каждой новой партии/отмене — устаревшие ответы движка игнорируются
let lastUserOffer = -99, lastBotOffer = -99, botOffer = false, hintLevel = 0, lastBookName = '';
const g = new Game();
const board = new Board($('#board'), g, {
  canMove: () => started && !over && !thinking && !waitChoice && (mode === 'local' || g.turn === user),
  onMove,
});
const effTc = () => (mode === 'coach' ? 0 : tc);
const opp = () => (mode === 'coach' ? COACH_LEVELS[coachLvl] : bot);
const oppName = () => (mode === 'coach' ? 'Альберт' : bot.name);
const rnd = a => a[Math.floor(Math.random() * a.length)];
const capOf = m => (m.cap && m.flag !== 'e' ? m.cap : '');
const uciOf = m => sqn(m.from) + sqn(m.to) + (m.promo || '');
const isPiece = c => !!c && 'nbrq'.includes(c);
const material = c => g.b.reduce((s, p) => s + (p && ((p === p.toUpperCase()) === (c === 'w')) ? VAL[p.toLowerCase()] : 0), 0);

/* ---------- движок в фоновом потоке (страница не замирает, пока считается) ---------- */
const Engine = (() => {
  let w = null, seq = 0; const pend = new Map();
  try {
    w = new Worker('/static/js/engine-worker.js');
    w.onmessage = e => { const f = pend.get(e.data.id); if (f) { pend.delete(e.data.id); f(e.data.res); } };
    w.onerror = () => { w = null; pend.forEach(f => f(null)); pend.clear(); };
  } catch (e) { w = null; }
  // запасной вариант без потока (если браузер не дал создать Worker)
  function local(cmd, d) {
    const gg = new Game(d.fen);
    if (cmd === 'review') return Chess.review(d.fen, d.mv, d.opts);
    if (cmd === 'pick') { const m = pickMove(gg, d.opts); return m && { from: m.from, to: m.to, promo: m.promo }; }
    if (cmd === 'best') { const l = gg.legal(), an = Chess.analyze(gg, d.opts); return an.length ? { from: an[0].m.from, to: an[0].m.to, promo: an[0].m.promo, s: an[0].s, san: gg.san(an[0].m, l) } : null; }
    return null;
  }
  return {
    call(cmd, d) {
      return new Promise(res => {
        const done = r => res(r && !r.error ? r : local(cmd, d));
        if (!w) { setTimeout(() => res(local(cmd, d)), 0); return; }
        const id = ++seq; pend.set(id, done); w.postMessage(Object.assign({ id, cmd }, d));
      });
    },
  };
})();

/* ---------- дебютная книга (для значка 📖 и для дебютных ходов Альберта) ---------- */
let BOOK = null;
fetch('/static/openings.json').then(r => r.json()).then(d => {
  BOOK = new Map();
  const add = (uci, nm) => {
    const mv = uci.split(' ');
    for (let k = 0; k <= mv.length; k++) {
      const key = mv.slice(0, k).join(' ');
      let e = BOOK.get(key);
      if (!e) { e = { name: nm, exact: false, next: new Set() }; BOOK.set(key, e); }
      if (k === mv.length && !e.exact) { e.name = nm; e.exact = true; }
      if (k < mv.length) e.next.add(mv[k]);
    }
  };
  // сначала точные названия из топ-300 (с вариантом), потом линии топ-10 Альберта
  d.all.forEach(o => add(o[3], (o[2] || o[1]).replace(':', ',')));
  d.top.w.concat(d.top.b).forEach(o => add(o.uci, o.name));
}).catch(() => {});

/* ---------- настройка партии ---------- */
$('#bots').innerHTML = BOTS.map((b, i) => `<button class="bot${i ? '' : ' on'}" data-i="${i}"><em>${AV[i]}</em><span>${b.name}</span><b>${b.elo}</b></button>`).join('');
$('#bots').addEventListener('click', e => {
  const b = e.target.closest('.bot'); if (!b) return;
  document.querySelectorAll('.bot').forEach(x => x.classList.remove('on')); b.classList.add('on'); bot = BOTS[+b.dataset.i]; labels();
});
$('#clevel').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  document.querySelectorAll('#clevel button').forEach(x => x.classList.toggle('on', x === b)); coachLvl = +b.dataset.l; labels();
});
$('#colors').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  document.querySelectorAll('#colors button').forEach(x => x.classList.remove('on')); b.classList.add('on'); pick = b.dataset.c;
});
$('#times').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  document.querySelectorAll('#times button').forEach(x => x.classList.remove('on')); b.classList.add('on'); tc = +b.dataset.t;
  resetClocks(); renderClocks();
});
$('#modes').addEventListener('click', e => { const b = e.target.closest('button'); if (b) setMode(b.dataset.m); });
function setMode(m) {
  if (!['bot', 'coach', 'local'].includes(m) || started) return;
  mode = m;
  document.querySelectorAll('#modes button').forEach(x => x.classList.toggle('on', x.dataset.m === m));
  document.querySelectorAll('.m-bot,.m-coach,.m-local').forEach(x => x.classList.toggle('hidden', !x.classList.contains('m-' + m)));
  $('#mdesc').textContent = MODES[m];
  try { history.replaceState(null, '', '?mode=' + m); } catch (e) {}
  resetClocks(); labels();
}

/* ---------- часы ---------- */
function fmt(ms) {
  ms = Math.max(0, ms);
  if (ms < 10000) return '0:' + (ms / 1000).toFixed(1).padStart(4, '0');
  const s = Math.ceil(ms / 1000); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}
function resetClocks() { clock.w = clock.b = effTc() * 1000; }
function renderClocks() {
  const topC = board.flip ? 'w' : 'b', botC = topC === 'w' ? 'b' : 'w', run = started && !over;
  [['#ctop', topC], ['#cbot', botC]].forEach(([id, c]) => {
    const el = $(id); el.classList.toggle('hidden', !effTc()); el.textContent = fmt(clock[c]);
    el.classList.toggle('on', run && g.turn === c); el.classList.toggle('low', clock[c] < 10000);
  });
}
function stopClock() { if (tick) clearInterval(tick); tick = null; }
function startClock() {
  stopClock(); if (!effTc()) return; lastT = Date.now();
  tick = setInterval(() => {
    const now = Date.now(), d = now - lastT; lastT = now;
    if (!started || over) return;
    clock[g.turn] -= d;
    if (clock[g.turn] <= 0) { clock[g.turn] = 0; renderClocks(); finish('timeout', g.turn); return; }
    renderClocks();
  }, 100);
}

/* ---------- имена игроков ---------- */
function who(el, p) {
  el.textContent = '';
  if (p.img) { const i = document.createElement('img'); i.className = 'ava'; i.src = p.img; i.alt = ''; el.appendChild(i); }
  else { const e = document.createElement('em'); e.className = 'ava em'; e.textContent = p.emoji; el.appendChild(e); }
  el.appendChild(document.createTextNode(p.text));
}
function player(c) {
  if (mode === 'local') return c === 'w' ? { emoji: '♔', text: 'Белые' } : { emoji: '♚', text: 'Чёрные' };
  if (c === user) return { img: myAva, text: myRating != null ? `${name} (${myRating})` : name };
  if (mode === 'coach') return { img: albertAva, text: `Тренер Альберт (${COACH_LEVELS[coachLvl].elo})` };
  return { emoji: AV[BOTS.indexOf(bot)], text: `${bot.name} (${bot.elo})` };
}
function labels() {
  renderClocks();
  if (!started && mode !== 'local') user = 'w';
  const topC = board.flip ? 'w' : 'b';
  who($('#top'), player(topC)); who($('#bottom'), player(topC === 'w' ? 'b' : 'w'));
}

/* ---------- партия ---------- */
function start() {
  epoch++;
  user = mode === 'local' ? 'w' : (pick === 'r' ? (Math.random() < .5 ? 'w' : 'b') : pick);
  allowUndo = mode === 'coach' ? true : $('#allowUndo').checked;
  autoFlip = mode === 'local' && $('#autoFlip').checked;
  g.load(Chess.START); hist = []; ucis = []; started = true; over = false; thinking = false; waitChoice = false; reviewing = false; pendingFinish = null;
  lastUserOffer = lastBotOffer = -99; botOffer = false; hintLevel = 0; lastBookName = '';
  board.flip = mode !== 'local' && user === 'b'; board.last = null; board.sel = -1; board.targets = []; board.badge = null; board.hint = -1; board.warn = -1;
  resetClocks(); startClock();
  closeModal(); hideOffer();
  $('#setup').classList.add('hidden'); $('#side').classList.remove('hidden');
  $('#coach').classList.toggle('hidden', mode !== 'coach');
  $('#evalbox').classList.toggle('hidden', mode !== 'coach'); setEval(0);
  if (mode === 'coach') coachSay(rnd(['Удачи! Я буду следить за каждым твоим ходом.', 'Начинаем! Не торопись и следи за моими угрозами.']), null, false);
  labels(); draw(); Sfx.play('start');
  if (mode !== 'local' && g.turn !== user) botMove();
}
function draw() {
  board.render();
  let h = '';
  for (let i = 0; i < hist.length; i += 2) h += `<i>${i / 2 + 1}.</i><span>${hist[i]}</span><span>${hist[i + 1] || ''}</span>`;
  const m = $('#moves'); m.innerHTML = h; m.scrollTop = m.scrollHeight;
  const spans = m.querySelectorAll('span'); if (hist.length) spans[hist.length - 1].classList.add('cur');
  status();
}
// надпись, часы и кнопки — без перерисовки доски (чтобы не сбить анимацию хода)
function status(text) {
  renderClocks(); controls();
  if (over) return;
  let t = text;
  if (!t) {
    if (waitChoice) t = 'Переходить или продолжить?';
    else if (thinking) t = mode === 'coach' ? (reviewing ? 'Альберт оценивает твой ход…' : 'Альберт думает…') : 'Бот думает…';
    else if (mode === 'local') t = g.turn === 'w' ? 'Ход белых' : 'Ход чёрных';
    else t = g.turn === user ? 'Твой ход' : '';
  }
  $('#status').textContent = t;
  $('#status').classList.toggle('busy', thinking);
}
function controls() {
  const live = started && !over;
  $('#resign').classList.toggle('hidden', !live);
  $('#draw').classList.toggle('hidden', !live);
  $('#again').classList.toggle('hidden', live);
  $('#undo').classList.toggle('hidden', !allowUndo || !live);
  $('#hint').classList.toggle('hidden', !live || mode !== 'coach');
}
function onMove(from, to, promo) {
  const fenBefore = g.fen();
  const r = g.move(from, to, promo); if (!r) return;
  hist.push(r.san); ucis.push(uciOf(r.m)); board.last = [from, to];
  board.badge = null; board.hint = -1; board.warn = -1; hintLevel = 0;
  if (botOffer) { hideOffer(); botOffer = false; status('Ты отклонил ничью'); }
  // тренер: ход ведёт к ничьей (пат, повторение), а ты был в большом выигрыше — предупредим ДО конца партии
  const s = g.status();
  if (mode === 'coach' && s && s !== 'checkmate' && material(user) - material(user === 'w' ? 'b' : 'w') >= 3) {
    draw(); board.animateLast(capOf(r.m)); Sfx.san(r.san);
    board.setBadge(to, 'blunder'); pendingFinish = s; waitChoice = true; status();
    coachSay(`Стоп! Это ${DRAWN[s]} — ничья, а у тебя большой перевес. Готов переходить?`, 'blunder', true);
    return;
  }
  after(r);
  if (over) return;
  if (mode === 'local') {
    if (autoFlip) setTimeout(() => { if (started && !over) { board.flip = g.turn === 'b'; labels(); board.render(); } }, 380);
    return;
  }
  if (mode === 'coach') review(r.m, fenBefore); else botMove();
}
function after(r) {
  const s = g.status(); draw(); board.animateLast(capOf(r.m)); Sfx.san(r.san);
  if (s) finish(s);
}
function botMove() {
  thinking = true; reviewing = false; status();
  const ep = epoch, t0 = Date.now();
  const o = opp(), cfg = effTc() ? Object.assign({}, o, { time: Math.max(120, Math.min(o.time, clock[g.turn] / 25)) }) : o;
  // Альберт в дебюте играет по книге — так партии выглядят естественно
  const bookMove = () => {
    if (mode !== 'coach' || !BOOK || hist.length > 16 || Math.random() < (coachLvl === 0 ? 0.4 : 0.1)) return null;
    const e = BOOK.get(ucis.join(' ')); if (!e || !e.next.size) return null;
    const u = rnd([...e.next]);
    return { from: Chess.sqi(u.slice(0, 2)), to: Chess.sqi(u.slice(2, 4)), promo: u[4] };
  };
  const book = bookMove();
  const ask = book ? Promise.resolve(book) : Engine.call('pick', { fen: g.fen(), opts: cfg });
  ask.then(m => {
    if (ep !== epoch || over || !started) return;
    const minDelay = tc === 60 && mode === 'bot' ? 150 : 450;   // не отвечать мгновенно — так видно анимацию
    setTimeout(() => {
      if (ep !== epoch || over || !started) return;
      let r = m && g.move(m.from, m.to, m.promo);
      if (!r) { const mm = pickMove(g, cfg); r = mm && g.move(mm.from, mm.to, mm.promo); }
      if (!r) { thinking = false; status(); return; }
      hist.push(r.san); ucis.push(uciOf(r.m)); board.last = [r.m.from, r.m.to];
      if (board.badge && board.badge.sq === r.m.to) board.badge = null; // фигуру со значком взяли
      thinking = false; after(r);
      if (!over) maybeBotOffer();
    }, Math.max(0, minDelay - (Date.now() - t0)));
  });
}

/* ---------- тренер Альберт ---------- */
function review(moved, fenBefore) {
  // книжный ход — хвалим и не тратим время на расчёт
  const e = BOOK && BOOK.get(ucis.join(' '));
  if (e) {
    board.setBadge(moved.to, 'book');
    // название объявляем, только когда позиция точно совпадает с дебютом (после 1.e4 это ещё не «Итальянская»)
    const named = e.exact && e.name !== lastBookName; if (named) lastBookName = e.name;
    coachSay(named ? say('bookNamed', { n: e.name }) : say('book'), 'book', false);
    botMove(); return;
  }
  thinking = true; reviewing = true; status();
  const ep = epoch;
  // глубина 5 (если успевает за 2,5 с) — так тренер видит вилки и тактику на ход-два вперёд
  Engine.call('review', { fen: fenBefore, mv: { from: moved.from, to: moved.to, promo: moved.promo }, opts: { depth: 5, time: 2500, tdepth: 4, ttime: 1000 } }).then(r => {
    if (ep !== epoch || over || !started) return;
    thinking = false; reviewing = false;
    if (!r || !r.best || !r.mine) { status(); botMove(); return; }
    const prevMove = g.stack.length >= 2 ? g.stack[g.stack.length - 2].m : null;   // последний ход соперника
    const v = classify(moved, r, prevMove);
    board.setBadge(moved.to, v.k);
    setEval(r.mine.s);
    if (v.danger) {
      const w = v.hang ? v.hang.to : v.fork ? r.threat.to : (r.threat && r.threat.cap && !v.mate1 && !v.mateThreat ? r.threat.to : -1);
      if (w >= 0) { board.warn = w; board.render(); }
      waitChoice = true; status(); Sfx.play('wrong');
      coachSay(coachText(v, r), v.k, true);
    } else {
      coachSay(coachText(v, r), v.k, false);
      botMove();
    }
  });
}
// Классификация хода. Материал считаем через SEE (размен на клетке с учётом всех ответных взятий),
// поэтому «упустил коня» или «могу взять ладью» звучит только когда это реальный выигрыш, а не размен.
function classify(moved, r, prev) {
  const cl = s => Math.max(-3000, Math.min(3000, s)), low = c => (c ? c.toLowerCase() : '');
  const { best, second, mine, threat } = r, loss = cl(best.s) - cl(mine.s);
  // Ответное взятие на клетке, где ты только что взял, — это размен. Потерей считаем только если
  // по итогам размена ты в минусе хотя бы на лёгкую фигуру (например, ферзь за пешку).
  const isTradeBack = h => mine.cap && h.to === mine.to && mine.see > -300;
  const real = (r.hangs || []).filter(h => !isTradeBack(h));
  const hang = real.find(h => h.see >= 200 && isPiece(low(h.cap))) || null;   // ты подставил фигуру
  const pawn = real.find(h => low(h.cap) === 'p' && h.see >= 100);
  // жертва: ты сознательно отдаёшь материал взятием (размен в минус) — не путаем с зевком
  const sac = !!mine.cap && mine.see <= -100 && mine.see > -300;
  const winsMaterial = !!mine.cap && mine.see >= 0;                         // взятие выгодно по размену
  const threatIsRecapture = !!(threat && threat.cap && threat.to === mine.to);
  const v = {
    loss, hang,
    castle: moved.flag === 'c',
    forced: r.n === 1,                                   // единственный легальный ход
    doomed: best.s < -MATE / 2,                          // мат неизбежен при любом ходе
    haveMate: mine.s > MATE / 2,
    missedMate: best.s > MATE / 2 && mine.s <= MATE / 2,
    mate1: r.mate1,                                      // соперник ставит мат сразу
    mateThreat: !!threat && threat.s > MATE / 2,
    pawnHang: !hang && !!pawn && loss < 200, sac,   // при большей потере дело не в одной пешке
    missedWin: isPiece(low(best.cap)) && best.see >= 250 && best.to !== mine.to ? low(best.cap) : '',
    took: low(mine.cap), tookGain: mine.see,
    recapture: !!(prev && prev.cap && mine.cap && prev.to === mine.to),
    trade: !!(mine.cap && threat && threat.cap && threat.to === mine.to),
    check: /\+$/.test(mine.san), promo: !!mine.promo,
    // вилка: лучший ответ соперника нападает сразу на две твои ценные фигуры (или короля и фигуру)
    fork: threat && threat.fork && threat.fork.length >= 2 ? threat.fork : null,
  };
  // Игру останавливаем только при настоящей угрозе: мат (сразу или форсированный);
  // подставленная фигура, которую соперник выигрывает с учётом разменов; или крупная потеря (вилка, связка).
  // жертва с большой потерей по расчёту (например, конь за пешку) — тоже спрашиваем, но честно называем, что отдаёшь
  v.sacDanger = sac && loss >= 200;
  // если почти все ходы ведут к мату (лучшего хода не было), «переходить» бессмысленно
  if (v.mateThreat && loss < 200) v.doomed = true;
  if (v.fork && loss < 150) v.fork = null;           // «вилка», которая ничего не выигрывает, — не угроза
  v.danger = !v.doomed && !v.forced && (!!v.mate1 || v.mateThreat || (!!hang && loss >= 150) || v.sacDanger || !!v.fork ||
             (loss >= 250 && !v.missedMate && !v.missedWin && !v.pawnHang && !sac && !winsMaterial && !threatIsRecapture));
  const gap = second ? cl(best.s) - cl(second.s) : 0;
  // ★ — только если ход почти не уступает лучшему (до 0,4 пешки). Рокировка и «взять обратно» больше
  // не получают звезду автоматически: если был ход заметно сильнее — это «?», даже когда ты просто отступил из-под удара.
  if (v.danger) v.k = 'blunder';
  else if (v.forced || v.doomed) v.k = 'good';
  else if (sac) v.k = loss < 120 ? 'good' : 'inacc';              // жертву не называем грубой ошибкой: движок мог не досчитать
  else if (winsMaterial && loss >= 200) v.k = 'inacc';             // выгодное взятие — максимум «?», не «??»
  else if (loss <= 10 && gap >= 150 && !v.recapture) v.k = 'best';
  else if (loss <= 40) v.k = 'good';
  else if (loss < 200) v.k = 'inacc';
  else v.k = 'blunder';
  return v;
}

/* ---------- реплики тренера: по несколько вариантов, без повторов подряд ---------- */
const PH = {
  book: ['Книжный ход.', 'Всё по теории.', 'Так играют мастера.', 'Правильно, это теория.', 'Дебют разыгрываешь грамотно.', 'По книге!'],
  bookNamed: ['Книжный ход. Это {n}.', 'По теории! Это {n}.', 'Знаешь дебют: {n}.', 'Это {n} — хороший выбор.'],
  good: ['Хороший ход!', 'Так держать.', 'Верно.', 'Разумно.', 'Крепкий ход.', 'Хорошо сыграно.', 'Правильная идея.', 'Отлично, продолжай.',
         'Неплохо!', 'Уверенно.', 'Хороший выбор.', 'Именно так.'],
  best: ['Превосходно! Единственный сильный ход в позиции.', 'Отличная находка!', 'Блестяще — лучший ход!', 'Точно в цель! Сильнее не придумать.',
         'Вот это ход! Нашёл лучшее.', 'Браво, именно этот ход!'],
  bestTake: ['Превосходно! Забираешь {p}.', 'Отличная находка — выигрываешь {p}!', 'Блестяще, {p} достаётся тебе даром!'],
  castle: ['Рокировка — отличное решение: король в безопасности!', 'Правильно, сначала спрячь короля.', 'Хорошая рокировка, ладья вступает в игру.',
           'Король в укрытии — так держать.', 'Своевременная рокировка!'],
  promo: ['Превращение пешки — отлично!', 'Новый ферзь на доске!', 'Пешка дошла — молодец!'],
  recapture: ['Верно, отыгрываешь {p}.', 'Правильно, забираешь обратно {p}.', 'Материал восстановлен — верно.', 'Конечно, берём обратно.'],
  trade: ['Размен — разумное решение.', 'Хороший размен.', 'Меняешься — нормально.', 'Упрощаешь позицию — разумно.'],
  take: ['Отлично, забираешь {p}!', 'Хороший ход — выигрываешь {p}.', 'Забираешь {p} — так и надо!', 'Есть! Ты отнял у меня {p}.'],
  takePawn: ['Хорошо, пешка твоя.', 'Верно, забираешь пешку.', 'Лишняя пешка — приятно.', 'Пешка в копилку.'],
  check: ['Шах! Хороший ход.', 'Хороший шах.', 'Шах — и с пользой.', 'Активно! Шах.'],
  sacOk: ['Смелая жертва! Посмотрим, что ты задумал.', 'Интересная жертва!', 'Рискованно, но интересно!'],
  sacBad: ['Смелая жертва! Но я не вижу компенсации — надёжнее было {b}.', 'Жертва? Хм, по-моему, точнее {b}.'],
  inacc: ['Неплохо, но сильнее было {b}.', 'Можно было лучше: {b}.', 'Есть ход посильнее — {b}.', 'Не лучший выбор. Точнее {b}.',
          'Хм, здесь был ход точнее: {b}.', 'Так можно, но {b} давало больше.', 'Подумай в следующий раз о {b}.'],
  pawnHang: ['Так ты отдаёшь пешку. Точнее было {b}.', 'Осторожно с пешками — одна повисла. Лучше {b}.', 'Пешка под ударом. Сильнее было {b}.'],
  missedWin: ['Ты упустил {p}: можно было {b}.', 'А ведь можно было выиграть {p} — {b}!', 'Посмотри внимательнее: {b} забирало {p}.'],
  missedMate1: ['Ты упустил мат в один ход: {b}!', 'Был мат сразу — {b}! Ничего, ищи дальше.', 'Эх, {b} ставило мат!'],
  missedMate: ['Ты упустил форсированный мат — он начинался с {b}.', 'Здесь был выигрыш через мат: начни с {b}.'],
  blunderSoft: ['Это ошибка — сильнее было {b}.', 'Серьёзная неточность. Лучше {b}.', 'После этого позиция заметно хуже. Точнее {b}.'],
  haveMate: ['Отлично! У тебя форсированный мат — найди его!', 'Атака решает: мат уже близко!', 'Ты в шаге от победы — ищи мат!'],
  forced: ['Единственный ход.', 'Других ходов не было.', 'Вынужденно, но верно.'],
  doomed: ['Позиция тяжёлая — мат уже не избежать. Держись!', 'Здесь уже не спастись. Ничего, учимся!'],
  doomedAttack: ['Опасно: у меня сильная атака на короля, но лучшего хода тут не было.', 'Атака серьёзная, но ты выбрал лучшее из возможного.'],
  // остановка игры (угроза)
  dMate1: ['Осторожно! После этого я ставлю мат: {m}.', 'Стоп! Тут мат в один ход: {m}.', 'Внимание: {m} — и это мат!'],
  dMate: ['Осторожно! Теперь у меня есть матовая атака.', 'Стоп! Твоему королю грозит мат.', 'Внимание: я вижу мат твоему королю.'],
  dHang: ['Осторожно, {my} может взять {your}!', 'Внимание: после этого {my} забирает {your}.', 'Стоп! {your_c} под ударом — {my} заберёт.'],
  dFork: ['Осторожно! После этого у меня вилка {t}: нападаю сразу на {list}.', 'Стоп! Ход {t} — и я атакую одновременно {list}.',
          'Внимание: {t} — двойной удар по {list_d}.'],
  dSac: ['Ты отдаёшь {mp} за {p}. Если это жертва — ты точно всё просчитал?', 'Отдавать {mp} за {p}? Уверен, что жертва оправдана?'],
  dTactic: ['Осторожно: у меня есть сильный ответ {t}.', 'Подумай ещё: на это у меня есть {t}.', 'Стоп! После этого {t} — и тебе будет тяжело.'],
  ask: [' Готов переходить?', ' Переходишь?', ' Может, переходишь?'],
};
const lastPh = {};
// выбрать вариант фразы (не тот же, что в прошлый раз) и подставить значения {…}
function say(key, vals) {
  const arr = PH[key]; let i = Math.floor(Math.random() * arr.length);
  if (arr.length > 1 && i === lastPh[key]) i = (i + 1) % arr.length;
  lastPh[key] = i;
  return arr[i].replace(/\{(\w+)\}/g, (_, k) => (vals && vals[k] != null ? vals[k] : ''));
}
// падежи для перечисления целей вилки: «нападаю на короля и ладью», «двойной удар по королю и ладье»
const ACC = { k: 'короля', q: 'ферзя', r: 'ладью', b: 'слона', n: 'коня' };
const DAT = { k: 'королю', q: 'ферзю', r: 'ладье', b: 'слону', n: 'коню' };
const NOM_YOUR = { q: 'Твой ферзь', r: 'Твоя ладья', b: 'Твой слон', n: 'Твой конь', p: 'Твоя пешка' };
const listOf = (ts, f) => { const u = [...new Set(ts)].map(t => f[t]); return u.length > 1 ? u.slice(0, -1).join(', ') + ' и ' + u[u.length - 1] : u[0]; };
function coachText(v, r) {
  const b = r.best.san;
  if (v.danger) {
    let msg;
    if (v.mate1) msg = say('dMate1', { m: r.mate1 });
    else if (v.mateThreat) msg = say('dMate');
    else if (v.hang) { const c = v.hang.cap.toLowerCase(); msg = say('dHang', { my: MY[v.hang.p.toLowerCase()] || 'я', your: YOUR[c], your_c: NOM_YOUR[c] }); }
    else if (v.fork) msg = say('dFork', { t: r.threat.san, list: listOf(v.fork, ACC), list_d: listOf(v.fork, DAT) });
    else if (v.sacDanger) msg = say('dSac', { mp: PIECE[r.mine.p.toLowerCase()], p: PIECE[v.took] });
    else msg = say('dTactic', { t: r.threat.san });
    return msg + say('ask');
  }
  const ok = v.k === 'best' || v.k === 'good';
  if (v.doomed) return say(v.mateThreat ? 'doomedAttack' : 'doomed');
  if (v.forced) return say('forced');
  if (v.haveMate && ok) return say('haveMate');
  if (v.castle && ok) return say('castle');
  if (v.promo && ok) return say('promo');
  if (v.sac) return ok ? say('sacOk') : say('sacBad', { b });
  if (v.recapture && ok) return say('recapture', { p: PIECE[v.took] || 'фигуру' });
  if (v.trade && ok) return say('trade');
  if (v.k === 'best') return isPiece(v.took) && v.tookGain >= 200 ? say('bestTake', { p: PIECE[v.took] }) : say('best');
  if (v.k === 'good') {
    if (isPiece(v.took) && v.tookGain >= 200) return say('take', { p: PIECE[v.took] });
    if (v.took === 'p' && v.tookGain >= 100) return say('takePawn');
    if (v.check) return say('check');
    return say('good');
  }
  // неточность или ошибка без прямой угрозы — совет, игра идёт дальше
  if (v.missedMate) return say(r.bestMate1 ? 'missedMate1' : 'missedMate', { b });
  if (v.missedWin) return say('missedWin', { p: PIECE[v.missedWin], b });
  if (v.pawnHang) return say('pawnHang', { b });
  return say(v.k === 'blunder' ? 'blunderSoft' : 'inacc', { b });
}
function coachSay(text, k, choice) {
  const box = $('#coach');
  box.className = 'coachbox' + (k ? ' k-' + k : '');
  $('#ctext').textContent = text;
  $('#cchoice').classList.toggle('hidden', !choice);
  box.querySelector('.cbub').animate([{ transform: 'scale(.96)', opacity: .4 }, { transform: 'none', opacity: 1 }], { duration: 220, easing: 'ease-out' });
  if (window.Albert) Albert.say(text);
}
// шкала оценки позиции (с твоей стороны)
function setEval(s) {
  let pct, txt;
  if (Math.abs(s) > MATE / 2) { pct = s > 0 ? 100 : 0; txt = s > 0 ? 'У тебя форсированный мат' : 'У Альберта матовая атака'; }
  else {
    const p = Math.max(-10, Math.min(10, s / 100));
    pct = 50 + p * 5;
    txt = Math.abs(p) < 0.5 ? 'Позиция равная' : (p > 0 ? 'Ты лучше' : 'Альберт лучше') + ` (${p > 0 ? '+' : ''}${p.toFixed(1)})`;
  }
  $('#evalfill').style.width = pct + '%'; $('#evaltext').textContent = txt;
}
$('#ctake').onclick = () => {
  if (!waitChoice) return;
  waitChoice = false; pendingFinish = null; board.warn = -1; undoPlies(1);
  coachSay('Давай попробуем ещё раз. Подумай, что может ответить соперник.', null, false);
};
$('#cgo').onclick = () => {
  if (!waitChoice) return;
  waitChoice = false; board.warn = -1; $('#cchoice').classList.add('hidden');
  if (pendingFinish) { const s = pendingFinish; pendingFinish = null; finish(s); return; }
  board.render();
  coachSay('Хорошо, продолжаем. Будь внимательнее!', null, false);
  botMove();
};
// подсказка: первое нажатие — какой фигурой ходить, второе — весь ход
$('#hint').onclick = () => {
  if (mode !== 'coach' || thinking || waitChoice || over || g.turn !== user) return;
  const ep = epoch, n = hist.length;
  status('Альберт ищет лучший ход…');
  Engine.call('best', { fen: g.fen(), opts: { depth: 4, time: 1200 } }).then(b => {
    if (ep !== epoch || n !== hist.length || !b) { status(); return; }
    hintLevel++;
    board.hint = b.from; board.render(); status();
    coachSay(hintLevel === 1 ? 'Подумай о ходе фигурой с подсвеченной клетки.' : `Я бы сыграл ${b.san}.`, null, false);
  });
};

/* ---------- ничья по соглашению ---------- */
function hideOffer() { $('#offer').classList.add('hidden'); }
function showOffer(text) {
  $('#offertext').textContent = text; $('#offer').classList.remove('hidden'); Sfx.play('notify');
  if (mode === 'coach') coachSay('Предлагаю ничью. Согласен?', null, false);
}
$('#draw').onclick = () => {
  if (!started || over) return;
  if (mode === 'local') {
    askConfirm({ title: 'Ничья?', sub: 'Соперник согласен закончить партию вничью?', yes: '🤝 Да, ничья', danger: false }, () => finish('agreement'));
    return;
  }
  if (thinking || waitChoice) return;
  if (hist.length - lastUserOffer < 10) { status('Предлагать ничью можно не чаще раза в 5 ходов'); return; }
  lastUserOffer = hist.length;
  const ep = epoch, n = hist.length, botC = user === 'w' ? 'b' : 'w';
  status(`${oppName()} думает над предложением…`);
  Engine.call('best', { fen: g.fen(), opts: { depth: 3, time: 700 } }).then(b => {
    if (ep !== epoch || over || n !== hist.length) return;
    const s = b ? b.s : 0, botEval = g.turn === botC ? s : -s;   // оценка с точки зрения бота
    let accept;
    if (botEval > 60) accept = false;                              // ему лучше — играет дальше
    else if (hist.length < 16) accept = botEval < -300;             // слишком рано
    else if (botEval <= -150) accept = Math.random() < 0.85;        // ему хуже — почти всегда соглашается
    else accept = Math.random() < 0.35 + (hist.length >= 80 ? 0.25 : 0);
    const coach = mode === 'coach';
    if (accept) {
      const t = coach ? 'Согласен, ничья. Хорошая партия!' : `${bot.name} принимает ничью.`;
      if (coach) coachSay(t, null, false); else status(t);
      setTimeout(() => finish('agreement'), 700);
    } else {
      const why = botEval > 60 ? 'Нет, я ещё поиграю — моя позиция лучше.' : hist.length < 16 ? 'Ещё рано, давай поиграем!' : 'Нет, давай доиграем.';
      if (coach) coachSay(why, null, false); else status(`${bot.name}: «${why}»`);
      Sfx.play('wrong');
    }
  });
};
// бот сам предлагает ничью, если ему хуже или позиция долго равная
function maybeBotOffer() {
  if (mode === 'local' || hist.length < 30 || hist.length - lastBotOffer < 20 || g.turn !== user) return;
  const botEval = -evaluate(g);   // evaluate — с точки зрения того, кто ходит (это ты)
  const offer = (botEval <= -250 && Math.random() < 0.3) || (Math.abs(botEval) <= 40 && hist.length >= 60 && Math.random() < 0.12);
  if (!offer) return;
  lastBotOffer = hist.length; botOffer = true;
  showOffer(`🤝 ${mode === 'coach' ? 'Тренер Альберт' : bot.name} предлагает ничью`);
}
$('#oyes').onclick = () => { if (!botOffer || over) return; botOffer = false; hideOffer(); finish('agreement'); };
$('#ono').onclick = () => { botOffer = false; hideOffer(); status('Ты отклонил ничью'); if (mode === 'coach') coachSay('Хорошо, играем дальше!', null, false); };

/* ---------- отмена ходов (с анимацией) ---------- */
function undoPlies(n) {
  epoch++;   // ответы движка для старой позиции больше не нужны
  const undone = [];
  for (let i = 0; i < n && hist.length; i++) { undone.push(g.stack[g.stack.length - 1].m); g.undo(); hist.pop(); ucis.pop(); }
  const top = g.stack[g.stack.length - 1];
  board.last = top ? [top.m.from, top.m.to] : null; board.sel = -1; board.targets = []; board.badge = null; board.pending = null; board.hint = -1; board.warn = -1;
  thinking = false; reviewing = false; hintLevel = 0; botOffer = false; hideOffer();
  if (autoFlip) { board.flip = g.turn === 'b'; labels(); }
  draw(); board.animateBack(undone); Sfx.play('move');
}
$('#undo').onclick = () => {
  if (!allowUndo || over || !hist.length) return;
  if (waitChoice) { $('#ctake').onclick(); return; }
  if (thinking && mode !== 'coach') return;
  // ход бота ещё считается — отменяем только свой ход; иначе — свой и ответ соперника
  undoPlies(mode === 'local' ? 1 : (g.turn === user ? 2 : 1));
  if (mode !== 'local' && g.turn !== user) botMove();
};

/* ---------- конец партии ---------- */
const REASON = { checkmate: 'объявлен мат', stalemate: 'пат', material: 'недостаточно материала', fifty: 'правило 50 ходов', repetition: 'троекратное повторение', resign: 'сдача', timeout: 'время вышло', agreement: 'ничья по соглашению' };
function finish(s, loser) {
  if (over) return; over = true; thinking = false; waitChoice = false; pendingFinish = null; botOffer = false;
  stopClock(); hideOffer(); controls(); renderClocks();
  $('#cchoice').classList.add('hidden');
  let lc = null;   // проигравший цвет (null — ничья)
  if (s === 'checkmate') lc = g.turn;
  if (s === 'resign' || s === 'timeout') lc = loser;
  let res, title, sub = REASON[s], av = null, text;
  if (mode === 'local') {
    res = lc ? 'win' : 'draw';
    title = lc ? (lc === 'w' ? 'Чёрные победили' : 'Белые победили') : 'Ничья';
    if (s === 'resign') sub = lc === 'w' ? 'белые сдались' : 'чёрные сдались';
    if (s === 'timeout') sub = lc === 'w' ? 'у белых вышло время' : 'у чёрных вышло время';
    av = { emoji: lc ? (lc === 'w' ? '♚' : '♔') : '🤝' }; text = 'Отличная партия! Сыграете ещё?';
    $('#mrating').classList.add('hidden');   // оффлайн-партии на рейтинг не влияют
  } else {
    res = lc ? (lc === user ? 'loss' : 'win') : 'draw';
    title = { win: 'Победа!', loss: 'Поражение', draw: 'Ничья' }[res];
    if (s === 'resign') sub = 'вы сдались';
    else if (s === 'timeout') sub = res === 'win' ? 'у соперника вышло время' : 'у вас вышло время';
    else if (s === 'checkmate') sub = res === 'win' ? 'вы объявили мат' : 'вам объявлен мат';
    av = mode === 'coach' ? { img: albertAva } : { emoji: AV[BOTS.indexOf(bot)] };
    text = rnd((mode === 'coach' ? COACH_SAY : SAY)[res]);
    // рейтинг: сервер считает изменение (+7…+11 за победу, −7…−12 за поражение, не ниже 100)
    const mr = $('#mrating'); mr.classList.add('hidden');
    if (auth) {
      fetch('/api/game-result', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify({ bot: opp().elo, mode, result: res, moves: hist.join(' ') }) })
        .then(r => r.json()).then(d => {
          if (d.rating == null) return;
          myRating = d.rating; labels();
          mr.className = 'mrating ' + (d.delta > 0 ? 'up' : d.delta < 0 ? 'down' : '');
          mr.textContent = `Рейтинг: ${d.rating} (${d.delta > 0 ? '+' : ''}${d.delta})`;
        }).catch(() => {});
    } else {
      mr.className = 'mrating'; mr.textContent = 'Войди в аккаунт, чтобы получать рейтинг за партии';
    }
  }
  $('#status').textContent = title + ' — ' + sub; $('#status').classList.remove('busy');
  if (mode === 'coach') coachSay(text, null, false);
  setTimeout(() => Sfx.play(res), 350);
  setTimeout(() => {
    $('#mbox').className = 'mbox ' + res;
    $('#mtitle').textContent = title; $('#msub').textContent = sub;
    const mav = $('#mav'); mav.textContent = '';
    if (av.img) { const i = document.createElement('img'); i.src = av.img; i.alt = ''; mav.appendChild(i); } else mav.textContent = av.emoji;
    $('#mtext').textContent = text;
    $('#rematch').textContent = mode === 'local' ? 'Ещё партию' : 'Реванш';
    $('#modal').classList.remove('hidden');
  }, 900);
}
function closeModal() { $('#modal').classList.add('hidden'); }
$('#start').onclick = start;
$('#again').onclick = $('#mnew').onclick = () => {
  epoch++; if (window.Albert) Albert.stop();
  closeModal(); stopClock(); hideOffer(); started = false; over = false; thinking = false; waitChoice = false;
  g.load(Chess.START); board.last = null; board.flip = false; board.badge = null; board.hint = -1; board.warn = -1; hist = []; ucis = []; resetClocks();
  $('#side').classList.add('hidden'); $('#setup').classList.remove('hidden'); labels(); board.render();
};
$('#rematch').onclick = () => {
  if (mode !== 'local') {
    pick = user === 'w' ? 'b' : 'w';
    document.querySelectorAll('#colors button').forEach(x => x.classList.toggle('on', x.dataset.c === pick));
  }
  start();
};
$('#mview').onclick = $('#mclose').onclick = closeModal;
$('#flip').onclick = () => { board.flip = !board.flip; labels(); board.render(); };

/* ---------- окно подтверждения (сдача, ничья в оффлайне) ---------- */
let onYes = null;
function askConfirm(o, yes) {
  $('#cfh').textContent = o.title; $('#ctitle').textContent = o.sub; $('#cyes').textContent = o.yes;
  $('#cyes').classList.toggle('danger', o.danger !== false);
  onYes = yes; $('#confirm').classList.remove('hidden');
}
const closeConfirm = () => { $('#confirm').classList.add('hidden'); onYes = null; };
$('#resign').onclick = () => {
  if (!started || over) return;
  askConfirm({ title: 'Сдаться?', sub: mode === 'local' ? `Сдаются ${g.turn === 'w' ? 'белые' : 'чёрные'} (чей сейчас ход)` : 'Партия будет засчитана как поражение', yes: '🏳 Сдаться' },
    () => finish('resign', mode === 'local' ? g.turn : user));
};
$('#cno').onclick = closeConfirm;
$('#cyes').onclick = () => { const f = onYes; closeConfirm(); if (f && started && !over) f(); };
$('#confirm').addEventListener('click', e => { if (e.target.id === 'confirm') closeConfirm(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeConfirm(); });

/* ---------- голос Альберта ---------- */
function voiceBtn() {
  const b = $('#cvoice');
  b.classList.toggle('hidden', !(window.Albert && Albert.supported));
  const on = window.Albert && Albert.on();
  b.textContent = on ? '🔊' : '🔇'; b.title = on ? 'Выключить голос Альберта' : 'Включить голос Альберта';
}
$('#cvoice').onclick = () => { Albert.toggle(); voiceBtn(); if (Albert.on()) Albert.say($('#ctext').textContent); };
voiceBtn();

const qMode = new URLSearchParams(location.search).get('mode');
setMode(['bot', 'coach', 'local'].includes(qMode) ? qMode : 'bot');
board.render();
})();
