"""База данных: подключение, транзакции и миграции схемы.

Миграции — это пронумерованные шаги в списке MIGRATIONS. Номер последнего применённого шага хранится
в таблице schema_version. При запуске сайта (и командой `python manage.py migrate`) применяются только
новые шаги, поэтому обновление сайта на хостинге не ломает существующую базу. Перед применением миграций
к уже существующей базе автоматически делается резервная копия instance/backup-before-vN-ДАТА.db.

Правило для разработчиков: уже выпущенные миграции НЕ меняем, только добавляем новые в конец.
"""
import os
import sqlite3
import time
from contextlib import contextmanager

from flask import current_app, g

RATING_START = 100


def connect(path):
    c = sqlite3.connect(path, timeout=15, isolation_level=None)   # autocommit; транзакции — через tx()
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=15000")
    # NOCASE в SQLite понимает только латиницу, поэтому ники сравниваем через Python casefold
    c.create_function("fold", 1, lambda s: s.casefold() if isinstance(s, str) else s, deterministic=True)
    return c


def db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
        g.tx_depth = 0
    return g.db


def close_db(_=None):
    d = g.pop("db", None)
    if d:
        d.close()


@contextmanager
def tx():
    """Транзакция с блокировкой записи (BEGIN IMMEDIATE): два одновременных запроса не испортят партию.
    Вложенные tx() работают как одна внешняя транзакция."""
    d = db()
    if g.tx_depth:
        g.tx_depth += 1
        try:
            yield d
        finally:
            g.tx_depth -= 1
        return
    d.execute("BEGIN IMMEDIATE")
    g.tx_depth = 1
    try:
        yield d
        d.execute("COMMIT")
    except BaseException:
        d.execute("ROLLBACK")
        raise
    finally:
        g.tx_depth = 0


# ---------------------------------------------------------------- миграции
def _cols(c, table):
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def _script(c, sql):
    """Выполнить несколько команд внутри текущей транзакции
    (executescript сам делает COMMIT, поэтому им пользоваться нельзя)."""
    for stmt in sql.split(";"):
        if stmt.strip():
            c.execute(stmt)


def _add_col(c, table, col, ddl):
    if col not in _cols(c, table):
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def m1_baseline(c):
    """Схема первой версии сайта (включая её старые «самодельные» миграции)."""
    _script(c, """
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      nick TEXT NOT NULL UNIQUE COLLATE NOCASE,
      birth TEXT NOT NULL DEFAULT '',
      pw_hash TEXT NOT NULL,
      created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS games(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id),
      bot INTEGER NOT NULL, result TEXT NOT NULL, moves TEXT NOT NULL,
      created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS solved(
      user_id INTEGER NOT NULL REFERENCES users(id),
      puzzle_id INTEGER NOT NULL,
      PRIMARY KEY(user_id, puzzle_id));
    CREATE TABLE IF NOT EXISTS avatar_img(
      user_id INTEGER PRIMARY KEY REFERENCES users(id),
      mime TEXT NOT NULL, data BLOB NOT NULL, ver INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS online_games(
      id TEXT PRIMARY KEY, created REAL NOT NULL, tc INTEGER NOT NULL,
      w_pid TEXT NOT NULL, b_pid TEXT NOT NULL, w_name TEXT NOT NULL, b_name TEXT NOT NULL,
      w_uid INTEGER, b_uid INTEGER, w_rating INTEGER, b_rating INTEGER, rated INTEGER NOT NULL DEFAULT 0,
      moves TEXT NOT NULL DEFAULT '', w_ms INTEGER NOT NULL, b_ms INTEGER NOT NULL, last_ts REAL NOT NULL,
      status TEXT NOT NULL DEFAULT 'active', result TEXT, reason TEXT, draw_offer TEXT,
      w_delta INTEGER, b_delta INTEGER);
    CREATE INDEX IF NOT EXISTS og_status ON online_games(status);
    """)
    _add_col(c, "users", "avatar", "TEXT NOT NULL DEFAULT 'a1'")
    if "mode" not in _cols(c, "games"):
        c.execute("ALTER TABLE games ADD COLUMN mode TEXT NOT NULL DEFAULT 'bot'")
        c.execute("UPDATE games SET mode='coach' WHERE bot=1200")
    _add_col(c, "users", "rating", f"INTEGER NOT NULL DEFAULT {RATING_START}")
    _add_col(c, "games", "delta", "INTEGER NOT NULL DEFAULT 0")
    _add_col(c, "games", "opp", "TEXT")
    c.execute("UPDATE users SET birth='' WHERE birth<>''")


