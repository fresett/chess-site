"""Рейтинги.

* Клубный рейтинг (users.rating, старт 100) — как и раньше, за партии с ботами и тренером Альбертом:
  победа +7…+11, поражение −7…−12. Понятен детям и не «прыгает».
* Glicko-2 — отдельно по контролям времени (пуля, блиц, рапид, классика) за онлайн-партии между людьми,
  и отдельно — за задачи. Старт 1500, как на lichess.
"""
import random
import time

from . import glicko2
from .db import RATING_START

POOLS = {"bullet": "Пуля", "blitz": "Блиц", "rapid": "Рапид", "classical": "Классика", "puzzle": "Задачи"}
GAME_POOLS = ("bullet", "blitz", "rapid", "classical")


def pool_of(base_sec, inc_sec):
    """Как на lichess: оценка длительности партии = база + 40 × прибавка."""
    est = base_sec + 40 * inc_sec
    if est < 180:
        return "bullet"
    if est < 480:
        return "blitz"
    if est < 1500:
        return "rapid"
    return "classical"


def club_rating_change(rating, opp, result):
    """Клубный рейтинг за партию с ботом. Победа: +7…+11, поражение: −7…−12, ничья: 0. Не ниже 100."""
    x = max(0.0, min(1.0, (opp - rating + 400) / 800))
    jitter = random.choice((-1, 0, 1))
    if result == "win":
        d = min(11, max(7, 7 + round(4 * x) + jitter))
    elif result == "loss":
        d = -min(12, max(7, 12 - round(5 * x) + jitter))
    else:
        d = 0
    new = max(RATING_START, rating + d)
    return new, new - rating


def get(d, uid, pool):
    """Рейтинг игрока в пуле: dict(rating, rd, vol, games). Если партий не было — стартовый."""
    r = d.execute("SELECT rating, rd, vol, games, updated FROM ratings WHERE user_id=? AND pool=?",
                  (uid, pool)).fetchone()
    if not r:
        return {"rating": glicko2.START, "rd": glicko2.START_RD, "vol": glicko2.START_VOL, "games": 0}
    rd = r["rd"]
    if r["updated"]:
        rd = glicko2.decay_rd(rd, r["vol"], (time.time() - r["updated"]) / 86400)
    return {"rating": r["rating"], "rd": rd, "vol": r["vol"], "games": r["games"]}


def all_for(d, uid):
    out = {}
    for p in POOLS:
        r = get(d, uid, p)
        out[p] = {**r, "show": display(r)}
    return out


def display(r):
    s = str(round(r["rating"]))
    return s + "?" if r["rd"] > glicko2.PROVISIONAL_RD else s


def _store(d, uid, pool, new):
    now = time.time()
    d.execute("INSERT INTO ratings(user_id,pool,rating,rd,vol,games,updated) VALUES(?,?,?,?,?,1,?) "
              "ON CONFLICT(user_id,pool) DO UPDATE SET rating=excluded.rating, rd=excluded.rd, vol=excluded.vol, "
              "games=games+1, updated=excluded.updated", (uid, pool, new[0], new[1], new[2], now))
    d.execute("INSERT INTO rating_history(user_id,pool,rating,ts) VALUES(?,?,?,?)", (uid, pool, round(new[0]), now))


def rate_game(d, w_uid, b_uid, pool, score_white):
    """Пересчитать Glicko-2 обоих игроков. Возвращает (дельта белых, дельта чёрных)."""
    rw, rb = get(d, w_uid, pool), get(d, b_uid, pool)
    nw = glicko2.update(rw["rating"], rw["rd"], rw["vol"], rb["rating"], rb["rd"], score_white)
    nb = glicko2.update(rb["rating"], rb["rd"], rb["vol"], rw["rating"], rw["rd"], 1 - score_white)
    _store(d, w_uid, pool, nw)
    _store(d, b_uid, pool, nb)
    return round(nw[0]) - round(rw["rating"]), round(nb[0]) - round(rb["rating"])


def rate_puzzle(d, uid, puzzle_rating, ok):
    r = get(d, uid, "puzzle")
    new = glicko2.update(r["rating"], r["rd"], r["vol"], puzzle_rating, 80, 1.0 if ok else 0.0)
    _store(d, uid, "puzzle", new)
    return round(new[0]), round(new[0]) - round(r["rating"])
