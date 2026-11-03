// Фоновый поток для тяжёлых расчётов тренера: страница не замирает, пока Альберт думает.
importScripts('/static/js/chess.js');
const { Game, analyze, review, pickMove } = self.Chess;
self.onmessage = e => {
  const { id, cmd, fen, mv, opts } = e.data;
  let res = null;
  try {
    if (cmd === 'review') res = review(fen, mv, opts);
    else if (cmd === 'best') {      // подсказка / оценка позиции: лучший ход и оценка
      const g = new Game(fen), legal = g.legal(), an = analyze(g, opts || { depth: 4, time: 1200 });
      res = an.length ? { from: an[0].m.from, to: an[0].m.to, promo: an[0].m.promo, s: an[0].s, san: g.san(an[0].m, legal) } : null;
    } else if (cmd === 'pick') {    // ход бота
      const g = new Game(fen), m = pickMove(g, opts);
      res = m && { from: m.from, to: m.to, promo: m.promo };
    }
  } catch (err) { res = { error: String(err) }; }
  self.postMessage({ id, res });
};
