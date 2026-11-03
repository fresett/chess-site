"""Жеребьёвка швейцарской системы, круговая система и тай-брейки."""
import itertools
import random

import pytest

from ladya import swiss


def play_tournament(n, rounds, seed=0, results=("1-0", "0-1", "1/2")):
    rnd = random.Random(seed)
    players = [{"id": i, "rating": 2000 - i * 10} for i in range(1, n + 1)]
    pairings = []
    for r in range(1, rounds + 1):
        info = swiss.history(players, pairings)
        pairs, bye = swiss.pair_round(list(info.values()), r, rounds - r)
        assert len(pairs) * 2 + (1 if bye else 0) == n
        ids = [x for p in pairs for x in p] + ([bye] if bye else [])
        assert sorted(ids) == list(range(1, n + 1)), "каждый игрок ровно в одной паре"
        for w, b in pairs:
            pairings.append({"round": r, "white": w, "black": b, "result": rnd.choice(results)})
        if bye:
            pairings.append({"round": r, "white": bye, "black": None, "result": "bye"})
    return players, pairings


def test_round1_dutch_top_half_vs_bottom_half():
    players = [{"id": i, "rating": 2000 - i} for i in range(1, 9)]
    info = swiss.history(players, [])
    pairs, bye = swiss.pair_round(list(info.values()), 1)
    assert bye is None
    as_sets = [set(p) for p in pairs]
    for top, bottom in ((1, 5), (2, 6), (3, 7), (4, 8)):
        assert {top, bottom} in as_sets
    # цвета в 1-м туре чередуются по доскам: верхний игрок белыми на 1-й доске, чёрными на 2-й
    assert pairs[0] == (1, 5) and pairs[1] == (6, 2)


@pytest.mark.parametrize("n", [4, 5, 6, 7, 8, 9, 10, 12, 15, 16, 21, 32])
@pytest.mark.parametrize("seed", range(5))
def test_no_rematches_and_single_bye(n, seed):
    import math
    rounds = min(n - 2, math.ceil(math.log2(n)) + 1)
    players, pairings = play_tournament(n, rounds, seed)
    seen = set()
    for x in pairings:
        if x["black"] is None:
            continue
        key = frozenset((x["white"], x["black"]))
        assert key not in seen, f"повторная пара {sorted(key)}"
        seen.add(key)
    info = swiss.history(players, pairings)
    assert max(i["byes"] for i in info.values()) <= 1, "bye — не больше одного раза"
    for i in info.values():
        c = i["colors"]
        assert abs(c.count("w") - c.count("b")) <= 2
        assert not any(c[k] == c[k + 1] == c[k + 2] for k in range(len(c) - 2)), "три партии подряд одним цветом"


def test_bye_goes_to_lowest_without_bye():
    players = [{"id": i, "rating": 2000 - i} for i in range(1, 6)]
    info = swiss.history(players, [])
    _, bye = swiss.pair_round(list(info.values()), 1)
    assert bye == 5
    pairings = [{"round": 1, "white": 5, "black": None, "result": "bye"},
                {"round": 1, "white": 1, "black": 3, "result": "1-0"},
                {"round": 1, "white": 4, "black": 2, "result": "0-1"}]
    info = swiss.history(players, [dict(p, bye_points=1.0) for p in pairings])
    _, bye2 = swiss.pair_round(list(info.values()), 2)
    assert bye2 != 5 and bye2 in (3, 4)


def test_score_groups_paired_together():
    players, pairings = play_tournament(8, 2, seed=3, results=("1-0", "0-1"))
    info = swiss.history(players, pairings)
    pairs, _ = swiss.pair_round(list(info.values()), 3)
    # после двух туров без ничьих: 2 игрока с 2 очками, 4 с 1, 2 с 0 — лидеры играют друг с другом
    leaders = {i for i, v in info.items() if v["score"] == 2}
    if len(leaders) == 2 and frozenset(leaders) not in {frozenset((x["white"], x["black"])) for x in pairings}:
        assert tuple(sorted(leaders)) in [tuple(sorted(p)) for p in pairs]


