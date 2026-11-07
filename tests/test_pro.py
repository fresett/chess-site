"""Rookline Pro: коды активации, замок на задачах 21+, продление срока."""
import time

from ladya.db import connect
from ladya.pro import FREE_PUZZLES, gen_codes, norm


def make_codes(app, n=1, days=30):
    c = connect(app.config["DATABASE"])
    try:
        return gen_codes(c, n, days)
    finally:
        c.close()


def test_code_format_and_normalize():
    assert norm("rl-abcd-efgh-jkmn") == "RL-ABCD-EFGH-JKMN"
    assert norm(" RLABCDEFGHJKMN ") == "RL-ABCD-EFGH-JKMN"
    assert norm("abcd efgh jkmn") == "RL-ABCD-EFGH-JKMN"
    assert norm("short") == ""


def test_free_user_sees_locked_puzzles_without_solution(browser):
    b = browser()
    b.register("free1")
    d = b.get("/api/puzzles").get_json()
    free = [p for p in d if p["id"] <= FREE_PUZZLES]
    locked = [p for p in d if p["id"] > FREE_PUZZLES]
    assert free and all(not p["locked"] and "sol" in p for p in free)
    assert locked and all(p["locked"] and "sol" not in p and "fen" not in p for p in locked)
    r = b.api("/api/puzzle-attempt", {"id": FREE_PUZZLES + 1, "ok": True})
    assert r.status_code == 403


def test_guest_also_locked(browser):
    d = browser().get("/api/puzzles").get_json()
    assert all(p["locked"] for p in d if p["id"] > FREE_PUZZLES)


def test_redeem_code_unlocks_and_is_single_use(app, browser, db_exec):
    code = make_codes(app)[0]
    b = browser()
    b.register("buyer")
    r = b.post("/pro/redeem", {"code": code.lower()})
    assert r.status_code == 302
    d = b.get("/api/puzzles").get_json()
    assert not any(p["locked"] for p in d)
    assert b.api("/api/puzzle-attempt", {"id": FREE_PUZZLES + 1, "ok": True}).status_code == 200
    until = db_exec("SELECT pro_until FROM users WHERE nick='buyer'")[0][0]
    assert 29 * 86400 < until - time.time() <= 30 * 86400 + 5
    # второй раз тот же код не сработает (ни у него, ни у другого)
    other = browser()
    other.register("thief")
    other.post("/pro/redeem", {"code": code})
    assert db_exec("SELECT pro_until FROM users WHERE nick='thief'")[0][0] == 0


def test_days_stack(app, browser, db_exec):
    a, b2 = make_codes(app, 2, 30)
    b = browser()
    b.register("stacker")
    b.post("/pro/redeem", {"code": a})
    b.post("/pro/redeem", {"code": b2})
    until = db_exec("SELECT pro_until FROM users WHERE nick='stacker'")[0][0]
    assert until - time.time() > 59 * 86400


def test_expired_pro_locks_again(app, browser, db_exec):
    b = browser()
    b.register("expired")
    db_exec("UPDATE users SET pro_until=? WHERE nick='expired'", (time.time() - 10,))
    d = b.get("/api/puzzles").get_json()
    assert any(p["locked"] for p in d)


def test_staff_gets_pro_free(browser, db_exec):
    b = browser()
    b.register("coachy")
    db_exec("UPDATE users SET role='coach' WHERE nick='coachy'")
    d = b.get("/api/puzzles").get_json()
    assert not any(p["locked"] for p in d)


def test_bad_code_and_pro_page(browser):
    b = browser()
    assert b.get("/pro").status_code == 200
    b.register("typo")
    r = b.post("/pro/redeem", {"code": "RL-AAAA-BBBB-CCCC"}, follow_redirects=True)
    assert "Такого кода нет".encode() in r.data


def test_neon_only_with_pro(app, browser, db_exec):
    b = browser()
    b.register("glow")
    b.post("/pro/neon", {"neon": "pink"})
    assert db_exec("SELECT neon FROM users WHERE nick='glow'")[0][0] == ""     # без Pro нельзя
    b.post("/pro/redeem", {"code": make_codes(app)[0]})
    b.post("/pro/neon", {"neon": "pink"})
    assert db_exec("SELECT neon FROM users WHERE nick='glow'")[0][0] == "pink"
    assert b"neon neon-pink" in b.get("/u/glow").data or b"neon neon-pink" in b.get("/profile").data
    b.post("/pro/neon", {"neon": "hacker; color:red"})                      # мусор = выключить
    assert db_exec("SELECT neon FROM users WHERE nick='glow'")[0][0] == ""


def test_neon_hidden_when_pro_expires(browser, db_exec):
    b = browser()
    b.register("faded")
    db_exec("UPDATE users SET neon='cyan', pro_until=? WHERE nick='faded'", (time.time() - 5,))
    assert b"neon-cyan" not in b.get("/profile").data
    assert b"neon-cyan" not in browser().get("/leaderboard").data


def test_pro_page_shows_prices(browser):
    d = browser().get("/pro").data.decode()
    for price in ("129", "249", "579"):
        assert price + " ₽" in d


def test_coach_limit_three_per_day_then_pro_unlimited(app, browser):
    b = browser()
    b.register("coachfan")
    for left in (2, 1, 0):
        d = b.api("/api/coach/start").get_json()
        assert d["ok"] and d["left"] == left
    d = b.api("/api/coach/start").get_json()
    assert not d["ok"] and "Pro" in d["error"]
    b.post("/pro/redeem", {"code": make_codes(app)[0]})
    for _ in range(5):
        assert b.api("/api/coach/start").get_json()["ok"]


def test_coach_limit_for_guests(browser):
    g = browser()
    assert [g.api("/api/coach/start").get_json()["ok"] for _ in range(4)] == [True, True, True, False]


def test_frame_and_badge(app, browser, db_exec):
    b = browser()
    b.register("framed")
    b.post("/pro/frame", {"frame": "fire"})
    assert db_exec("SELECT frame FROM users WHERE nick='framed'")[0][0] == ""
    b.post("/pro/redeem", {"code": make_codes(app)[0]})
    b.post("/pro/frame", {"frame": "fire"})
    page = b.get("/profile").data
    assert b"fr fr-fire" in page and b"pro-mini" in page


def test_weekly_puzzle_locked_without_pro(app, browser):
    from ladya.puzzles import WEEKLY, weekly_open
    assert weekly_open() >= 1
    b = browser()
    b.register("weekly")
    w = [p for p in b.get("/api/puzzles").get_json() if p.get("weekly")]
    assert w and all(p["locked"] and "sol" not in p for p in w)
    assert b.api("/api/puzzle-attempt", {"id": WEEKLY[0]["id"], "ok": True}).status_code == 403
    b.post("/pro/redeem", {"code": make_codes(app)[0]})
    w = [p for p in b.get("/api/puzzles").get_json() if p.get("weekly")]
    assert all(not p["locked"] and p["sol"] for p in w)
    assert b.api("/api/puzzle-attempt", {"id": WEEKLY[0]["id"], "ok": True}).status_code == 200


def test_weekly_puzzles_are_legal():
    import chess
    from ladya.puzzles import WEEKLY
    for p in WEEKLY:
        bd = chess.Board(p["fen"])
        for u in p["sol"]:
            bd.push(chess.Move.from_uci(u))
        if p["kind"] == "mate":
            assert bd.is_checkmate(), p["title"]
