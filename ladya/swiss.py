"""Швейцарская система: жеребьёвка тура и турнирная таблица с тай-брейками.

Модуль не знает про базу данных и Flask — только чистые функции, поэтому его легко проверять тестами.

Жеребьёвка (упрощённая голландская система, близкая к правилам FIDE C.04.3):
  * игроки упорядочиваются по очкам, затем по рейтингу;
  * при нечётном числе игроков свободный тур (bye) получает самый нижний игрок, у которого bye ещё не было
    (и при котором остальные могут быть спарены без повторов);
  * пары подбираются поиском с возвратом: повторные встречи запрещены; внутри очковой группы
    верхняя половина играет с нижней (1-й с n/2+1-м и т. д.), игроки с несовместимыми цветами
    (оба должны играть одним цветом) разводятся; если в группе не хватает соперников — игрок «спускается»
    в следующую очковую группу;
  * если жёсткие условия выполнить нельзя, они ослабляются по очереди: сначала цвета, потом — только
    в крайнем случае (маленький турнир, много туров) — разрешаются повторные пары.
Цвета: выравнивается баланс белых/чёрных, никто не играет одним цветом три раза подряд, при прочих
равных игрок получает цвет, противоположный предыдущему.

Тай-брейки (в порядке применения): очки → усечённый Бухгольц (без худшего соперника) → Бухгольц →
Бергер (Зоннеборн — Бергер) → число побед → рейтинг.
Несыгранные туры (bye, неявка, пропуск) считаются как партия с «виртуальным соперником», у которого
столько же очков, сколько у самого игрока (правило FIDE 2023 для тай-брейков).
"""

POINTS = {  # result -> (очки белых, очки чёрных)
    "1-0": (1.0, 0.0), "0-1": (0.0, 1.0), "1/2": (0.5, 0.5),
    "+-": (1.0, 0.0), "-+": (0.0, 1.0), "--": (0.0, 0.0),
}
PLAYED = {"1-0", "0-1", "1/2"}            # реально сыгранные партии (неявки — нет)
RESULT_LABEL = {"1-0": "1–0", "0-1": "0–1", "1/2": "½–½", "+-": "+ / −", "-+": "− / +", "--": "− / −",
                "bye": "bye"}
MAX_NODES = 200_000


# ------------------------------------------------------------------ цвета
def color_pref(colors):
    """Предпочтение цвета по истории ('w'/'b' за сыгранные партии). Возвращает (цвет|None, сила 0..3)."""
    if not colors:
        return None, 0
    diff = colors.count("w") - colors.count("b")
    last2 = colors[-2:]
    if diff <= -2 or last2 == ["b", "b"]:
        return "w", 3
    if diff >= 2 or last2 == ["w", "w"]:
        return "b", 3
    if diff == -1:
        return "w", 2
    if diff == 1:
        return "b", 2
    return ("b" if colors[-1] == "w" else "w"), 1


def absolute_conflict(a, b):
    pa, sa = color_pref(a["colors"])
    pb, sb = color_pref(b["colors"])
    return sa == 3 and sb == 3 and pa == pb


def soft_conflict(a, b):
    pa, sa = color_pref(a["colors"])
    pb, sb = color_pref(b["colors"])
    return sa >= 2 and sb >= 2 and pa == pb


def allocate_colors(a, b, board_no):
    """a — игрок с более высоким местом в посеве. Возвращает (белые, чёрные)."""
    pa, sa = color_pref(a["colors"])
    pb, sb = color_pref(b["colors"])
    if pa is None and pb is None:
        return (a, b) if board_no % 2 == 1 else (b, a)          # 1-й тур: на нечётных досках верхний — белыми
    if pa and pb and pa != pb:
        return (a, b) if pa == "w" else (b, a)
    if pa is None:
        return (b, a) if pb == "w" else (a, b)
    if pb is None:
        return (a, b) if pa == "w" else (b, a)
    # оба хотят один цвет — получает тот, у кого предпочтение сильнее; при равенстве — тот, кто выше в посеве,
    # но если у них разная история цветов, решает последний тур, где цвета различались
    if sa != sb:
        winner = a if sa > sb else b
    else:
        winner = a
        for ca, cb in zip(reversed(a["colors"]), reversed(b["colors"])):
            if ca != cb:
                # тот, кто в последний раз играл «не тем» цветом, получает желаемый
                winner = a if ca != pa else b
                break
    loser = b if winner is a else a
    return (winner, loser) if pa == "w" else (loser, winner)


# ------------------------------------------------------------------ жеребьёвка
def _rank_key(p):
    return (-p["score"], -p.get("rating", 0), p["id"])


