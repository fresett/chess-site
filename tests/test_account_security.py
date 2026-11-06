"""Аккаунт (пароль, ник, удаление), лимиты и блокировки, CSRF, заголовки, аватарки, миграции, manage.py."""
import os
import sqlite3
import subprocess
import sys

from conftest import PW

from ladya import create_app
from ladya.security import password_problem

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- пароли
def test_password_rules():
    assert password_problem("short1")                     # < 8
    assert password_problem("12345678")                   # частый
    assert password_problem("qwertyuiop")
    assert password_problem("йцукенгш")
    assert password_problem("aaaaaaaaaa")                 # повтор
    assert password_problem("23456789")                   # цифры подряд
    assert password_problem("superKirill1", "Kirill")     # содержит ник
    assert password_problem("Конь-на-f3!") is None
    assert password_problem("correct horse battery") is None


def test_register_rejects_weak_and_stores_hash(browser, db_exec):
    b = browser()
    r = b.post("/register", {"nick": "Weak", "password": "12345678", "password2": "12345678"})
    assert r.status_code == 200 and "известный" in r.data.decode()
    b.register("Strong")
    h = db_exec("SELECT pw_hash FROM users WHERE nick='Strong'")[0][0]
    assert h.startswith("scrypt:") and PW not in h


def test_change_password(browser, db_exec):
    a = browser()
    a.register("Masha")
    other = browser()
    other.login("Masha")
    assert other.get("/profile").status_code == 200
    # неверный старый
    r = a.post("/account/password", {"old": "wrong-pass", "new": "Novyi-parol-1", "new2": "Novyi-parol-1"},
               follow_redirects=True)
    assert "Текущий пароль неверный" in r.data.decode()
    # не совпадает повтор
    r = a.post("/account/password", {"old": PW, "new": "Novyi-parol-1", "new2": "Novyi-parol-2"}, follow_redirects=True)
    assert "не совпадают" in r.data.decode()
    # слишком простой
    r = a.post("/account/password", {"old": PW, "new": "zzzzzzzzzzz", "new2": "zzzzzzzzzzz"}, follow_redirects=True)
    assert "простой" in r.data.decode()
    # успех: текущая сессия остаётся, другая — выходит
    r = a.post("/account/password", {"old": PW, "new": "Novyi-parol-1", "new2": "Novyi-parol-1"}, follow_redirects=True)
    assert "Пароль изменён" in r.data.decode()
    assert a.get("/profile").status_code == 200
    assert other.get("/profile").status_code == 302
    assert browser().login("Masha", "Novyi-parol-1").status_code == 302
    assert browser().login("Masha", PW).status_code == 401


def test_change_nick_and_delete(browser, db_exec):
    a = browser()
    a.register("OldNick")
    browser().register("Taken")
    r = a.post("/account/nick", {"nick": "taken", "password": PW}, follow_redirects=True)
    assert "занят" in r.data.decode()
    r = a.post("/account/nick", {"nick": "NewNick", "password": "nope"}, follow_redirects=True)
    assert "неверный" in r.data.decode()
    a.post("/account/nick", {"nick": "NewNick", "password": PW})
    assert db_exec("SELECT COUNT(*) FROM users WHERE nick='NewNick'")[0][0] == 1
    # удаление
    a.post("/account/delete", {"password": "nope"})
    assert db_exec("SELECT COUNT(*) FROM users WHERE nick='NewNick'")[0][0] == 1
    a.post("/account/delete", {"password": PW})
    assert db_exec("SELECT COUNT(*) FROM users WHERE nick='NewNick'")[0][0] == 0


def test_delete_account_with_everything(browser, db_exec):
    """Удаление аккаунта, у которого есть партии, клуб, турнир, рейтинги, уведомления."""
    from test_online import start_game, play
    gid, w, b = start_game(browser)
    play(gid, w, b, ["e2e4", "e7e5"])
    w.api(f"/api/online/game/{gid}/resign")
    nick = db_exec("SELECT w_name FROM online_games WHERE id=?", (gid,))[0][0]
    db_exec("UPDATE users SET role='coach' WHERE nick=?", (nick,))
    w.post("/clubs/new", {"name": "Мой клуб", "join_policy": "open"})
    w.post("/tournaments/new", {"name": "Мой турнир", "rounds": "3", "tc": "600+5", "mode": "offline",
                                "system": "swiss", "bye_points": "1", "club_id": ""})
    tid = db_exec("SELECT id FROM tournaments")[0][0]
    w.post(f"/t/{tid}/join")
    r = w.post("/account/delete", {"password": PW})
    assert r.status_code == 302
    assert db_exec("SELECT COUNT(*) FROM users WHERE nick=?", (nick,))[0][0] == 0
    assert db_exec("SELECT user_id FROM t_players")[0][0] is None          # имя в турнире осталось
    assert db_exec("SELECT COUNT(*) FROM ratings")[0][0] == 1              # остался только соперник