def test_standings_tiebreaks_known_example():
    # 4 игрока, 3 тура (все со всеми): A выиграл всё, B 2, C 1, D 0
    P = [{"id": i, "name": n, "rating": 0} for i, n in enumerate("ABCD", 1)]
    res = [(1, 1, 2, "1-0"), (1, 3, 4, "1-0"), (2, 1, 3, "1-0"), (2, 2, 4, "1-0"), (3, 4, 1, "0-1"), (3, 2, 3, "1-0")]
    pairings = [{"round": r, "white": w, "black": b, "result": x} for r, w, b, x in res]
    st = swiss.standings(P, pairings)
    by = {r["name"]: r for r in st}
    assert [r["name"] for r in st] == ["A", "B", "C", "D"]
    assert by["A"]["points"] == 3 and by["B"]["points"] == 2
    assert by["A"]["bh"] == 2 + 1 + 0            # соперники: B(2), C(1), D(0)
    assert by["A"]["bh1"] == 3                   # без худшего (D=0)
    assert by["A"]["sb"] == 3                    # победы над всеми: 2+1+0
    assert by["B"]["sb"] == 1                    # победы над C(1) и D(0)


def test_tiebreak_orders_equal_points():
    # B и C по 1 очку, но B выиграл у более сильного соперника → выше по Бухгольцу/Бергеру
    P = [{"id": i, "name": n, "rating": 0} for i, n in enumerate("ABCD", 1)]
    pairings = [{"round": 1, "white": 1, "black": 2, "result": "1-0"}, {"round": 1, "white": 3, "black": 4, "result": "1-0"},
                {"round": 2, "white": 2, "black": 3, "result": "1-0"}, {"round": 2, "white": 4, "black": 1, "result": "0-1"}]
    st = swiss.standings(P, pairings)
    assert st[0]["name"] == "A"
    names = [r["name"] for r in st]
    assert names.index("B") < names.index("C")


def test_bye_virtual_opponent_and_points():
    P = [{"id": 1, "name": "A", "rating": 0}, {"id": 2, "name": "B", "rating": 0}, {"id": 3, "name": "C", "rating": 0}]
    pairings = [{"round": 1, "white": 1, "black": 2, "result": "1/2"}, {"round": 1, "white": 3, "black": None, "result": "bye"}]
    st = {r["name"]: r for r in swiss.standings(P, pairings, bye_points=1.0)}
    assert st["C"]["points"] == 1 and st["A"]["points"] == 0.5
    assert st["C"]["bh"] == 1                    # виртуальный соперник с очками самого игрока
    st0 = {r["name"]: r for r in swiss.standings(P, pairings, bye_points=0.5)}
    assert st0["C"]["points"] == 0.5


def test_forfeit_counts_points_but_not_as_played():
    P = [{"id": 1, "name": "A", "rating": 0}, {"id": 2, "name": "B", "rating": 0}]
    st = {r["name"]: r for r in swiss.standings(P, [{"round": 1, "white": 1, "black": 2, "result": "+-"}])}
    assert st["A"]["points"] == 1 and st["B"]["points"] == 0
    info = swiss.history(P, [{"round": 1, "white": 1, "black": 2, "result": "+-"}])
    assert info[1]["colors"] == [] and 2 in info[1]["opps"]


@pytest.mark.parametrize("n", [3, 4, 5, 6, 7, 8, 10])
def test_round_robin_everyone_meets_everyone(n):
    rr = swiss.round_robin(list(range(1, n + 1)))
    assert len(rr) == (n - 1 if n % 2 == 0 else n)
    met = set()
    for rnd in rr:
        ids = [x for p in rnd for x in p if x is not None]
        assert len(ids) == len(set(ids))
        for a, b in rnd:
            if b is not None:
                met.add(frozenset((a, b)))
    assert met == {frozenset(p) for p in itertools.combinations(range(1, n + 1), 2)}


def test_color_allocation_balances():
    a = {"id": 1, "colors": ["w", "w"]}
    b = {"id": 2, "colors": ["b", "b"]}
    w, bl = swiss.allocate_colors(a, b, 1)
    assert w is b and bl is a
