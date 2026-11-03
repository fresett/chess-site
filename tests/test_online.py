"""Онлайн-партия двумя независимыми клиентами (у каждого свои cookie): поиск, ходы, часы, сдача, ничья,
реванш, разрыв связи, время, превращение, рокировка, взятие на проходе, вызовы."""
import time

import pytest

from ladya import online


@pytest.fixture
def clock(monkeypatch):
    """Управляемое время: clock.t += 10 — «прошло 10 секунд»."""
    class C:
        t = time.time()
    monkeypatch.setattr(time, "time", lambda: C.t)
    return C


def start_game(browser, a_nick="Alice", b_nick="Bob", tc="5+0", guest_b=False):
    a, b = browser(), browser()
    a.register(a_nick)
    if guest_b:
        b.post("/guest", {"next": "/online"})
    else:
        b.register(b_nick)
    r = a.api("/api/online/seek", {"tc": tc}).get_json()
    assert r.get("searching")
    r = b.api("/api/online/seek", {"tc": tc}).get_json()
    gid = r["game"]
    st_a = a.get(f"/api/online/game/{gid}").get_json()
    white, black = (a, b) if st_a["you"] == "w" else (b, a)
    return gid, white, black


def mv(br, gid, uci, ply):
    return br.api(f"/api/online/game/{gid}/move", {"uci": uci, "ply": ply})


def play(gid, white, black, moves):
    st = None
    for i, u in enumerate(moves):
        r = mv(white if i % 2 == 0 else black, gid, u, i)
        assert r.status_code == 200, (u, r.get_json())
        st = r.get_json()
    return st


def test_seek_and_play_scholars_mate_rated(browser, db_exec):
    gid, w, b = start_game(browser)
    st = w.get(f"/api/online/game/{gid}").get_json()
    assert st["status"] == "active" and st["rated"] and st["pool"] == "blitz"
    st = play(gid, w, b, ["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6", "h5f7"])
    assert st["status"] == "over" and st["result"] == "1-0" and st["reason"] == "checkmate"
    assert st["delta"]["w"] > 0 and st["delta"]["b"] < 0
    # история партий у обоих + Glicko-2 в пуле «блиц»
    assert db_exec("SELECT COUNT(*) FROM games WHERE mode='online' AND online_id=?", (gid,))[0][0] == 2
    assert db_exec("SELECT COUNT(*) FROM ratings WHERE pool='blitz'")[0][0] == 2
    # второй клиент видит то же самое
    assert b.get(f"/api/online/game/{gid}").get_json()["result"] == "1-0"
    # PGN
    pgn = w.get(f"/online/game/{gid}.pgn").data.decode()
    assert "1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0" in pgn


def test_illegal_and_out_of_turn_moves_rejected(browser):
    gid, w, b = start_game(browser)
    assert mv(b, gid, "e7e5", 0).status_code == 409          # не его ход
    assert mv(w, gid, "e2e5", 0).status_code == 400          # недопустимый ход
    assert mv(w, gid, "e2e4", 5).status_code == 409          # устаревшая позиция
    assert mv(w, gid, "e2e4", 0).status_code == 200
    stranger = browser()
    stranger.register("Eve")
    assert mv(stranger, gid, "e7e5", 1).status_code == 403   # чужая партия
    # но смотреть можно
    assert stranger.get(f"/api/online/game/{gid}").get_json()["you"] is None


def test_castling_en_passant_promotion(browser):
    gid, w, b = start_game(browser)
    # рокировка белых в короткую сторону
    st = play(gid, w, b, ["e2e4", "a7a6", "g1f3", "a6a5", "f1e2", "a5a4", "e1g1"])
    assert st["moves"][-1] == "e1g1"
    # взятие на проходе и превращение
    gid2, w2, b2 = start_game(browser, "Carl", "Dina")
    seq = ["e2e4", "a7a6", "e4e5", "d7d5", "e5d6",            # взятие на проходе
           "a6a5", "d6c7", "a5a4", "c7b8q"]                    # превращение со взятием
    st = play(gid2, w2, b2, seq)
    assert st["moves"][4] == "e5d6" and st["moves"][-1] == "c7b8q"
    # пешка без указания фигуры превращается в ферзя
    gid3, w3, b3 = start_game(browser, "Fedor", "Galya")
    st = play(gid3, w3, b3, ["h2h4", "g7g5", "h4g5", "h7h6", "g5h6", "a7a6", "h6h7", "a6a5"])
    st = mv(w3, gid3, "h7g8", 8).get_json()
    assert st["moves"][-1] == "h7g8q"


def test_resign_draw_and_rematch(browser):
    gid, w, b = start_game(browser)
    play(gid, w, b, ["d2d4", "d7d5"])
    # ничья: предложение → отклонено → снова → принято
    st = w.api(f"/api/online/game/{gid}/draw", {"action": "offer"}).get_json()
    assert st["draw_offer"] == "w"
    st = b.api(f"/api/online/game/{gid}/draw", {"action": "decline"}).get_json()
    assert st["draw_offer"] is None
    w.api(f"/api/online/game/{gid}/draw", {"action": "offer"})
    st = b.api(f"/api/online/game/{gid}/draw", {"action": "accept"}).get_json()
    assert st["status"] == "over" and st["result"] == "1/2-1/2" and st["reason"] == "agreement"
    # реванш: цвета меняются
    st = b.api(f"/api/online/game/{gid}/rematch", {"action": "offer"}).get_json()
    assert st["rematch_offer"] == "b"
    st = w.api(f"/api/online/game/{gid}/rematch", {"action": "accept"}).get_json()
    new = st["rematch"]
    assert new
    st2 = b.get(f"/api/online/game/{new}").get_json()
    assert st2["you"] == "w"                                   # бывший чёрный теперь белыми
    # сдача
    play(new, b, w, ["e2e4", "e7e5"])
    st = w.api(f"/api/online/game/{new}/resign").get_json()
    assert st["result"] == "1-0" and st["reason"] == "resign"


