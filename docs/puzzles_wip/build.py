"""Собрать ladya/puzzles.py из проверенных задач (*.out.json)."""
import glob
import json
import sys

out_path = sys.argv[1]
items = []
for f in sorted(glob.glob("*.out.json")):
    items += json.load(open(f, encoding="utf-8"))
seen, uniq = set(), []
for p in items:
    if p["title"] in seen:
        continue
    seen.add(p["title"])
    uniq.append(p)
uniq.sort(key=lambda p: (p["rating"], p["title"]))

lines = ['"""Задачи «Ладьи». Сгенерировано и проверено скриптом на python-chess:',
         'решение работает, первый ход единственный, ответы соперника — лучшая защита.',
         '',
         'sol — вся линия в UCI: ход игрока, ответ соперника, ход игрока… (в матовых задачах последний ход —',
         'любой мат). rating — сложность для рейтинга в задачах."""',
         '',
         'THEMES = {',
         '    "mate1": ("Мат в 1 ход", "♚", "mate"), "mate2": ("Мат в 2 хода", "♛", "mate"),',
         '    "mate3": ("Мат в 3+ хода", "👑", "mate"), "fork": ("Вилка", "🍴", "tactic"),',
         '    "pin": ("Связка", "📌", "tactic"), "skewer": ("Сквозной удар", "🎯", "tactic"),',
         '    "discovered": ("Вскрытое нападение", "⚡", "tactic"), "promotion": ("Превращение", "⬆", "tactic"),',
         '    "opening": ("Дебютная ловушка", "🪤", "opening"), "endgame": ("Эндшпиль", "♙", "endgame"),',
         '}',
         '',
         'PUZZLES = [']
for i, p in enumerate(uniq, 1):
    d = {"id": i, "title": p["title"], "theme": p["theme"], "kind": p["kind"], "n": p["n"], "rating": p["rating"],
         "fen": p["fen"], "sol": p["sol"], "san": p["san"], "idea": p["idea"], "hint": p["hint"]}
    lines.append("    " + json.dumps(d, ensure_ascii=False) + ",")
lines += [']', '', '',
          'def _goal(p):',
          '    side = "Белые" if " w " in p["fen"] else "Чёрные"',
          '    if p["kind"] == "mate":',
          '        n = p["n"]',
          '        return f"{side}: мат в {n} " + ("ход" if n == 1 else "хода" if n < 5 else "ходов")',
          '    return f"{side}: выиграй материал"',
          '',
          '',
          'def _line(p):',
          '    w = " w " in p["fen"]',
          '    out, num = [], int(p["fen"].split()[-1])',
          '    for k, s in enumerate(p["san"]):',
          '        white_moves = (k % 2 == 0) == w',
          '        if white_moves:',
          '            out.append(f"{num}. {s}")',
          '        else:',
          '            out.append(f"{num}… {s}" if k == 0 else s)',
          '            num += 1',
          '    return " ".join(out)',
          '',
          '',
          'for _p in PUZZLES:',
          '    _name, _icon, _group = THEMES[_p["theme"]]',
          '    _p.update(themeName=_name, icon=_icon, group=_group, goal=_goal(_p), line=_line(_p))',
          'BY_ID = {p["id"]: p for p in PUZZLES}',
          '']
open(out_path, "w", encoding="utf-8").write("\n".join(lines))
print(f"{len(uniq)} задач записано в {out_path}")
from collections import Counter  # noqa: E402
print(Counter(p["theme"] for p in uniq))
