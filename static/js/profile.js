(function () {
'use strict';
const $ = s => document.querySelector(s);
const csrf = document.querySelector('meta[name=csrf]').content;
const msg = t => { $('#avamsg').textContent = t; };
function setAva(url) {
  $('#avaimg').src = url;
  const r = document.querySelector('.rava'); if (r) r.src = url;
}
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
