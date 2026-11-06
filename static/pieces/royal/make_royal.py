"""Генератор набора фигур «Royal»: слоновая кость / обсидиан с золотой окантовкой.
Запуск: python static/pieces/royal/make_royal.py — перезапишет 12 SVG рядом с этим файлом."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

COLORS = {
    "w": dict(hi="#fffdf6", lo="#d8c7a4", line="#3b2f1e", gold="#b8892e", shine="#ffffff"),
    "b": dict(hi="#4a5263", lo="#0d1017", line="#06080c", gold="#e0b456", shine="#8d97ab"),
}

# Общая подставка: широкий цоколь + золотой поясок
BASE = ('<path class="p" d="M20 90h60c2 0 3-1 3-3v-3c0-2-1-3-3-3H20c-2 0-3 1-3 3v3c0 2 1 3 3 3z"/>'
        '<path class="p" d="M27 81h46l-3-6H30z"/><path class="g" d="M29 77.5h42"/>')

SHAPES = {
    "p": ('<path class="p" d="M36 75c1-10 6-17 10-21h8c4 4 9 11 10 21z"/>'
          '<path class="p" d="M39 55h22c1.5 0 2.5-1 2.5-2.5S62.5 50 61 50H39c-1.5 0-2.5 1-2.5 2.5S37.5 55 39 55z"/>'
          '<circle class="p" cx="50" cy="36" r="13"/><path class="s" d="M44 30a8 8 0 0 1 7-5"/>'),
    "r": ('<path class="p" d="M34 75l3-34h26l3 34z"/>'
          '<path class="p" d="M31 41h38v-5H31z"/>'
          '<path class="p" d="M30 36V20h8v6h7v-6h10v6h7v-6h8v16z"/>'
          '<path class="g" d="M36 58h28"/><path class="s" d="M40 46v24"/>'),
    "n": ('<path class="p" d="M33 75c0-11 3-18 12-25-6 1-12 4-16 9-5-1-7-6-3-12l11-14c2-8 7-15 15-18l1-7 6 5c15 3 23 18 22 36-1 10-2 19-2 26z"/>'
          '<circle class="e" cx="52" cy="27" r="2.6"/>'
          '<path class="g" d="M63 15c10 7 12 22 8 38"/><path class="s" d="M44 22c3-4 6-7 10-8"/>'),
    "b": ('<path class="p" d="M37 75c1-9 4-15 8-19h10c4 4 7 10 8 19z"/>'
          '<path class="p" d="M37 56h26v-5H37z"/>'
          '<path class="p" d="M50 14c-11 9-15 18-15 25 0 7 6 12 15 12s15-5 15-12c0-7-4-16-15-25z"/>'
          '<circle class="p" cx="50" cy="11" r="4.5"/><path class="g" d="M55 25L47 37"/>'
          '<path class="s" d="M41 38c0-6 3-12 7-17"/>'),
    "q": ('<path class="p" d="M33 75c2-11 6-19 10-24h14c4 5 8 13 10 24z"/>'
          '<path class="p" d="M35 51h30l4-6H31z"/>'
          '<path class="p" d="M31 45l-6-24 13 13 5-20 7 18 7-18 5 20 13-13-6 24z"/>'
          '<circle class="g2" cx="25" cy="19" r="3.5"/><circle class="g2" cx="43" cy="12" r="3.5"/>'
          '<circle class="g2" cx="57" cy="12" r="3.5"/><circle class="g2" cx="75" cy="19" r="3.5"/>'
          '<circle class="g2" cx="50" cy="28" r="3"/><path class="s" d="M39 70c1-7 3-12 6-16"/>'),
    "k": ('<path class="p" d="M33 75c2-11 6-19 10-24h14c4 5 8 13 10 24z"/>'
          '<path class="p" d="M34 51h32l3-6H31z"/>'
          '<path class="p" d="M31 45c-4-10-3-20 6-22 6-1 10 3 13 8 3-5 7-9 13-8 9 2 10 12 6 22z"/>'
          '<path class="g" d="M50 31V9M43 15h14" style="stroke-width:5"/>'
          '<path class="s" d="M37 40c-2-5-1-10 3-11"/>'),
}

TPL = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
       '<defs><linearGradient id="f" x1="0" y1="0" x2=".9" y2="1">'
       '<stop offset="0" stop-color="{hi}"/><stop offset="1" stop-color="{lo}"/></linearGradient></defs>'
       '<style>.p{{fill:url(#f);stroke:{line};stroke-width:2.4;stroke-linejoin:round}}'
       '.g{{fill:none;stroke:{gold};stroke-width:2.6;stroke-linecap:round}}'
       '.g2{{fill:{gold};stroke:{line};stroke-width:1.6}}.e{{fill:{line}}}'
       '.s{{fill:none;stroke:{shine};stroke-width:2;stroke-linecap:round;opacity:.55}}</style>'
       '{body}</svg>')

for color, c in COLORS.items():
    for code, shape in SHAPES.items():
        with open(os.path.join(HERE, f"{color}{code}.svg"), "w", encoding="utf-8") as f:
            f.write(TPL.format(body=BASE + shape, **c))
print("ok")
