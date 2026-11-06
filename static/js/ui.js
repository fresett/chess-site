(function () {
const sb = document.getElementById('sound');
function lab() {
  if (!sb) return;
  sb.querySelector('span').textContent = Sfx.muted() ? 'Звук выкл.' : 'Звук вкл.';
  sb.querySelector('use').setAttribute('href', Sfx.muted() ? '#i-mute' : '#i-sound');
}
if (sb) { sb.addEventListener('click', () => { Sfx.toggle(); lab(); Sfx.play('move'); }); lab(); }
document.addEventListener('click', e => {
  const a = e.target.closest('a[href^="/"]');
  if (!a || a.hasAttribute('download') || e.ctrlKey || e.metaKey || e.shiftKey || a.target || e.defaultPrevented) return;
  e.preventDefault(); document.body.classList.add('leaving');
  setTimeout(() => { location.href = a.href; }, 200);
});
window.addEventListener('pageshow', () => document.body.classList.remove('leaving'));
// «я на сайте»: раз в 25 секунд, пока вкладка открыта и видна (для счётчика онлайна)
setInterval(() => { if (!document.hidden) fetch('/api/ping').catch(() => {}); }, 25000);

// телефон: кнопка «Ещё» раскрывает остальные пункты меню
const more = document.getElementById('morebtn'), rail = document.getElementById('rail');
if (more && rail) more.addEventListener('click', () => { const o = rail.classList.toggle('open'); more.setAttribute('aria-expanded', o ? 'true' : 'false'); });

// уведомления: счётчик на колокольчике и всплывашка о новом
const bell = document.getElementById('bellcount');
let lastNotif = null;
function checkNotif() {
  if (!bell || document.hidden) return;
  fetch('/api/notifications', { cache: 'no-store' }).then(r => r.json()).then(d => {
    bell.textContent = d.unread; bell.classList.toggle('hidden', !d.unread);
    const top = d.items && d.items[0];
    if (top && lastNotif !== null && top.id > lastNotif && !top.read) toast(top);
    if (top) lastNotif = Math.max(lastNotif || 0, top.id); else if (lastNotif === null) lastNotif = 0;
  }).catch(() => {});
}
function toast(n) {
  const t = document.createElement(n.url ? 'a' : 'div');
  t.className = 'toast'; t.textContent = '🔔 ' + n.text; if (n.url) t.href = n.url;
  document.body.appendChild(t); if (window.Sfx) Sfx.play('notify');
  setTimeout(() => t.classList.add('out'), 7000); setTimeout(() => t.remove(), 7600);
}
if (bell) { checkNotif(); setInterval(checkNotif, 20000); }

// кнопки «Копировать» (data-copy="#id поля")
document.addEventListener('click', e => {
  const b = e.target.closest('[data-copy]'); if (!b) return;
  const i = document.querySelector(b.dataset.copy); if (!i) return;
  i.select();
  const done = () => { const o = b.textContent; b.textContent = '✔ Скопировано'; setTimeout(() => { b.textContent = o; }, 1500); };
  if (navigator.clipboard) navigator.clipboard.writeText(i.value).then(done, () => { document.execCommand('copy'); done(); });
  else { document.execCommand('copy'); done(); }
});
// выпадающий список, который сразу отправляет форму (результат партии в турнире)
document.addEventListener('change', e => {
  const s = e.target.closest('select[data-autosubmit]'); if (s && s.form) s.form.submit();
});
// подсказка ников (поле с data-nicks="#datalist")
document.addEventListener('input', e => {
  const i = e.target.closest('input[data-nicks]'); if (!i) return;
  const q = i.value.trim(); if (q.length < 2) return;
  clearTimeout(i._t);
  i._t = setTimeout(() => fetch('/api/users?q=' + encodeURIComponent(q)).then(r => r.json()).then(list => {
    const dl = document.querySelector(i.dataset.nicks); if (!dl || !Array.isArray(list)) return;
    dl.textContent = ''; list.forEach(n => { const o = document.createElement('option'); o.value = n; dl.appendChild(o); });
  }).catch(() => {}), 250);
});
// подтверждение опасных действий (форма с data-confirm)
document.addEventListener('submit', e => {
  const f = e.target.closest('form[data-confirm]');
  if (f && !confirm(f.dataset.confirm)) e.preventDefault();
});
// живая страница (турнир): перезагружается, когда на сервере что-то изменилось
const live = document.querySelector('[data-sig-url]');
if (live) {
  let sig = null;
  setInterval(() => {
    if (document.hidden || document.activeElement && /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
    if (document.querySelector('details[open].menu')) return;
    fetch(live.dataset.sigUrl, { cache: 'no-store' }).then(r => r.json()).then(d => {
      if (sig !== null && d.sig !== sig) location.reload();
      sig = d.sig;
    }).catch(() => {});
  }, 8000);
}
})();
// подсказки у значков меню (на невысоких экранах подписи скрыты)
document.querySelectorAll('.rail nav a, .rail-bottom a, .rail-bottom button').forEach(a => {
  const s = a.querySelector('span'); if (s && !a.title) a.title = s.textContent;
});
