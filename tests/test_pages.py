"""Все страницы открываются (гость, участник, тренер, админ), задачи и партии с ботами."""
from test_roles_clubs_tournaments import create_club, create_tournament, make

PUBLIC = ["/", "/play", "/play?mode=coach", "/puzzles", "/puzzles?id=3", "/knowledge", "/about", "/analysis",
          "/analysis?fen=8/8/8/8/8/8/8/K6k%20w%20-%20-%200%201", "/leaderboard", "/leaderboard?pool=blitz",
          "/leaderboard?pool=puzzle", "/online", "/clubs", "/tournaments", "/login", "/register", "/favicon.ico",
          "/training", "/api/puzzles", "/api/ping", "/api/online/status", "/api/notifications"]


def test_public_pages(browser):
    b = browser()
    for url in PUBLIC:
        r = b.get(url)
        assert r.status_code in (200, 301, 302), (url, r.status_code)
    assert b.get("/no-such-page").status_code == 404
    assert "Страница не найдена" in b.get("/no-such-page").data.decode()


def test_member_pages_and_bot_game(browser, db_exec):
    u = make(browser, db_exec, "Pupil")
    for url in ("/profile", "/account", "/notifications", "/u/Pupil"):
        assert u.get(url).status_code in (200, 302), url
    r = u.api("/api/game-result", {"bot": 600, "mode": "bot", "result": "win", "color": "b",
                                   "moves": "e4 e5 Qh5 Nc6 Bc4 Nf6 Qxf7#"})
    d = r.get_json()
    assert d["ok"] and d["delta"] > 0
    gid = db_exec("SELECT id FROM games")[0][0]
    assert u.get(f"/game/{gid}").status_code == 200
    pgn = u.get(f"/game/{gid}.pgn").data.decode()
    assert '[White "Бот 600"]' in pgn and "0-1" in pgn                  # человек играл чёрными и выиграл
    assert u.get(f"/analysis?game={gid}").status_code == 200
    # неверные данные
    assert u.api("/api/game-result", {"bot": 777, "result": "win"}).status_code == 400
    # задачи: первая попытка двигает рейтинг, повторная — нет
    d1 = u.api("/api/puzzle-attempt", {"id": 1, "ok": True}).get_json()
    assert d1["delta"] > 0
    d2 = u.api("/api/puzzle-attempt", {"id": 1, "ok": True}).get_json()
    assert "delta" not in d2
    d3 = u.api("/api/puzzle-attempt", {"id": 2, "ok": False}).get_json()
    assert d3["delta"] < 0
    assert u.api("/api/puzzle-attempt", {"id": 999, "ok": True}).status_code == 400
    assert db_exec("SELECT COUNT(*) FROM solved")[0][0] == 1
    assert u.get("/leaderboard?pool=puzzle").status_code == 200
    other = browser()
    assert other.get("/u/Pupil").status_code == 200


def test_coach_and_admin_pages(browser, db_exec):
    c = make(browser, db_exec, "Coach", "coach")
    cid = create_club(c)
    tid = create_tournament(c, club_id=cid)
    for url in ("/clubs/new", f"/clubs/{cid}", f"/clubs/{cid}/edit", "/tournaments/new", f"/t/{tid}",
                f"/t/{tid}/edit", f"/t/{tid}/print", f"/tournaments/new?club={cid}"):
        assert c.get(url).status_code == 200, url
    a = make(browser, db_exec, "Admin", "admin")
    assert a.get("/admin/users?role=coach&blocked=1&q=co").status_code == 200