def _candidates(p, rest, colors_mode):
    """Порядок, в котором пробуем соперников для p (p — верхний из неспаренных)."""
    group = [q for q in rest if q["score"] == p["score"]]
    n = len(group) + 1                                   # группа вместе с p
    half = n // 2
    order = {}
    # голландская схема: верхний игрок группы — с первым игроком нижней половины, далее по порядку
    s2 = group[half - 1:] if half >= 1 else group
    s1 = list(reversed(group[:half - 1])) if half >= 2 else []
    for i, q in enumerate(s2 + s1):
        order[q["id"]] = i
    lower = [q for q in rest if q["score"] != p["score"]]

    def key(q):
        same = q["score"] == p["score"]
        cc = soft_conflict(p, q) if colors_mode >= 1 else False
        return (q["id"] in p["opps"], abs(q["score"] - p["score"]), 0 if same else 1, cc, order.get(q["id"], 0) if same else 0,
                _rank_key(q))
    return sorted(group, key=key) + sorted(lower, key=key)


def _search(players, allow_rematch, strict_colors, budget, accept=None, acc=()):
    """Поиск с возвратом. players — в порядке посева. Возвращает список пар или None.
    accept(пары) — дополнительная проверка готового варианта (просмотр на тур вперёд)."""
    if not players:
        return [] if accept is None or accept(acc) else None
    p, rest = players[0], players[1:]
    for q in _candidates(p, rest, 1):
        budget[0] -= 1
        if budget[0] < 0:
            return None
        if not allow_rematch and q["id"] in p["opps"]:
            continue
        if strict_colors and absolute_conflict(p, q):
            continue
        sub = _search([x for x in rest if x is not q], allow_rematch, strict_colors, budget, accept, acc + ((p, q),))
        if sub is not None:
            return [(p, q)] + sub
    return None


def _next_round_possible(players, pairs, bye):
    """Можно ли будет провести ещё один тур без повторных встреч, если сыграть эти пары."""
    new_opps = {p["id"]: set(p["opps"]) for p in players}
    for a, b in pairs:
        new_opps[a["id"]].add(b["id"])
        new_opps[b["id"]].add(a["id"])
    nxt = [{**p, "opps": new_opps[p["id"]], "byes": p["byes"] + (1 if bye is p else 0)} for p in players]
    nxt.sort(key=_rank_key)
    cands = [None]
    if len(nxt) % 2:
        cands = sorted([p for p in nxt if p["byes"] == 0], key=lambda p: _rank_key(p), reverse=True) or [None]
    for c in cands:
        rest = [x for x in nxt if x is not c]
        if len(rest) % 2:
            continue
        if _search(rest, False, False, [3000]) is not None:
            return True
    return False