def test_abort_before_first_moves(browser):
    gid, w, b = start_game(browser)
    st = b.api(f"/api/online/game/{gid}/resign").get_json()
    assert st["result"] == "aborted"


def test_timeout_and_increment(browser, clock):
    gid, w, b = start_game(browser, tc="1+0")
    play(gid, w, b, ["e2e4", "e7e5"])                          # часы пошли после хода чёрных
    clock.t += 30
    st = mv(w, gid, "g1f3", 2).get_json()
    assert 29000 <= st["clock"]["w"] <= 30500
    clock.t += 61                                              # чёрные думают слишком долго
    st = w.get(f"/api/online/game/{gid}").get_json()
    assert st["status"] == "over" and st["result"] == "1-0" and st["reason"] == "timeout"
    # прибавка: 2+1
    gid2, w2, b2 = start_game(browser, "Ira", "Kostya", tc="2+1")
    play(gid2, w2, b2, ["e2e4", "e7e5"])
    clock.t += 5
    st = mv(w2, gid2, "g1f3", 2).get_json()
    assert 115500 <= st["clock"]["w"] <= 116500                # 120 − 5 + 1


def test_first_move_timeout_aborts(browser, clock):
    gid, w, b = start_game(browser)
    clock.t += online.START_WAIT + 1
    st = w.get(f"/api/online/game/{gid}").get_json()
    assert st["result"] == "aborted"


def test_disconnect_claim_victory(browser, clock):
    gid, w, b = start_game(browser, tc="10+0")
    play(gid, w, b, ["e2e4", "e7e5"])
    # чёрные «закрыли вкладку»: белые продолжают опрашивать
    for _ in range(4):
        clock.t += 10
        st = w.get(f"/api/online/game/{gid}").get_json()
    assert st["oppGone"] >= online.CLAIM_AFTER and st["canClaim"]
    st = w.api(f"/api/online/game/{gid}/claim", {"action": "win"}).get_json()
    assert st["result"] == "1-0" and st["reason"] == "left"


def test_claim_not_allowed_while_opponent_online(browser):
    gid, w, b = start_game(browser)
    play(gid, w, b, ["e2e4", "e7e5"])
    st = w.api(f"/api/online/game/{gid}/claim", {"action": "win"}).get_json()
    assert st["status"] == "active"


def test_light_poll_when_nothing_changed(browser):
    gid, w, b = start_game(browser)
    st = w.get(f"/api/online/game/{gid}").get_json()
    r = w.get(f"/api/online/game/{gid}?v={st['ver']}").get_json()
    assert r["same"] is True and "moves" not in r


def test_guest_vs_user_unrated(browser):
    gid, w, b = start_game(browser, guest_b=True)
    st = w.get(f"/api/online/game/{gid}").get_json()
    assert st["rated"] is False


def test_same_account_two_tabs_not_paired(browser):
    a1 = browser()
    a1.register("Solo")
    a2 = browser()
    a2.login("Solo")
    a1.api("/api/online/seek", {"tc": "5+0"})
    r = a2.api("/api/online/seek", {"tc": "5+0"}).get_json()
    assert r.get("searching")


def test_challenge_by_nick_and_link(browser, db_exec):
    a, b, c = browser(), browser(), browser()
    a.register("Masha")
    b.register("Petya")
    r = a.api("/api/online/challenge", {"nick": "petya", "tc": "10+5", "color": "white"}).get_json()
    cid = r["id"]
    assert db_exec("SELECT COUNT(*) FROM notifications WHERE kind='challenge'")[0][0] == 1
    assert b.get("/api/notifications").get_json()["unread"] == 1
    # третий не может принять адресный вызов
    c.register("Vasya")
    assert c.api(f"/api/online/challenge/{cid}/accept").status_code == 403
    st = b.api(f"/api/online/challenge/{cid}/accept").get_json()
    gid = st["game"]
    assert a.get(f"/api/online/game/{gid}").get_json()["you"] == "w"
    assert a.get(f"/api/online/challenge/{cid}").get_json()["game"] == gid
    # вызов по ссылке: гость может принять
    play(gid, a, b, ["e2e4", "e7e5"])
    a.api(f"/api/online/game/{gid}/resign")
    r = a.api("/api/online/challenge", {"tc": "3+2"}).get_json()
    g = browser()
    g.post("/guest", {"next": "/"})
    assert g.get(r["url"]).status_code == 200
    st = g.api(f"/api/online/challenge/{r['id']}/accept").get_json()
    assert st["status"] == "accepted"


def test_api_requires_identity(browser):
    anon = browser()
    assert anon.api("/api/online/seek", {"tc": "5+0"}).status_code == 401


def test_game_pages_render(browser):
    gid, w, b = start_game(browser)
    assert w.get(f"/online/game/{gid}").status_code == 200
    assert w.get("/online").status_code == 200
    assert browser().get(f"/online/game/{gid}").status_code == 200        # зритель
