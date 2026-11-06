"""Права ролей, клубы, турниры (очные и онлайн), админка."""
import re

from conftest import PW


def make(browser, db_exec, nick, role="user"):
    b = browser()
    b.register(nick)
    if role != "user":
        db_exec("UPDATE users SET role=? WHERE nick=?", (role, nick))
    return b


def create_club(coach, name="Клуб Ладья", policy="request"):
    r = coach.post("/clubs/new", {"name": name, "description": "Занятия по субботам", "join_policy": policy})
    assert r.status_code == 302
    return int(r.headers["Location"].rstrip("/").split("/")[-1])


def create_tournament(coach, **kw):
    data = {"name": "Осенний кубок", "rounds": "3", "tc": "600+5", "mode": "offline", "system": "swiss",
            "bye_points": "1", "club_id": ""}
    data.update({k: str(v) for k, v in kw.items()})
    r = coach.post("/tournaments/new", data)
    assert r.status_code == 302, r.data[:300]
    return int(re.search(r"/t/(\d+)", r.headers["Location"]).group(1))


# ---------------------------------------------------------------- роли
def test_user_cannot_create_clubs_or_tournaments_or_open_admin(browser, db_exec):
    u = make(browser, db_exec, "Student")
    assert u.get("/clubs/new").status_code == 403
    assert u.post("/clubs/new", {"name": "Хакеры"}).status_code == 403
    assert u.get("/tournaments/new").status_code == 403
    assert u.get("/admin/").status_code == 403
    assert u.post("/admin/users/1/role", {"role": "admin"}).status_code == 403
    anon = browser()
    assert anon.get("/admin/").status_code == 302                       # на вход


def test_coach_manages_only_own(browser, db_exec):
    c1 = make(browser, db_exec, "Elena", "coach")
    c2 = make(browser, db_exec, "Other", "coach")
    cid = create_club(c1)
    assert c2.get(f"/clubs/{cid}/edit").status_code == 403
    assert c2.post(f"/clubs/{cid}/new-code").status_code == 403
    tid = create_tournament(c1, club_id=cid)
    assert c2.post(f"/t/{tid}/start").status_code == 403
    assert c2.post(f"/t/{tid}/players/add", {"name": "X"}).status_code == 403
    # второй тренер не может создать турнир в чужом клубе
    r = c2.post("/tournaments/new", {"name": "Чужой", "rounds": "3", "tc": "600+5", "mode": "offline",
                                     "system": "swiss", "bye_points": "1", "club_id": str(cid)})
    assert r.status_code == 200 and "клуб, которым ты управляешь" in r.data.decode()
    # админ может всё
    adm = make(browser, db_exec, "Kirill", "admin")
    assert adm.get(f"/clubs/{cid}/edit").status_code == 200
    assert adm.post(f"/t/{tid}/players/add", {"name": "Вася"}).status_code == 302


def test_admin_user_management(browser, db_exec):
    adm = make(browser, db_exec, "Boss", "owner")      # роли выдаёт только владелец
    s = make(browser, db_exec, "Kid")
    uid = db_exec("SELECT id FROM users WHERE nick='Kid'")[0][0]
    assert "Kid" in adm.get("/admin/users?q=ki").data.decode()
    adm.post(f"/admin/users/{uid}/role", {"role": "coach"})
    assert db_exec("SELECT role FROM users WHERE id=?", (uid,))[0][0] == "coach"
    # блокировка выкидывает из сессии и не пускает
    adm.post(f"/admin/users/{uid}/block")
    assert s.get("/profile").status_code == 302
    assert s.login("Kid").status_code == 403
    adm.post(f"/admin/users/{uid}/unblock")
    # сброс пароля → временный пароль + обязательная смена
    r = adm.post(f"/admin/users/{uid}/reset", follow_redirects=True)
    temp = re.search(r"Временный пароль для Kid: (\S+)", r.data.decode()).group(1)
    k = browser()
    r = k.login("Kid", temp)
    assert r.headers["Location"].endswith("/account/new-password")
    assert k.get("/profile").status_code == 302                          # пока не сменит — никуда
    r = k.post("/account/new-password", {"old": temp, "new": "NewSecret-77", "new2": "NewSecret-77"})
    assert r.status_code == 302
    assert k.get("/profile").status_code == 200
    # владельца нельзя понизить через админку
    aid = db_exec("SELECT id FROM users WHERE nick='Boss'")[0][0]
    adm.post(f"/admin/users/{aid}/role", {"role": "user"})
    assert db_exec("SELECT role FROM users WHERE id=?", (aid,))[0][0] == "owner"
    for page in ("/admin/", "/admin/clubs", "/admin/tournaments", "/admin/reports", "/admin/logins"):
        assert adm.get(page).status_code == 200, page


