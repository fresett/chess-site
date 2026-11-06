(function () {
'use strict';
// Страница вызова: создатель ждёт, пока вызов примут; соперник — принимает или отклоняет.
const $ = s => document.querySelector(s);
const csrf = document.querySelector('meta[name=csrf]').content;
const box = $('#chpage'), id = box.dataset.id;
const post = (a) => fetch(`/api/online/challenge/${id}/${a}`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: '{}' }).then(r => r.json());
const msg = t => { $('#chmsg').textContent = t; };
if ($('#copy')) $('#copy').onclick = () => {
  const i = $('#chlink'); i.select();
  (navigator.clipboard ? navigator.clipboard.writeText(i.value) : Promise.reject()).then(() => msg('✔ Ссылка скопирована'), () => { document.execCommand('copy'); msg('✔ Ссылка скопирована'); });
};
if (box.dataset.mine === '1' && box.dataset.status === 'open') {
  const t = setInterval(() => fetch(`/api/online/challenge/${id}`).then(r => r.json()).then(d => {
    if (d.game) { clearInterval(t); if (window.Sfx) Sfx.play('notify'); location.href = '/online/game/' + d.game; }
    else if (d.status !== 'open') { clearInterval(t); location.reload(); }
  }).catch(() => {}), 1500);
}
if ($('#chcancel')) $('#chcancel').onclick = () => post('cancel').then(() => location.reload());
if ($('#chaccept')) $('#chaccept').onclick = () => post('accept').then(d => {
  if (d.game) location.href = '/online/game/' + d.game; else msg('⚠ ' + (d.error || 'Не получилось'));
}).catch(() => msg('Нет связи с сервером'));
if ($('#chdecline')) $('#chdecline').onclick = () => post('decline').then(() => location.reload());
})();
