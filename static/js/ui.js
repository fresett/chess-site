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
  if (!a || e.ctrlKey || e.metaKey || e.shiftKey || a.target || e.defaultPrevented) return;
  e.preventDefault(); document.body.classList.add('leaving');
  setTimeout(() => { location.href = a.href; }, 200);
});
window.addEventListener('pageshow', () => document.body.classList.remove('leaving'));
// «я на сайте»: раз в 25 секунд, пока вкладка открыта и видна (для счётчика онлайна)
setInterval(() => { if (!document.hidden) fetch('/api/ping').catch(() => {}); }, 25000);
})();