def _pair_all(players, everyone=None, bye=None, lookahead=False):
    everyone = everyone or players
    accept = None
    if lookahead and len(everyone) <= 40:
        accept = lambda pairs: _next_round_possible(everyone, pairs, bye)  # noqa: E731
    for allow_rematch, strict in ((False, True), (False, False), (True, False)):
        if accept:
            res = _search(players, allow_rematch, strict, [MAX_NODES // 4], accept)
            if res is not None:
                return res
        res = _search(players, allow_rematch, strict, [MAX_NODES])
        if res is not None:
            return res
    return None


def pair_round(players, round_no=1, rounds_left=0):
    """players: [{id, rating, score, opps:set, colors:[...], byes:int}] — только активные игроки.
    rounds_left — сколько туров останется после этого: если > 0, жеребьёвка выбирает такие пары,
    после которых следующий тур тоже можно провести без повторных встреч.
    Возвращает (pairs, bye_id): pairs — [(white_id, black_id)] в порядке досок."""
    ps = sorted(players, key=_rank_key)
    for p in ps:
        p.setdefault("opps", set())
        p.setdefault("colors", [])
        p.setdefault("byes", 0)
    everyone = list(ps)
    bye = None
    lookahead = rounds_left > 0
    if len(ps) % 2 == 1:
        # снизу вверх: первый, у кого не было bye и без кого остальные спариваются без повторов
        order = sorted(ps, key=lambda p: (p["byes"], [-x for x in _rank_key(p)[:2]], -p["id"]))
        chosen = None
        for cand in order:
            rest = [x for x in ps if x is not cand]
            acc = (lambda pairs, c=cand: _next_round_possible(everyone, pairs, c)) if lookahead else None
            if _search(rest, False, False, [MAX_NODES // 4], acc) is not None:
                chosen = cand
                break
        if chosen is None:
            for cand in order:
                if _search([x for x in ps if x is not cand], False, False, [MAX_NODES // 4]) is not None:
                    chosen = cand
                    break
        bye = chosen or order[0]
        ps = [x for x in ps if x is not bye]
    raw = _pair_all(ps, everyone, bye, lookahead) or []
    raw.sort(key=lambda ab: (-max(ab[0]["score"], ab[1]["score"]), -(ab[0]["score"] + ab[1]["score"]),
                             min(ps.index(ab[0]), ps.index(ab[1]))))
    pairs = []
    for i, (a, b) in enumerate(raw, 1):
        hi, lo = (a, b) if ps.index(a) < ps.index(b) else (b, a)
        w, bl = allocate_colors(hi, lo, i)
        pairs.append((w["id"], bl["id"]))
    return pairs, (bye["id"] if bye else None)


# ------------------------------------------------------------------ таблица
def standings(players, pairings, bye_points=1.0, rounds_done=None):
    """players: [{id, name, rating, withdrawn, joined_round}];
    pairings: [{round, white, black|None, result}] (black=None — bye, result='bye').
    rounds_done — сколько туров учитывать в тай-брейках (по умолчанию — максимальный тур с результатами).
    Возвращает список строк таблицы, отсортированный по местам."""
    ids = {p["id"] for p in players}
    pts = {i: 0.0 for i in ids}
    wins = {i: 0 for i in ids}
    games = {i: {} for i in ids}          # round -> (opp|None, очки за тур, сыграна ли)
    colors = {i: [] for i in ids}
    if rounds_done is None:
        rounds_done = max([x["round"] for x in pairings if x.get("result")] or [0])
    for x in pairings:
        r, w, b, res = x["round"], x["white"], x.get("black"), x.get("result")
        if not res or w not in ids:
            continue
        if b is None:
            sw = bye_points if res == "bye" else 0.0
            pts[w] += sw
            games[w][r] = (None, sw, False)
            continue
        sw, sb = POINTS.get(res, (0.0, 0.0))
        played = res in PLAYED
        pts[w] += sw
        pts[b] += sb
        if sw == 1:
            wins[w] += 1
        if sb == 1:
            wins[b] += 1
        games[w][r] = (b, sw, played)
        games[b][r] = (w, sb, played)
        if played:
            colors[w].append("w")
            colors[b].append("b")

    rows = []
    for p in players:
        i = p["id"]
        contrib, sb = [], 0.0
        for r in range(1, rounds_done + 1):
            opp, got, played = games[i].get(r, (None, 0.0, False))
            if played:
                o = pts[opp]
            else:
                o = pts[i]                     # виртуальный соперник с очками самого игрока
            contrib.append(o)
            sb += o * got
        bh = sum(contrib)
        bh1 = bh - min(contrib) if len(contrib) > 1 else bh
        rows.append({**p, "points": pts[i], "bh": bh, "bh1": bh1, "sb": sb, "wins": wins[i],
                     "games": games[i], "colors": colors[i]})
    rows.sort(key=lambda x: (-x["points"], -x["bh1"], -x["bh"], -x["sb"], -x["wins"], -x.get("rating", 0), x["id"]))
    for n, row in enumerate(rows, 1):
        row["place"] = n
    return rows


def history(players, pairings):
    """Для жеребьёвки: очки, соперники, цвета и число bye у каждого игрока по сыгранным турам."""
    info = {p["id"]: {"id": p["id"], "rating": p.get("rating", 0), "score": 0.0, "opps": set(), "colors": [],
                      "byes": 0} for p in players}
    for x in sorted(pairings, key=lambda x: x["round"]):
        w, b, res = x["white"], x.get("black"), x.get("result")
        if b is None:
            if w in info:
                info[w]["byes"] += 1
                if res == "bye":
                    info[w]["score"] += x.get("bye_points", 1.0)
            continue
        if w in info and b is not None:
            info[w]["opps"].add(b)
        if b in info:
            info[b]["opps"].add(w)
        if not res:
            continue
        sw, sb = POINTS.get(res, (0.0, 0.0))
        if w in info:
            info[w]["score"] += sw
        if b in info:
            info[b]["score"] += sb
        if res in PLAYED:
            if w in info:
                info[w]["colors"].append("w")
            if b in info:
                info[b]["colors"].append("b")
    return info


# ------------------------------------------------------------------ круговая система
def round_robin(ids):
    """Круговой турнир (метод «карусели», таблицы Бергера): все со всеми.
    Возвращает список туров, каждый тур — [(white_id, black_id|None)]; None — свободный тур."""
    arr = list(ids)
    if len(arr) % 2:
        arr.append(None)
    n = len(arr)
    rounds = []
    for r in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = arr[i], arr[n - 1 - i]
            if i == 0:
                a, b = (a, b) if r % 2 == 0 else (b, a)
            elif i % 2 == 1:
                a, b = b, a
            if a is None:
                a, b = b, None
            pairs.append((a, b))
        # пары со свободным туром — в конец
        pairs.sort(key=lambda x: x[1] is None)
        rounds.append(pairs)
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]
    return rounds