# ---------------------------------------------------------------- клубы
def test_club_join_flow(browser, db_exec):
    coach = make(browser, db_exec, "Elena", "coach")
    cid = create_club(coach)
    s1 = make(browser, db_exec, "Anya")
    s2 = make(browser, db_exec, "Borya")
    # заявка → тренер видит → принимает
    s1.post(f"/clubs/{cid}/join", {"message": "Хочу заниматься"})
    page = coach.get(f"/clubs/{cid}").data.decode()
    assert "Anya" in page and "Хочу заниматься" in page
    uid1 = db_exec("SELECT id FROM users WHERE nick='Anya'")[0][0]
    coach.post(f"/clubs/{cid}/requests/{uid1}/approve")
    assert db_exec("SELECT COUNT(*) FROM club_members WHERE club_id=? AND user_id=?", (cid, uid1))[0][0] == 1
    # по ссылке-приглашению — сразу
    code = db_exec("SELECT invite_code FROM clubs WHERE id=?", (cid,))[0][0]
    assert s2.get(f"/clubs/join/{code}").status_code == 200
    s2.post(f"/clubs/join/{code}")
    uid2 = db_exec("SELECT id FROM users WHERE nick='Borya'")[0][0]
    assert db_exec("SELECT COUNT(*) FROM club_members WHERE club_id=? AND user_id=?", (cid, uid2))[0][0] == 1
    # новая ссылка — старая не работает
    coach.post(f"/clubs/{cid}/new-code")
    r = make(browser, db_exec, "Late").get(f"/clubs/join/{code}")
    assert r.status_code == 302
    # удаление участника
    coach.post(f"/clubs/{cid}/members/{uid2}/remove")
    assert db_exec("SELECT COUNT(*) FROM club_members WHERE club_id=? AND user_id=?", (cid, uid2))[0][0] == 0
    # участник не может удалять других
    assert s1.post(f"/clubs/{cid}/members/{uid1}/remove").status_code == 403
    # страница клуба: рейтинг внутри клуба
    for pool in ("club", "blitz", "puzzle"):
        assert coach.get(f"/clubs/{cid}?pool={pool}").status_code == 200


def test_xss_in_names_is_escaped(browser, db_exec):
    coach = make(browser, db_exec, "Elena", "coach")
    cid = create_club(coach, name='<script>alert(1)</script>')
    html = coach.get(f"/clubs/{cid}").data.decode()
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    tid = create_tournament(coach, name='"><img src=x onerror=alert(1)>')
    coach.post(f"/t/{tid}/players/add", {"name": "<b>Петя</b>"})
    html = coach.get(f"/t/{tid}").data.decode()
    assert "<img src=x" not in html and "<b>Петя</b>" not in html