def m2_roles_security(c):
    """Роли, блокировка, смена пароля при входе, журнал входов, жалобы, журнал действий админа."""
    _add_col(c, "users", "role", "TEXT NOT NULL DEFAULT 'user'")          # admin / coach / user
    _add_col(c, "users", "blocked", "INTEGER NOT NULL DEFAULT 0")
    _add_col(c, "users", "must_change_pw", "INTEGER NOT NULL DEFAULT 0")
    _add_col(c, "users", "sess_ver", "INTEGER NOT NULL DEFAULT 0")          # смена → все сессии выходят
    _add_col(c, "users", "failed_logins", "INTEGER NOT NULL DEFAULT 0")
    _add_col(c, "users", "locked_until", "REAL NOT NULL DEFAULT 0")
    _add_col(c, "users", "last_login", "TEXT")
    _script(c, """
    CREATE TABLE IF NOT EXISTS login_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, ip TEXT, nick TEXT, user_id INTEGER,
      ok INTEGER NOT NULL, note TEXT);
    CREATE INDEX IF NOT EXISTS login_log_ts ON login_log(ts);
    CREATE TABLE IF NOT EXISTS admin_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, admin_id INTEGER, action TEXT NOT NULL, details TEXT);
    CREATE TABLE IF NOT EXISTS reports(
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, from_uid INTEGER, target_uid INTEGER,
      target_name TEXT, game_id TEXT, reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
      resolved_by INTEGER, resolved_ts REAL);
    """)


def m3_online_v2(c):
    """Онлайн: прибавка времени, пул рейтинга, версия состояния (лёгкий опрос), присутствие игроков,
    реванш, вызовы, очередь поиска в базе; рейтинги Glicko-2 по контролям и история рейтинга."""
    for col, ddl in (("inc", "INTEGER NOT NULL DEFAULT 0"), ("pool", "TEXT NOT NULL DEFAULT 'blitz'"),
                     ("ver", "INTEGER NOT NULL DEFAULT 0"), ("w_seen", "REAL NOT NULL DEFAULT 0"),
                     ("b_seen", "REAL NOT NULL DEFAULT 0"), ("rematch_offer", "TEXT"), ("rematch_gid", "TEXT"),
                     ("pairing_id", "INTEGER"), ("ended", "REAL"), ("start_wait", "INTEGER NOT NULL DEFAULT 30")):
        _add_col(c, "online_games", col, ddl)
    _script(c, """
    CREATE TABLE IF NOT EXISTS seeks(
      pid TEXT PRIMARY KEY, name TEXT NOT NULL, uid INTEGER, rating INTEGER, tc INTEGER NOT NULL,
      inc INTEGER NOT NULL, ts REAL NOT NULL, since REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS challenges(
      id TEXT PRIMARY KEY, created REAL NOT NULL, from_pid TEXT NOT NULL, from_name TEXT NOT NULL,
      from_uid INTEGER, to_uid INTEGER, tc INTEGER NOT NULL, inc INTEGER NOT NULL,
      color TEXT NOT NULL DEFAULT 'random', rated INTEGER NOT NULL DEFAULT 1,
      status TEXT NOT NULL DEFAULT 'open', game_id TEXT);
    CREATE INDEX IF NOT EXISTS ch_to ON challenges(to_uid, status);
    CREATE TABLE IF NOT EXISTS ratings(
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, pool TEXT NOT NULL,
      rating REAL NOT NULL, rd REAL NOT NULL, vol REAL NOT NULL, games INTEGER NOT NULL DEFAULT 0,
      updated REAL NOT NULL DEFAULT 0, PRIMARY KEY(user_id, pool));
    CREATE TABLE IF NOT EXISTS rating_history(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      pool TEXT NOT NULL, rating INTEGER NOT NULL, ts REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS rh_user ON rating_history(user_id, pool, ts);
    """)
    # старые партии были без прибавки; пул — по базовому времени
    _add_col(c, "games", "online_id", "TEXT")
    _add_col(c, "games", "color", "TEXT")              # каким цветом играл человек (w/b)          # ссылка на онлайн-партию (для просмотра и PGN)
    c.execute("UPDATE online_games SET pool=CASE WHEN tc<180 THEN 'bullet' WHEN tc<480 THEN 'blitz' "
              "WHEN tc<1500 THEN 'rapid' ELSE 'classical' END")


def m4_clubs(c):
    _script(c, """
    CREATE TABLE IF NOT EXISTS clubs(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
      owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL, invite_code TEXT NOT NULL UNIQUE,
      join_policy TEXT NOT NULL DEFAULT 'request', created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS club_members(
      club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      role TEXT NOT NULL DEFAULT 'member', joined TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(club_id, user_id));
    CREATE INDEX IF NOT EXISTS cm_user ON club_members(user_id);
    CREATE TABLE IF NOT EXISTS club_requests(
      club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      message TEXT NOT NULL DEFAULT '', created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(club_id, user_id));
    """)


