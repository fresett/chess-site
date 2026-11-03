(function () {
'use strict';
// Раздел «Познания»: вкладки «Дебюты» / «Чемпионы мира» и биографии чемпионов.
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
let list = null;

function showTab(k) {
  document.querySelectorAll('#ktabs button').forEach(b => b.classList.toggle('on', b.dataset.k === k));
  $('#k-openings').classList.toggle('hidden', k !== 'openings');
  $('#k-champions').classList.toggle('hidden', k !== 'champions');
  try { history.replaceState(null, '', k === 'champions' ? '?tab=champions' : location.pathname); } catch (e) {}
  if (k === 'champions') load();
}
$('#ktabs').addEventListener('click', e => { const b = e.target.closest('button'); if (b) showTab(b.dataset.k); });

function load() {
  if (list) return;
  fetch('/static/champions/champions.json').then(r => r.json()).then(d => {
    list = d;
    $('#champs').innerHTML = d.map((c, i) => `
      <article class="champ">
        <div class="cph"><img loading="lazy" src="/static/champions/${esc(c.photo)}" alt="${esc(c.name)}">
          <button class="btn biobtn" data-i="${i}">📖 Биография</button></div>
        <div class="cinfo"><span class="bnum">${c.n}</span><b>${esc(c.name)}</b><small>${esc(c.years)}</small></div>
      </article>`).join('');
    document.querySelectorAll('.champ').forEach((el, i) => el.style.setProperty('--d', i * 40 + 'ms'));
  });
}
$('#champs').addEventListener('click', e => {
  const b = e.target.closest('.biobtn, .champ'); if (!b || !list) return;
  const i = b.classList.contains('biobtn') ? +b.dataset.i : +b.querySelector('.biobtn').dataset.i;
  openBio(list[i]);
});
function openBio(c) {
  $('#biophoto').src = '/static/champions/' + c.photo; $('#biophoto').alt = c.name;
  $('#bionum').textContent = c.n + '-й чемпион мира';
  $('#bioname').textContent = c.name;
  $('#bionick').textContent = c.nick;
  $('#biometa').textContent = `${c.life} · ${c.country} · чемпион: ${c.years}`;
  $('#biotext').innerHTML = c.bio.map(p => `<p>${esc(p)}</p>`).join('');
  $('#biosrc').textContent = c.credit; $('#biosrc').href = c.source;
  $('#bio').classList.remove('hidden');
}
const closeBio = () => $('#bio').classList.add('hidden');
$('#bioclose').onclick = closeBio;
$('#bio').addEventListener('click', e => { if (e.target.id === 'bio') closeBio(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeBio(); });

if (new URLSearchParams(location.search).get('tab') === 'champions') showTab('champions');
})();