# ---------------------------------------------------------------- турниры: очно
def test_offline_swiss_tournament_full_cycle(browser, db_exec):
    coach = make(browser, db_exec, "Elena", "coach")
    cid = create_club(coach, policy="open")
    tid = create_tournament(coach, club_id=cid, rounds=3)
    # участники клуба записываются сами, плюс игроки без аккаунта
    for n in ("Anya", "Borya", "Vika"):
        s = make(browser, db_exec, n)
        s.post(f"/clubs/{cid}/join")
        assert s.post(f"/t/{tid}/join").status_code == 302
    outsider = make(browser, db_exec, "Stranger")
    outsider.post(f"/t/{tid}/join")
    for name, rating in (("Гоша", 1200), ("Даша", 900)):
        coach.post(f"/t/{tid}/players/add", {"name": name, "rating": rating})
    names = [r[0] for r in db_exec("SELECT name FROM t_players WHERE tournament_id=?", (tid,))]
    assert len(names) == 5 and "Stranger" not in names
    coach.post(f"/t/{tid}/start")
    assert db_exec("SELECT status FROM tournaments WHERE id=?", (tid,))[0][0] == "running"
    for rnd in (1, 2, 3):
        coach.post(f"/t/{tid}/pair")
        rows = db_exec("SELECT id, black_id, result FROM t_pairings WHERE tournament_id=? AND round=?", (tid, rnd))
        assert len(rows) == 3                                            # 2 пары + bye
        assert sum(1 for r in rows if r[1] is None and r[2] == "bye") == 1
        # следующую жеребьёвку нельзя, пока нет результатов
        coach.post(f"/t/{tid}/pair")
        assert db_exec("SELECT MAX(round) FROM t_pairings WHERE tournament_id=?", (tid,))[0][0] == rnd
        for pid, black, res in rows:
            if black is not None:
                coach.post(f"/t/{tid}/result/{pid}", {"result": "1-0" if pid % 2 else "1/2"})
    assert db_exec("SELECT status FROM tournaments WHERE id=?", (tid,))[0][0] == "finished"
    # без повторных пар
    pairs = db_exec("SELECT white_id, black_id FROM t_pairings WHERE tournament_id=? AND black_id IS NOT NULL", (tid,))
    assert len({frozenset(p) for p in pairs}) == len(pairs)
    html = coach.get(f"/t/{tid}").data.decode()
    assert "Бух-1" in html and "Гоша" in html
    assert coach.get(f"/t/{tid}/print").status_code == 200
    assert coach.get(f"/t/{tid}/print/round/2").status_code == 200
    # исправление результата после окончания: открыть снова
    coach.post(f"/t/{tid}/reopen")
    pid = db_exec("SELECT id FROM t_pairings WHERE tournament_id=? AND round=1 AND black_id IS NOT NULL", (tid,))[0][0]
    coach.post(f"/t/{tid}/result/{pid}", {"result": "0-1"})
    assert db_exec("SELECT result FROM t_pairings WHERE id=?", (pid,))[0][0] == "0-1"


def test_withdraw_and_late_entry(browser, db_exec):
    coach = make(browser, db_exec, "Elena", "coach")
    tid = create_tournament(coach, rounds=3)
    for n in "ABCD":
        coach.post(f"/t/{tid}/players/add", {"name": f"Игрок {n}"})
    coach.post(f"/t/{tid}/start")
    coach.post(f"/t/{tid}/pair")
    for (pid,) in db_exec("SELECT id FROM t_pairings WHERE tournament_id=? AND round=1", (tid,)):
        coach.post(f"/t/{tid}/result/{pid}", {"result": "1-0"})
    a = db_exec("SELECT id FROM t_players WHERE tournament_id=? AND name='Игрок A'", (tid,))[0][0]
    coach.post(f"/t/{tid}/players/{a}/withdraw")
    coach.post(f"/t/{tid}/players/add", {"name": "Опоздавший"})
    coach.post(f"/t/{tid}/pair")
    r2 = db_exec("SELECT white_id, black_id FROM t_pairings WHERE tournament_id=? AND round=2", (tid,))
    ids = {x for p in r2 for x in p if x}
    assert a not in ids and len(ids) == 4
    # отмена жеребьёвки тура без результатов
    coach.post(f"/t/{tid}/unpair")
    assert db_exec("SELECT COUNT(*) FROM t_pairings WHERE tournament_id=? AND round=2", (tid,))[0][0] == 0


def test_round_robin(browser, db_exec):
    coach = make(browser, db_exec, "Elena", "coach")
    tid = create_tournament(coach, system="rr", rounds=1)
    for n in "ABCD":
        coach.post(f"/t/{tid}/players/add", {"name": "Игрок " + n})
    coach.post(f"/t/{tid}/start")
    assert db_exec("SELECT rounds FROM tournaments WHERE id=?", (tid,))[0][0] == 3
    for rnd in (1, 2, 3):
        coach.post(f"/t/{tid}/pair")
        for (pid,) in db_exec("SELECT id FROM t_pairings WHERE tournament_id=? AND round=?", (tid, rnd)):
            coach.post(f"/t/{tid}/result/{pid}", {"result": "1/2"})
    pairs = db_exec("SELECT white_id, black_id FROM t_pairings WHERE tournament_id=?", (tid,))
    assert len({frozenset(p) for p in pairs}) == 6
    assert db_exec("SELECT status FROM tournaments WHERE id=?", (tid,))[0][0] == "finished"


