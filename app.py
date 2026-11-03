import os, re, random, sqlite3, secrets, time
from collections import defaultdict, deque
from flask import (Flask, g, request, session, redirect, url_for, render_template,
                   jsonify, abort, flash)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from puzzles import PUZZLES
import online

BASE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(BASE, "instance"), exist_ok=True)
DB = os.path.join(BASE, "instance", "chess.db")
PROD = os.environ.get("PRODUCTION") == "1"
BOTS = {200, 600, 1000, 1500, 2000}
COACH_LEVELS = {600, 1200, 1800}  # с какой силой играет тренер Альберт
RATING_START = 100                 # стартовый (и минимальный) рейтинг игрока
PW_MIN = 6                         # минимальная длина пароля (сайт для шахматного клуба, в том числе для детей)
AUTH_PER_MIN = 60                  # входов/регистраций в минуту с одного IP (весь клуб может быть в одном Wi-Fi)
AVATAR_PRESETS = {f"a{i}" for i in range(1, 13)}
AVATAR_MAX = 150 * 1024
# допустимые форматы загружаемой аватарки: определяем по первым байтам файла, а не по имени
MAGIC = [(b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg")]


def get_secret():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    p = os.path.join(BASE, "instance", "secret.key")
    if not os.path.exists(p):
        with open(p, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(p, 0o600)
    return open(p).read().strip()


app = Flask(__name__)
app.config.update(
    SECRET_KEY=get_secret(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=PROD,
    PERMANENT_SESSION_LIFETIME=7 * 24 * 3600,
    MAX_CONTENT_LENGTH=AVATAR_MAX + 16 * 1024,
)
if os.environ.get("BEHIND_PROXY") == "1":  # за nginx / Cloudflare
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# ---------------- БД ----------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nick TEXT NOT NULL UNIQUE COLLATE NOCASE,
  birth TEXT NOT NULL,
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
"""


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
        # NOCASE в SQLite понимает только латиницу, поэтому для ников сравниваем через Python casefold
        g.db.create_function("fold", 1, lambda s: s.casefold() if isinstance(s, str) else s, deterministic=True)
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


with sqlite3.connect(DB) as _c:
    _c.executescript(SCHEMA)
    # миграция: колонка аватарки у старых баз ("a1".."a12" — готовая, "u:<версия>" — загруженная)
    if "avatar" not in {r[1] for r in _c.execute("PRAGMA table_info(users)")}:
        _c.execute("ALTER TABLE users ADD COLUMN avatar TEXT NOT NULL DEFAULT 'a1'")
    # миграция: режим партии ('bot' или 'coach'); старые партии с ботом 1200 — это тренер
    if "mode" not in {r[1] for r in _c.execute("PRAGMA table_info(games)")}:
        _c.execute("ALTER TABLE games ADD COLUMN mode TEXT NOT NULL DEFAULT 'bot'")
        _c.execute("UPDATE games SET mode='coach' WHERE bot=1200")
    # миграция: рейтинг игрока (старт 100) и изменение рейтинга за каждую партию
    if "rating" not in {r[1] for r in _c.execute("PRAGMA table_info(users)")}:
        _c.execute(f"ALTER TABLE users ADD COLUMN rating INTEGER NOT NULL DEFAULT {RATING_START}")
    if "delta" not in {r[1] for r in _c.execute("PRAGMA table_info(games)")}:
        _c.execute("ALTER TABLE games ADD COLUMN delta INTEGER NOT NULL DEFAULT 0")
    # дата рождения больше не нужна — стираем ранее сохранённые (меньше персональных данных)
    _c.execute("UPDATE users SET birth='' WHERE birth<>''")
    # онлайн-партии и имя соперника в истории партий
    _c.executescript(online.SCHEMA)
    if "opp" not in {r[1] for r in _c.execute("PRAGMA table_info(games)")}:
        _c.execute("ALTER TABLE games ADD COLUMN opp TEXT")


def rating_change(rating, opp, result):
    """Изменение рейтинга за партию. Победа: +7…+11, поражение: −7…−12, ничья: 0.
    Чем сильнее соперник относительно тебя, тем больше за победу и тем меньше теряешь за поражение.
    Рейтинг не опускается ниже 100."""
    x = max(0.0, min(1.0, (opp - rating + 400) / 800))   # 0 — соперник намного слабее, 1 — намного сильнее
    jitter = random.choice((-1, 0, 1))
    if result == "win":
        d = min(11, max(7, 7 + round(4 * x) + jitter))
    elif result == "loss":
        d = -min(12, max(7, 12 - round(5 * x) + jitter))
    else:
        d = 0
    new = max(RATING_START, rating + d)
    return new, new - rating


def avatar_url(u):
    if not u:
        return url_for("static", filename="avatars/a1.svg")
    a = u["avatar"] or "a1"
    if a.startswith("u:"):
        return url_for("avatar", uid=u["id"], v=a[2:])
    return url_for("static", filename=f"avatars/{a if a in AVATAR_PRESETS else 'a1'}.svg")

# ---------------- Защита: лимиты, CSRF, заголовки ----------------
hits = defaultdict(deque)
fails = defaultdict(deque)


def limited(store, key, limit, window):
    now = time.time()
    q = store[key]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    if len(store) > 20000:  # защита памяти
        for k in [k for k, v in store.items() if not v or v[-1] < now - 600]:
            store.pop(k, None)
    return False


@app.before_request
def guard():
    ip = request.remote_addr or "?"
    # Лимиты на один IP-адрес. Они щедрые специально: на занятии весь клуб сидит в одном Wi-Fi,
    # и для сайта все ученики выглядят как один адрес. Лимиты защищают только от явного флуда.
    if request.path.startswith("/static/"):
        if limited(hits, ("s", ip), 6000, 60):
            abort(429)
    elif limited(hits, ("p", ip), 1500, 60):
        abort(429)
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    if "sid" not in session:                     # анонимный идентификатор посетителя — для счётчика «на сайте»
        session["sid"] = secrets.token_hex(8)
    if not request.path.startswith("/static/"):
        online.touch(session["sid"])
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        tok = request.form.get("csrf") or request.headers.get("X-CSRF-Token") or ""
        if not secrets.compare_digest(tok, session["csrf"]):
            abort(400)
    g.user = None
    uid = session.get("uid")
    if uid:
        g.user = db().execute("SELECT id,nick,avatar,rating,created FROM users WHERE id=?", (uid,)).fetchone()
        if not g.user:
            session.pop("uid", None)


@app.after_request
def headers(resp):
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    if PROD:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.path.startswith("/api/") or request.endpoint in ("login", "register", "profile", "leaderboard"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.context_processor
def inject():
    u = g.get("user")
    return {"user": u, "csrf": session.get("csrf", ""), "avatar": avatar_url(u) if u else None,
            "presets": sorted(AVATAR_PRESETS, key=lambda s: int(s[1:]))}


def login_required(f):
    from functools import wraps

    @wraps(f)
    def w(*a, **k):
        if not g.user:
            if request.path.startswith("/api/"):
                return jsonify(error="auth"), 401
            return redirect(url_for("login"))
        return f(*a, **k)
    return w


# ---------------- Страницы ----------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/play")
def play():
    return render_template("play.html")


@app.route("/puzzles")
def puzzles():
    return render_template("puzzles.html")


@app.route("/knowledge")
def knowledge():
    return render_template("knowledge.html")


@app.route("/training")
def training():  # старый адрес раздела
    return redirect(url_for("knowledge"), 301)


@app.route("/avatar/<int:uid>")
def avatar(uid):
    row = db().execute("SELECT mime,data FROM avatar_img WHERE user_id=?", (uid,)).fetchone()
    if not row:
        abort(404)
    resp = app.response_class(row["data"], mimetype=row["mime"])
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # адрес меняется вместе с ?v=
    resp.headers["Content-Disposition"] = "inline"
    return resp


NICK_RE = re.compile(r"^[A-Za-z0-9_А-Яа-яЁё]{3,20}$")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        if limited(hits, ("auth", request.remote_addr), AUTH_PER_MIN, 60):
            abort(429)
        nick = request.form.get("nick", "").strip()
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        err = None
        if not NICK_RE.match(nick):
            err = "Ник: 3–20 символов, буквы, цифры и _."
        elif len(pw) < PW_MIN or len(pw) > 128:
            err = f"Пароль: от {PW_MIN} до 128 символов."
        elif pw != pw2:
            err = "Пароли не совпадают."
        if not err and db().execute("SELECT 1 FROM users WHERE fold(nick)=?", (nick.casefold(),)).fetchone():
            err = "Этот ник уже занят."
        if not err:
            try:
                # дату рождения больше не собираем (лишние персональные данные); колонка осталась пустой
                cur = db().execute("INSERT INTO users(nick,birth,pw_hash) VALUES(?,'',?)",
                                   (nick, generate_password_hash(pw)))
                db().commit()
                session.clear()
                session["uid"] = cur.lastrowid
                session["csrf"] = secrets.token_hex(16)
                session.permanent = True
                return redirect(url_for("profile"))
            except sqlite3.IntegrityError:
                err = "Этот ник уже занят."
        flash(err)
    return render_template("register.html")


DUMMY = generate_password_hash("dummy-password")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.remote_addr
        if limited(hits, ("auth", ip), AUTH_PER_MIN, 60):
            abort(429)
        nick = request.form.get("nick", "").strip().casefold()
        key = (nick, ip)
        if len([t for t in fails[key] if t > time.time() - 300]) >= 5:
            flash("Слишком много попыток. Подожди 5 минут.")
            return render_template("login.html")
        row = db().execute("SELECT id,pw_hash FROM users WHERE fold(nick)=?", (nick,)).fetchone()
        ok = check_password_hash(row["pw_hash"] if row else DUMMY, request.form.get("password", ""))
        if row and ok:
            fails.pop(key, None)
            session.clear()
            session["uid"] = row["id"]
            session["csrf"] = secrets.token_hex(16)
            session.permanent = True
            return redirect(url_for("index"))
        fails[key].append(time.time())
        flash("Неверный ник или пароль.")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/profile")
@login_required
def profile():
    d = db()
    stats = {r["result"]: r["n"] for r in d.execute(
        "SELECT result, COUNT(*) n FROM games WHERE user_id=? GROUP BY result", (g.user["id"],))}
    solved = d.execute("SELECT COUNT(*) FROM solved WHERE user_id=?", (g.user["id"],)).fetchone()[0]
    recent = d.execute("SELECT bot,result,created,mode,delta,opp FROM games WHERE user_id=? ORDER BY id DESC LIMIT 10",
                       (g.user["id"],)).fetchall()
    return render_template("profile.html", stats=stats, solved=solved, total=len(PUZZLES), recent=recent, pw_min=PW_MIN)


@app.route("/leaderboard")
def leaderboard():
    """Рейтинг клуба: все участники по рейтингу."""
    rows = db().execute("""
        SELECT u.id, u.nick, u.avatar, u.rating,
               (SELECT COUNT(*) FROM games WHERE user_id=u.id) AS games,
               (SELECT COUNT(*) FROM games WHERE user_id=u.id AND result='win') AS wins,
               (SELECT COUNT(*) FROM solved WHERE user_id=u.id) AS puzzles
        FROM users u ORDER BY u.rating DESC, wins DESC, u.id ASC LIMIT 100""").fetchall()
    players = [{**dict(r), "ava": avatar_url(r)} for r in rows]
    return render_template("leaderboard.html", players=players)


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/online")
def online_page():
    return render_template("online.html", me=online.me())


@app.route("/guest", methods=["POST"])
def guest():
    """Войти как гость: можно играть онлайн, но без рейтинга и сохранения партий."""
    if not g.user and "guest" not in session:
        session["guest"] = {"id": secrets.token_hex(6), "name": f"Гость-{secrets.randbelow(9000) + 1000}"}
    nxt = request.form.get("next", "")
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("online_page"))


@app.route("/favicon.ico")
def favicon():
    return redirect(url_for("static", filename="logo.svg"), 301)


def check_password(uid, pw):
    row = db().execute("SELECT pw_hash FROM users WHERE id=?", (uid,)).fetchone()
    return bool(row) and check_password_hash(row["pw_hash"], pw)


@app.route("/account/password", methods=["POST"])
@login_required
def change_password():
    old, new, new2 = (request.form.get(k, "") for k in ("old", "new", "new2"))
    if limited(hits, ("auth", request.remote_addr), AUTH_PER_MIN, 60):
        abort(429)
    if not check_password(g.user["id"], old):
        flash("Текущий пароль неверный.")
    elif len(new) < PW_MIN or len(new) > 128:
        flash(f"Новый пароль: от {PW_MIN} до 128 символов.")
    elif new != new2:
        flash("Новые пароли не совпадают.")
    else:
        db().execute("UPDATE users SET pw_hash=? WHERE id=?", (generate_password_hash(new), g.user["id"]))
        db().commit()
        flash("✔ Пароль изменён.", "ok")
    return redirect(url_for("profile"))


@app.route("/account/delete", methods=["POST"])
@login_required
def delete_account():
    if limited(hits, ("auth", request.remote_addr), AUTH_PER_MIN, 60):
        abort(429)
    if not check_password(g.user["id"], request.form.get("password", "")):
        flash("Пароль неверный — аккаунт не удалён.")
        return redirect(url_for("profile"))
    d, uid = db(), g.user["id"]
    for t in ("games", "solved", "avatar_img"):
        d.execute(f"DELETE FROM {t} WHERE user_id=?", (uid,))
    d.execute("DELETE FROM users WHERE id=?", (uid,))
    d.commit()
    session.clear()
    flash("Аккаунт и все его данные удалены.", "ok")
    return redirect(url_for("index"))


# ---------------- API ----------------
@app.route("/api/puzzles")
def api_puzzles():
    done = set()
    if g.user:
        done = {r[0] for r in db().execute("SELECT puzzle_id FROM solved WHERE user_id=?", (g.user["id"],))}
    return jsonify([{**p, "solved": p["id"] in done} for p in PUZZLES])


@app.route("/api/puzzle-solved", methods=["POST"])
@login_required
def api_solved():
    pid = (request.get_json(silent=True) or {}).get("id")
    if pid not in {p["id"] for p in PUZZLES}:
        return jsonify(error="bad id"), 400
    db().execute("INSERT OR IGNORE INTO solved VALUES(?,?)", (g.user["id"], pid))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/avatar", methods=["POST"])
@login_required
def api_avatar():
    preset = (request.get_json(silent=True) or {}).get("preset")
    if preset not in AVATAR_PRESETS:
        return jsonify(error="bad preset"), 400
    db().execute("UPDATE users SET avatar=? WHERE id=?", (preset, g.user["id"]))
    db().commit()
    return jsonify(ok=True, url=url_for("static", filename=f"avatars/{preset}.svg"))


@app.route("/api/avatar-upload", methods=["POST"])
@login_required
def api_avatar_upload():
    if limited(hits, ("avatar", g.user["id"]), 10, 60):
        abort(429)
    data = request.get_data(cache=False)
    mime = next((m for sig, m in MAGIC if data.startswith(sig)), None)
    if not data or len(data) > AVATAR_MAX or not mime:
        return jsonify(error="Нужна картинка PNG или JPEG до 150 КБ"), 400
    ver = int(time.time())
    d = db()
    d.execute("INSERT INTO avatar_img(user_id,mime,data,ver) VALUES(?,?,?,?) "
              "ON CONFLICT(user_id) DO UPDATE SET mime=excluded.mime, data=excluded.data, ver=excluded.ver",
              (g.user["id"], mime, data, ver))
    d.execute("UPDATE users SET avatar=? WHERE id=?", (f"u:{ver}", g.user["id"]))
    d.commit()
    return jsonify(ok=True, url=url_for("avatar", uid=g.user["id"], v=ver))


@app.route("/api/game-result", methods=["POST"])
@login_required
def api_game():
    d = request.get_json(silent=True) or {}
    mode = d.get("mode", "bot")
    allowed = COACH_LEVELS if mode == "coach" else BOTS if mode == "bot" else set()
    if d.get("bot") not in allowed or d.get("result") not in ("win", "loss", "draw"):
        return jsonify(error="bad data"), 400
    moves = str(d.get("moves", ""))[:4000]
    if limited(hits, ("game", g.user["id"]), 30, 60):
        abort(429)
    new, delta = rating_change(g.user["rating"], d["bot"], d["result"])
    db().execute("INSERT INTO games(user_id,bot,result,moves,mode,delta) VALUES(?,?,?,?,?,?)",
                 (g.user["id"], d["bot"], d["result"], moves, mode, delta))
    db().execute("UPDATE users SET rating=? WHERE id=?", (new, g.user["id"]))
    db().commit()
    return jsonify(ok=True, rating=new, delta=delta)


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(405)
@app.errorhandler(413)
@app.errorhandler(429)
@app.errorhandler(500)
def err(e):
    code = getattr(e, "code", 500) or 500
    msg = {400: "Неверный запрос (возможно, страница устарела — обнови её)", 404: "Страница не найдена",
           405: "Так эту страницу открыть нельзя", 413: "Файл слишком большой",
           429: "Слишком много запросов, подожди минуту", 500: "Что-то сломалось на сервере. Попробуй ещё раз"}.get(code, "Ошибка")
    if request.path.startswith("/api/"):
        return jsonify(error=msg), code
    return render_template("error.html", code=code, msg=msg), code


online.init(app, db, rating_change, limited, hits)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
