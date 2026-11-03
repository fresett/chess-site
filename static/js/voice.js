(function (root) {
'use strict';
// Голос тренера Альберта — встроенный в браузер синтез речи (Web Speech API).
// Лучше всего звучат нейросетевые голоса: в Edge — «Microsoft Dmitry Online (Natural)» (мужской),
// в Chrome — «Google русский». Голос можно выбрать вручную в окне «Дизайн».
const synth = root.speechSynthesis;
let on = true, chosen = '', voice = null;
try { on = localStorage.getItem('albertVoice') !== '0'; chosen = localStorage.getItem('albertVoiceName') || ''; } catch (e) {}
const MALE = /dmitr|дмитр|pavel|павел|maxim|максим|yuri|юрий|\bmale/i;
const NEURAL = /natural|online|neural|google/i;
// чем выше — тем лучше голос подходит Альберту
const rank = v => (NEURAL.test(v.name) ? 10 : 0) + (MALE.test(v.name) ? 5 : 0) + (/natural/i.test(v.name) ? 3 : 0);
function ruVoices() { return synth ? synth.getVoices().filter(v => /^ru/i.test(v.lang)).sort((a, b) => rank(b) - rank(a)) : []; }
function pick() {
  const list = ruVoices();
  voice = list.find(v => v.name === chosen) || list[0] || null;
}
if (synth) { pick(); synth.addEventListener('voiceschanged', pick); }

// Ходы в нотации (Bxg5, O-O, e8=Q+) превращаем в слова, чтобы голос не читал их по буквам.
const PIECE = { K: 'король', Q: 'ферзь', R: 'ладья', B: 'слон', N: 'конь' };
const FILE = { a: 'а', b: 'бэ', c: 'цэ', d: 'дэ', e: 'е', f: 'эф', g: 'жэ', h: 'аш' };
const PROMO = { Q: 'ферзя', R: 'ладью', B: 'слона', N: 'коня' };
function sanToWords(san) {
  let tail = '';
  if (san.endsWith('#')) { tail = ', мат'; san = san.slice(0, -1); } else if (san.endsWith('+')) { tail = ', шах'; san = san.slice(0, -1); }
  if (san === 'O-O-O') return 'длинная рокировка' + tail;
  if (san === 'O-O') return 'короткая рокировка' + tail;
  const m = san.match(/^([KQRBN])?([a-h])?([1-8])?(x)?([a-h])([1-8])(?:=([QRBN]))?$/); if (!m) return san;
  let w = m[1] ? PIECE[m[1]] : 'пешка';
  if (m[4]) w += ' берёт';
  w += ' ' + FILE[m[5]] + ' ' + m[6];
  if (m[7]) w += ' с превращением в ' + PROMO[m[7]];
  return w + tail;
}
function speechText(t) {
  return t.replace(/\b(O-O-O|O-O|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?)(?![\w])/g, s => sanToWords(s))
          .replace(/[«»"]/g, '')
          .replace(/[^\p{L}\p{N}\s.,!?:;—–-]/gu, ' ').replace(/\s+/g, ' ').trim();
}
function speak(text) {
  if (!synth || !text) return;
  synth.cancel();
  if (!voice) pick();
  // по предложениям: так интонация естественнее и между фразами есть паузы
  const parts = speechText(text).match(/[^.!?]+[.!?]*/g) || [];
  parts.forEach(p => {
    const u = new SpeechSynthesisUtterance(p.trim());
    u.lang = 'ru-RU'; if (voice) u.voice = voice;
    const neural = voice && NEURAL.test(voice.name);
    u.rate = neural ? 0.98 : 0.92;          // чуть медленнее — спокойная манера тренера
    u.pitch = voice && !MALE.test(voice.name) && !neural ? 0.85 : 1;
    u.volume = 1;
    synth.speak(u);
  });
}
root.Albert = {
  supported: !!synth,
  on: () => on && !!synth,
  toggle() { on = !on; try { localStorage.setItem('albertVoice', on ? '1' : '0'); } catch (e) {} if (!on) this.stop(); return on; },
  stop() { if (synth) synth.cancel(); },
  say(text) { if (on) speak(text); },
  sample() { speak('Привет! Я тренер Альберт. Конь эф 3 — отличный ход, развиваешь фигуры.'); },
  voices: () => ruVoices().map(v => ({ name: v.name, label: (NEURAL.test(v.name) ? '★ ' : '') + v.name.replace(/^Microsoft |^Google /, '') + (MALE.test(v.name) ? ' (муж.)' : '') })),
  voiceName: () => (voice ? voice.name : ''),
  setVoice(name) { chosen = name; try { localStorage.setItem('albertVoiceName', name); } catch (e) {} pick(); },
  speechText,
};
})(this);