# ---------------------------------------------------------------- лимиты и блокировки
def test_account_lockout_after_failed_logins(app, browser, db_exec):
    browser().register("Target")
    attacker = browser()
    n = app.config["LOCK_AFTER_FAILS"]
    for _ in range(n):
        assert attacker.login("Target", "wrong-password").status_code == 401
    # теперь даже верный пароль не пускает — аккаунт закрыт на время
    r = attacker.login("Target", PW)
    assert r.status_code == 429 and "закрыт" in r.data.decode()
    assert db_exec("SELECT COUNT(*) FROM login_log WHERE ok=0")[0][0] >= n
    # настоящий владелец с другого адреса входит спокойно — чужак не может закрыть ему вход
    owner = browser()
    r = owner.post("/login", {"nick": "Target", "password": PW}, environ_base={"REMOTE_ADDR": "10.0.0.7"})
    assert r.status_code == 302
    # а с адреса подбора — по-прежнему закрыто
    assert attacker.login("Target", PW).status_code == 429


def test_login_rate_limit_per_ip(app, browser):
    app.config["RATE_LOGIN_IP_PER_MIN"] = 5
    b = browser()
    codes = [b.login(f"nobody{i}", "x").status_code for i in range(7)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


def test_login_rate_limit_per_account(app, browser):
    app.config["RATE_LOGIN_NICK_PER_MIN"] = 3
    browser().register("Popular")
    codes = [browser().login("Popular", "wrong").status_code for _ in range(4)]   # с разных «устройств»
    assert codes[-1] == 429


def test_register_rate_limit(app, browser):
    app.config["RATE_REGISTER_IP_PER_HOUR"] = 2
    b = browser()
    b.register("First1")
    b2 = browser()
    b2.register("Second2")
    r = browser().post("/register", {"nick": "Third3", "password": PW, "password2": PW})
    assert r.status_code == 429


def test_api_rate_limit_per_user(app, browser):
    app.config["RATE_API_USER_PER_MIN"] = 10
    b = browser()
    b.register("Spammer")
    codes = [b.get("/api/online/status").status_code for _ in range(12)]
    assert 429 in codes


def test_global_ip_limit(app, browser):
    app.config["RATE_GLOBAL_PER_MIN"] = 20
    b = browser()
    codes = [b.get("/about").status_code for _ in range(25)]
    assert codes[-1] == 429


# ---------------------------------------------------------------- CSRF, заголовки, аватарки
def test_csrf_required_for_all_posts(app, browser):
    b = browser()
    b.register("Csrf")
    c = b.c
    for url in ("/logout", "/account/password", "/account/delete", "/api/online/seek", "/guest", "/clubs/new",
                "/api/avatar", "/api/puzzle-attempt", "/api/notifications/read"):
        assert c.post(url, data={}).status_code == 400, url
        assert c.post(url, json={}, headers={"X-CSRF-Token": "bad"}).status_code == 400, url
    # все маршруты с POST требуют токен (кроме проверенных выше — проверяем перебором правил)
    for rule in app.url_map.iter_rules():
        if "POST" in rule.methods and not rule.arguments:
            assert c.post(rule.rule, data={}).status_code in (400, 404), rule.rule


def test_security_headers(app, browser):
    r = app.test_client().get("/")
    h = r.headers
    assert "default-src 'self'" in h["Content-Security-Policy"]
    assert h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "same-origin"
    assert h["X-Content-Type-Options"] == "nosniff"
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    https = browser().c.get("/", base_url="https://localhost")
    assert "max-age" in https.headers.get("Strict-Transport-Security", "")


def test_secure_cookie_in_production(tmp_path, monkeypatch):
    monkeypatch.setenv("PRODUCTION", "1")
    app = create_app({"TESTING": True, "SECRET_KEY": "x", "DATABASE": str(tmp_path / "p.db")})
    r = app.test_client().get("/", base_url="https://localhost")
    assert "Secure" in r.headers["Set-Cookie"]


def test_avatar_upload_validation(browser):
    b = browser()
    b.register("Pic")
    up = lambda data: b.c.post("/api/avatar-upload", data=data, headers={"X-CSRF-Token": b.csrf,  # noqa: E731
                                                                         "Content-Type": "image/jpeg"})
    assert up(b"<svg onload=alert(1)>").status_code == 400                 # не картинка
    assert up(b"GIF89a" + b"0" * 100).status_code == 400                    # не тот формат
    assert up(b"\xff\xd8\xff" + b"0" * 100).status_code == 400              # обрезанный JPEG
    assert up(b"\xff\xd8\xff" + b"0" * (200 * 1024) + b"\xff\xd9").status_code in (400, 413)  # слишком большой
    r = up(b"\xff\xd8\xff\xe0" + b"0" * 1000 + b"\xff\xd9")
    assert r.status_code == 200
    img = b.get(r.get_json()["url"])
    assert img.mimetype == "image/jpeg" and img.headers["X-Content-Type-Options"] == "nosniff"


# ---------------------------------------------------------------- миграции и manage.py
OLD_SCHEMA = """
CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, nick TEXT NOT NULL UNIQUE COLLATE NOCASE,
  birth TEXT NOT NULL, pw_hash TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  avatar TEXT NOT NULL DEFAULT 'a1', rating INTEGER NOT NULL DEFAULT 100);
CREATE TABLE games(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
  bot INTEGER NOT NULL, result TEXT NOT NULL, moves TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  mode TEXT NOT NULL DEFAULT 'bot', delta INTEGER NOT NULL DEFAULT 0, opp TEXT);
CREATE TABLE solved(user_id INTEGER NOT NULL REFERENCES users(id), puzzle_id INTEGER NOT NULL, PRIMARY KEY(user_id, puzzle_id));
CREATE TABLE avatar_img(user_id INTEGER PRIMARY KEY REFERENCES users(id), mime TEXT NOT NULL, data BLOB NOT NULL, ver INTEGER NOT NULL);
CREATE TABLE online_games(id TEXT PRIMARY KEY, created REAL NOT NULL, tc INTEGER NOT NULL,
  w_pid TEXT NOT NULL, b_pid TEXT NOT NULL, w_name TEXT NOT NULL, b_name TEXT NOT NULL,
  w_uid INTEGER, b_uid INTEGER, w_rating INTEGER, b_rating INTEGER, rated INTEGER NOT NULL DEFAULT 0,
  moves TEXT NOT NULL DEFAULT '', w_ms INTEGER NOT NULL, b_ms INTEGER NOT NULL, last_ts REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'active', result TEXT, reason TEXT, draw_offer TEXT, w_delta INTEGER, b_delta INTEGER);
"""


def test_migration_keeps_old_data(tmp_path):
    from werkzeug.security import generate_password_hash
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript(OLD_SCHEMA)
    c.execute("INSERT INTO users(nick,birth,pw_hash,rating) VALUES('Ветеран','',?,345)",
              (generate_password_hash(PW),))
    c.execute("INSERT INTO games(user_id,bot,result,moves) VALUES(1,600,'win','e4 e5 Qh5 Nc6 Bc4 Nf6 Qxf7#')")
    c.execute("INSERT INTO solved VALUES(1, 3)")
    c.commit()
    c.close()
    app = create_app({"TESTING": True, "SECRET_KEY": "x", "DATABASE": str(path)})
    cl = app.test_client()
    cl.get("/")
    with cl.session_transaction() as s:
        tok = s["csrf"]
    r = cl.post("/login", data={"nick": "ветеран", "password": PW, "csrf": tok})
    assert r.status_code == 302
    html = cl.get("/profile").data.decode()
    assert "Ветеран" in html and "345" in html
    assert cl.get("/game/1").status_code == 200
    assert "Qxf7#" in cl.get("/game/1.pgn").data.decode()
    c = sqlite3.connect(path)
    assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] >= 7
    assert c.execute("SELECT role FROM users").fetchone()[0] == "user"
    c.close()
    assert any(p.name.startswith("backup-before-v1") for p in tmp_path.iterdir())
    # повторный запуск ничего не ломает
    create_app({"TESTING": True, "SECRET_KEY": "x", "DATABASE": str(path)})


