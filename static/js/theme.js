(function () {
  // Настройки дизайна: фигуры, доска, фон, звуки. Хранятся в localStorage браузера.
  var OPTS = {
    pieces: ['classic', 'wood', 'flat'],
    board: ['felt', 'amber', 'ice', 'emerald'],
    bg: ['night', 'ink', 'paper', 'graphite'],
    sound: ['club', 'classic', 'wood', 'chime', 'neon', 'arcade']
  };
  var DEF = { pieces: 'classic', board: 'felt', bg: 'night', sound: 'club' };
  var root = document.documentElement;
  var cur = {};

  function load() {
    var s = null;
    try { s = JSON.parse(localStorage.getItem('design') || 'null'); } catch (e) {}
    if (!s) {
      s = {};
      try { var old = localStorage.getItem('theme'); if (old === 'light') s.bg = 'paper'; } catch (e) {}
    }
    if (s.bg === 'light') s.bg = 'paper';
    if (s.v !== 2 && s.v !== 3 && s.bg === 'ink') delete s.bg;
    if (s.v !== 3) { delete s.bg; delete s.board; delete s.pieces; delete s.sound; } // редизайн «Ночной клуб»: показать новый вид всем // «Чернила» раньше был фоном по умолчанию — теперь по умолчанию «Графит»
    for (var k in DEF) cur[k] = OPTS[k].indexOf(s[k]) >= 0 ? s[k] : DEF[k];
  }
  function apply() {
    root.dataset.theme = cur.bg; root.dataset.board = cur.board; root.dataset.pieces = cur.pieces; root.dataset.sound = cur.sound;
    var imgs = document.querySelectorAll('img[data-piece]');
    for (var i = 0; i < imgs.length; i++) imgs[i].src = pieceSrc(imgs[i].dataset.piece);
  }
  function pieceSrc(code, set) {
    set = set || cur.pieces;
    return '/static/pieces/' + (set === 'classic' ? '' : set + '/') + code + '.svg';
  }
  function set(k, v) {
    if (OPTS[k].indexOf(v) < 0) return;
    cur[k] = v; apply();
    try { localStorage.setItem('design', JSON.stringify({ v: 3, pieces: cur.pieces, board: cur.board, bg: cur.bg, sound: cur.sound })); } catch (e) {}
    window.dispatchEvent(new Event('designchange'));
  }
  load(); apply();
  window.Design = { get: function () { return cur; }, set: set, pieceSrc: pieceSrc };

  document.addEventListener('DOMContentLoaded', function () {
    apply();
    var btn = document.getElementById('design'), modal = document.getElementById('dmodal');
    if (!btn || !modal) return;
    function mark() {
      var all = modal.querySelectorAll('[data-k]');
      for (var i = 0; i < all.length; i++) all[i].classList.toggle('on', cur[all[i].dataset.k] === all[i].dataset.v);
    }
    // превью фигур для каждого набора
    var pv = modal.querySelectorAll('[data-k="pieces"] .pv');
    for (var i = 0; i < pv.length; i++) {
      var s = pv[i].parentNode.dataset.v;
      pv[i].innerHTML = ['wk', 'wn', 'bq', 'bp'].map(function (c) { return '<img alt="" src="' + pieceSrc(c, s) + '">'; }).join('');
    }
    btn.addEventListener('click', function () { mark(); modal.classList.remove('hidden'); });
    modal.addEventListener('click', function (e) {
      var o = e.target.closest('[data-k]');
      if (o) {
        set(o.dataset.k, o.dataset.v); mark();
        if (o.dataset.k === 'sound' && window.Sfx) { Sfx.play('move'); setTimeout(function () { Sfx.play('capture'); }, 380); setTimeout(function () { Sfx.play('check'); }, 760); }
        return;
      }
      if (e.target === modal || e.target.closest('.dclose')) modal.classList.add('hidden');
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') modal.classList.add('hidden'); });
  });
})();
