(function () {
'use strict';
const $ = s => document.querySelector(s);
const csrf = document.querySelector('meta[name=csrf]').content;
const msg = t => { $('#avamsg').textContent = t; };
function setAva(url) {
  $('#avaimg').src = url;
  const r = document.querySelector('.rava'); if (r) r.src = url;
}

/* ---------- график рейтинга (SVG без библиотек) ---------- */
const POOLS = { bullet: 'Пуля', blitz: 'Блиц', rapid: 'Рапид', classical: 'Классика', puzzle: 'Задачи' };
let hist = {};
try { hist = JSON.parse(($('#histdata') || {}).textContent || '{}'); } catch (e) { hist = {}; }
const NS = 'http://www.w3.org/2000/svg';
function el(tag, attrs, text) { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; return e; }
function chart(pool) {
  const svg = $('#chart'); if (!svg) return;
  svg.textContent = '';
  document.querySelectorAll('.pool').forEach(b => b.classList.toggle('on', b.dataset.pool === pool));
  const pts = (hist[pool] || []).slice(-200);
  // ширина в «пикселях экрана», чтобы подписи не становились крошечными на телефоне
  const W = Math.max(280, Math.round(svg.getBoundingClientRect().width) || 600), H = 190, L = 44, R = 10, T = 12, B = 26;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  if (pts.length < 2) {
    svg.appendChild(el('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle' }, pts.length ? 'Сыграй ещё хотя бы одну партию — появится график' : `В разделе «${POOLS[pool]}» пока нет партий`));
    $('#charthint').textContent = ''; return;
  }
  const vals = pts.map(p => p[1]); let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi - lo < 40) { lo -= 20; hi += 20; }
  const x = i => L + (W - L - R) * i / (pts.length - 1), y = v => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  for (let k = 0; k <= 3; k++) {
    const v = Math.round(lo + (hi - lo) * k / 3), yy = y(v);
    svg.appendChild(el('line', { x1: L, x2: W - R, y1: yy, y2: yy, class: 'grid' }));
    svg.appendChild(el('text', { x: L - 6, y: yy + 4, 'text-anchor': 'end' }, v));
  }
  const fmt = t => new Date(t * 1000).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
  svg.appendChild(el('text', { x: L, y: H - 6 }, fmt(pts[0][0])));
  svg.appendChild(el('text', { x: W - R, y: H - 6, 'text-anchor': 'end' }, fmt(pts[pts.length - 1][0])));
  svg.appendChild(el('polyline', { points: pts.map((p, i) => `${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join(' '), class: 'ln' }));
  const last = pts.length - 1; svg.appendChild(el('circle', { cx: x(last), cy: y(pts[last][1]), r: 4, class: 'pt' }));
  $('#charthint').textContent = `${POOLS[pool]}: от ${Math.min(...vals)} до ${Math.max(...vals)}, сейчас ${vals[last]} (${pts.length} изм.)`;
}
const poolsBox = $('#pools');
if (poolsBox) {
  poolsBox.addEventListener('click', e => { const b = e.target.closest('.pool'); if (b) chart(b.dataset.pool); });
  const best = Object.keys(POOLS).sort((a, b) => (hist[b] || []).length - (hist[a] || []).length)[0];
  chart(best || 'blitz');
}

if (!$('#avabtn')) return;    // чужой профиль — дальше только смена своей аватарки
$('#avabtn').onclick = () => $('#avapick').classList.toggle('hidden');

// готовые аватарки
document.querySelector('.avagrid').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  fetch('/api/avatar', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify({ preset: b.dataset.p }) })
    .then(r => r.json()).then(d => {
      if (!d.ok) { msg('Не получилось сохранить'); return; }
      document.querySelectorAll('.avagrid button').forEach(x => x.classList.toggle('on', x === b));
      setAva(d.url); msg('Аватарка сохранена ✔');
    }).catch(() => msg('Нет связи с сервером'));
});

// своя картинка: обрезаем по центру в квадрат 256×256 и сжимаем в JPEG прямо в браузере
$('#avafile').addEventListener('change', e => {
  const f = e.target.files[0]; e.target.value = ''; if (!f) return;
  if (!/^image\//.test(f.type)) { msg('Это не картинка'); return; }
  if (f.size > 20 * 1024 * 1024) { msg('Файл слишком большой (больше 20 МБ)'); return; }
  msg('Загружаю…');
  const url = URL.createObjectURL(f), img = new Image();
  img.onload = () => {
    URL.revokeObjectURL(url);
    const S = 256, c = document.createElement('canvas'); c.width = c.height = S;
    const side = Math.min(img.naturalWidth, img.naturalHeight);
    const sx = (img.naturalWidth - side) / 2, sy = (img.naturalHeight - side) / 2;
    const ctx = c.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, S, S);
    ctx.drawImage(img, sx, sy, side, side, 0, 0, S, S);
    c.toBlob(blob => {
      if (!blob) { msg('Не получилось обработать картинку'); return; }
      fetch('/api/avatar-upload', { method: 'POST', headers: { 'Content-Type': 'image/jpeg', 'X-CSRF-Token': csrf }, body: blob })
        .then(r => r.json()).then(d => {
          if (!d.ok) { msg(d.error || 'Не получилось загрузить'); return; }
          document.querySelectorAll('.avagrid button').forEach(x => x.classList.remove('on'));
          setAva(d.url); msg('Аватарка загружена ✔');
        }).catch(() => msg('Нет связи с сервером'));
    }, 'image/jpeg', 0.86);
  };
  img.onerror = () => { URL.revokeObjectURL(url); msg('Не получилось открыть картинку'); };
  img.src = url;
});
})();