def run_manage(db, *args, env=None, inp=None):
    e = dict(os.environ, LADYA_DB=str(db), **(env or {}))
    return subprocess.run([sys.executable, os.path.join(ROOT, "manage.py"), *args], capture_output=True, text=True,
                          env=e, input=inp, timeout=60)


def test_manage_create_admin_and_backup(tmp_path):
    db = tmp_path / "m.db"
    r = run_manage(db, "create-admin", "Elena", env={"ADMIN_PASSWORD": "12345678"})
    assert r.returncode == 1 and "известный" in r.stdout                   # простой пароль не принимается
    r = run_manage(db, "create-admin", "Elena", "--must-change", env={"ADMIN_PASSWORD": "Ochen-Slozhnyi-7"})
    assert r.returncode == 0, r.stdout + r.stderr
    c = sqlite3.connect(db)
    row = c.execute("SELECT role, must_change_pw, pw_hash FROM users WHERE nick='Elena'").fetchone()
    assert row[0] == "admin" and row[1] == 1 and row[2].startswith("scrypt:")
    c.close()
    # повышение существующего аккаунта без смены пароля
    c = sqlite3.connect(db)
    c.execute("INSERT INTO users(nick,birth,pw_hash) VALUES('Kirill','','x')")
    c.commit()
    c.close()
    r = run_manage(db, "create-admin", "kirill", inp="\n")
    assert r.returncode == 0 and "администратор" in r.stdout
    assert sqlite3.connect(db).execute("SELECT role FROM users WHERE nick='Kirill'").fetchone()[0] == "admin"
    # пароль в командной строке не принимается
    assert run_manage(db, "reset-password", "Kirill", "Parol123").returncode == 1
    r = run_manage(db, "backup")
    assert r.returncode == 0 and "ok" in r.stdout
    assert any(p.name.startswith("backup-") for p in tmp_path.iterdir())
    assert run_manage(db, "check").returncode == 0
    assert "Elena" in run_manage(db, "users").stdout