def m5_tournaments(c):
    _script(c, """
    CREATE TABLE IF NOT EXISTS tournaments(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
      club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
      owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
      rounds INTEGER NOT NULL, tc INTEGER NOT NULL, inc INTEGER NOT NULL DEFAULT 0,
      mode TEXT NOT NULL DEFAULT 'offline', status TEXT NOT NULL DEFAULT 'registration',
      current_round INTEGER NOT NULL DEFAULT 0, bye_points REAL NOT NULL DEFAULT 1,
      system TEXT NOT NULL DEFAULT 'swiss', starts TEXT, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE INDEX IF NOT EXISTS t_club ON tournaments(club_id, status);
    CREATE TABLE IF NOT EXISTS t_players(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
      user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
      name TEXT NOT NULL, rating INTEGER NOT NULL DEFAULT 0, withdrawn INTEGER NOT NULL DEFAULT 0,
      joined_round INTEGER NOT NULL DEFAULT 1, seed INTEGER);
    CREATE INDEX IF NOT EXISTS tp_t ON t_players(tournament_id);
    CREATE UNIQUE INDEX IF NOT EXISTS tp_user ON t_players(tournament_id, user_id) WHERE user_id IS NOT NULL;
    CREATE TABLE IF NOT EXISTS t_pairings(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
      round INTEGER NOT NULL, board INTEGER NOT NULL,
      white_id INTEGER NOT NULL REFERENCES t_players(id) ON DELETE CASCADE,
      black_id INTEGER REFERENCES t_players(id) ON DELETE CASCADE,
      result TEXT, game_id TEXT);
    CREATE INDEX IF NOT EXISTS tpair_tr ON t_pairings(tournament_id, round);
    """)


def m6_notifications_puzzles(c):
    _script(c, """
    CREATE TABLE IF NOT EXISTS notifications(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      ts REAL NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, url TEXT, read INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS notif_user ON notifications(user_id, read, id);
    CREATE TABLE IF NOT EXISTS puzzle_attempts(
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, puzzle_id INTEGER NOT NULL,
      ok INTEGER NOT NULL, ts REAL NOT NULL, PRIMARY KEY(user_id, puzzle_id));
    """)


def m7_indexes(c):
    _script(c, """
    CREATE INDEX IF NOT EXISTS games_user ON games(user_id, id);
    CREATE INDEX IF NOT EXISTS og_w ON online_games(w_pid, status);
    CREATE INDEX IF NOT EXISTS og_b ON online_games(b_pid, status);
    CREATE INDEX IF NOT EXISTS og_wuid ON online_games(w_uid, created);
    CREATE INDEX IF NOT EXISTS og_buid ON online_games(b_uid, created);
    CREATE INDEX IF NOT EXISTS og_pairing ON online_games(pairing_id);
    CREATE INDEX IF NOT EXISTS users_rating ON users(rating);
    CREATE INDEX IF NOT EXISTS reports_status ON reports(status, ts);
    """)


MIGRATIONS = [
    (1, "Базовая схема", m1_baseline),
    (2, "Роли и безопасность", m2_roles_security),
    (3, "Онлайн-игра v2 и рейтинги Glicko-2", m3_online_v2),
    (4, "Клубы", m4_clubs),
    (5, "Турниры (швейцарская система)", m5_tournaments),
    (6, "Уведомления и задачи", m6_notifications_puzzles),
    (7, "Индексы", m7_indexes),
]
LATEST = MIGRATIONS[-1][0]


def current_version(c):
    c.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY, name TEXT, applied REAL)")
    return c.execute("SELECT COALESCE(MAX(version),0) FROM schema_version").fetchone()[0]


def migrate(path, backup=True, log=print):
    """Применить новые миграции. Возвращает список применённых номеров."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    existed = os.path.exists(path) and os.path.getsize(path) > 0
    c = connect(path)
    try:
        ver = current_version(c)
        todo = [m for m in MIGRATIONS if m[0] > ver]
        if not todo:
            return []
        has_data = existed and c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
        if backup and has_data:
            dst = os.path.join(os.path.dirname(path), time.strftime(f"backup-before-v{todo[0][0]}-%Y-%m-%d_%H-%M-%S.db"))
            with sqlite3.connect(dst) as b:
                c.backup(b)
            log and log(f"Резервная копия перед обновлением базы: {dst}")
        for num, name, fn in todo:
            c.execute("BEGIN IMMEDIATE")
            try:
                fn(c)
                c.execute("INSERT INTO schema_version(version,name,applied) VALUES(?,?,?)", (num, name, time.time()))
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            log and log(f"Миграция {num}: {name} — применена")
        return [m[0] for m in todo]
    finally:
        c.close()