# ---------------------------------------------------------------- турниры: онлайн
def test_online_tournament_games_and_auto_results(browser, db_exec):
    coach = make(browser, db_exec, "Elena", "coach")
    tid = create_tournament(coach, mode="online", rounds=2, tc="180+2")
    players = {}
    for n in ("Ann", "Ben", "Cat", "Dan"):
        players[n] = make(browser, db_exec, n)
        players[n].post(f"/t/{tid}/join")
    # в онлайн-турнир нельзя добавить игрока без аккаунта
    coach.post(f"/t/{tid}/players/add", {"name": "Без аккаунта"})
    assert db_exec("SELECT COUNT(*) FROM t_players WHERE tournament_id=?", (tid,))[0][0] == 4
    coach.post(f"/t/{tid}/start")
    coach.post(f"/t/{tid}/pair")
    games = db_exec("SELECT p.id, g.id, g.w_name, g.b_name FROM t_pairings p JOIN online_games g ON g.id=p.game_id "
                    "WHERE p.tournament_id=? AND p.round=1", (tid,))
    assert len(games) == 2
    # игроки получили уведомления
    assert db_exec("SELECT COUNT(*) FROM notifications WHERE kind='tournament'")[0][0] >= 4
    # статус лобби ведёт игрока в его партию
    _, gid, wn, bn = games[0]
    assert players[wn].get("/api/online/status").get_json()["game"] == gid
    # партия 1: мат; партия 2: сдача
    for i, u in enumerate(["f2f3", "e7e5", "g2g4", "d8h4"]):
        r = players[wn if i % 2 == 0 else bn].api(f"/api/online/game/{gid}/move", {"uci": u, "ply": i})
        assert r.status_code == 200
    _, gid2, wn2, bn2 = games[1]
    for i, u in enumerate(["e2e4", "e7e5"]):
        players[wn2 if i % 2 == 0 else bn2].api(f"/api/online/game/{gid2}/move", {"uci": u, "ply": i})
    players[bn2].api(f"/api/online/game/{gid2}/resign")
    res = dict(db_exec("SELECT game_id, result FROM t_pairings WHERE tournament_id=? AND round=1", (tid,)))
    assert res[gid] == "0-1" and res[gid2] == "1-0"
    # тренер получил уведомление «тур завершён», можно делать 2-й тур
    coach.post(f"/t/{tid}/pair")
    assert db_exec("SELECT COUNT(*) FROM t_pairings WHERE tournament_id=? AND round=2", (tid,))[0][0] == 2
    # в турнирной партии нельзя «отменить» партию сдачей до первых ходов — это поражение
    g3 = db_exec("SELECT g.id, g.w_name FROM t_pairings p JOIN online_games g ON g.id=p.game_id "
                 "WHERE p.tournament_id=? AND p.round=2", (tid,))[0]
    st = players[g3[1]].api(f"/api/online/game/{g3[0]}/resign").get_json()
    assert st["result"] == "0-1"
    assert coach.get(f"/t/{tid}").status_code == 200
    assert coach.get(f"/api/t/{tid}/sig").get_json()["sig"]


def test_online_tournament_noshow_is_forfeit(browser, db_exec, monkeypatch):
    import time as _t
    coach = make(browser, db_exec, "Elena", "coach")
    tid = create_tournament(coach, mode="online", rounds=1)
    for n in ("Ann", "Ben"):
        make(browser, db_exec, n).post(f"/t/{tid}/join")
    coach.post(f"/t/{tid}/start")
    coach.post(f"/t/{tid}/pair")
    now = _t.time()
    monkeypatch.setattr(_t, "time", lambda: now + 200)
    coach.get(f"/t/{tid}")                                               # страница турнира сама завершает брошенные
    assert db_exec("SELECT result FROM t_pairings WHERE tournament_id=?", (tid,))[0][0] == "-+"
    assert db_exec("SELECT status FROM tournaments WHERE id=?", (tid,))[0][0] == "finished"


def test_must_join_club_for_club_tournament(browser, db_exec):
    coach = make(browser, db_exec, "Elena", "coach")
    cid = create_club(coach)
    tid = create_tournament(coach, club_id=cid)
    s = make(browser, db_exec, "Anya")
    s.post(f"/t/{tid}/join")
    assert db_exec("SELECT COUNT(*) FROM t_players WHERE tournament_id=?", (tid,))[0][0] == 0
    assert PW


def test_double_pair_click_does_not_duplicate_round(app, browser, db_exec):
    from ladya import tournaments as tm
    coach = make(browser, db_exec, "Elena", "coach")
    tid = create_tournament(coach, mode="online", rounds=2)
    for n in ("Ann", "Ben", "Cat", "Dan"):
        make(browser, db_exec, n).post(f"/t/{tid}/join")
    coach.post(f"/t/{tid}/start")
    with app.test_request_context():
        stale = tm.get_t(tid)                     # второй запрос прочитал турнир до того, как первый сделал пары
        assert tm.make_pairings(tm.get_t(tid))[0] == 1
        assert tm.make_pairings(stale) == (None, [])
    assert db_exec("SELECT COUNT(*) FROM t_pairings WHERE tournament_id=?", (tid,))[0][0] == 2
    assert db_exec("SELECT COUNT(*) FROM online_games")[0][0] == 2


def test_arbiter_result_goes_to_history_and_rating(browser, db_exec):
    coach = make(browser, db_exec, "Elena", "coach")
    tid = create_tournament(coach, mode="online", rounds=1)
    pl = {n: make(browser, db_exec, n) for n in ("Ann", "Ben")}
    for b in pl.values():
        b.post(f"/t/{tid}/join")
    coach.post(f"/t/{tid}/start")
    coach.post(f"/t/{tid}/pair")
    pid, gid, wn, bn = db_exec("SELECT p.id, g.id, g.w_name, g.b_name FROM t_pairings p "
                               "JOIN online_games g ON g.id=p.game_id WHERE p.tournament_id=?", (tid,))[0]
    for i, u in enumerate(["e2e4", "e7e5"]):
        pl[wn if i % 2 == 0 else bn].api(f"/api/online/game/{gid}/move", {"uci": u, "ply": i})
    coach.post(f"/t/{tid}/result/{pid}", {"result": "1-0"})
    assert tuple(db_exec("SELECT status, result, reason FROM online_games WHERE id=?", (gid,))[0]) == ("over", "1-0", "arbiter")
    assert db_exec("SELECT result FROM t_pairings WHERE id=?", (pid,))[0][0] == "1-0"
    assert db_exec("SELECT COUNT(*) FROM games WHERE online_id=?", (gid,))[0][0] == 2
    assert db_exec("SELECT status FROM tournaments WHERE id=?", (tid,))[0][0] == "finished"


def test_owner_role_assignment(browser, db_exec):
    owner = make(browser, db_exec, "fresett", "owner")
    admin = make(browser, db_exec, "Kirill", "admin")
    make(browser, db_exec, "Ira")
    ira = db_exec("SELECT id FROM users WHERE nick='Ira'")[0][0]
    boss = db_exec("SELECT id FROM users WHERE nick='fresett'")[0][0]
    # админ не выдаёт роли и не трогает владельца
    admin.post(f"/admin/users/{ira}/role", {"role": "coach"})
    assert db_exec("SELECT role FROM users WHERE id=?", (ira,))[0][0] == "user"
    admin.post(f"/admin/users/{boss}/block")
    assert db_exec("SELECT blocked FROM users WHERE id=?", (boss,))[0][0] == 0
    # владелец выдаёт «преподавателя», но не может раздать «владельца»
    owner.post(f"/admin/users/{ira}/role", {"role": "coach"})
    assert db_exec("SELECT role FROM users WHERE id=?", (ira,))[0][0] == "coach"
    assert owner.post(f"/admin/users/{ira}/role", {"role": "owner"}).status_code == 400
    # владелец — тоже админ: админка открыта
    assert owner.get("/admin/").status_code == 200
