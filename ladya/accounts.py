"""Аккаунты: регистрация, вход (с защитой от подбора), гость, профиль, смена пароля/ника/аватарки, удаление."""
import json
import re
import secrets
import sqlite3
import time

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import AVATAR_MAX, AVATAR_PRESETS, avatar_url
from . import ratings as rt
from .db import db, tx
from .security import (client_ip, limited, login_required, new_csrf, password_problem, safe_next, PW_MIN, PW_MAX)

bp = Blueprint("accounts", __name__)
NICK_RE = re.compile(r"^[A-Za-z0-9_А-Яа-яЁё]{3,20}$")
NICK_RULES = "Ник: 3–20 символов — буквы, цифры и _"
MAGIC = [(b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg")]
DUMMY = generate_password_hash("dummy-password-for-timing")


def hash_pw(pw):
    # scrypt — по умолчанию в werkzeug 3; явно, чтобы не зависеть от версии
    return generate_password_hash(pw, method="scrypt")


def find_user(d, nick):
    return d.execute("SELECT * FROM users WHERE fold(nick)=?", (nick.strip().casefold(),)).fetchone()


def log_login(d, nick, uid, ok, note=""):
    d.execute("INSERT INTO login_log(ts,ip,nick,user_id,ok,note) VALUES(?,?,?,?,?,?)",
              (time.time(), client_ip(), nick[:40], uid, 1 if ok else 0, note))
    d.execute("DELETE FROM login_log WHERE ts < ?", (time.time() - 90 * 86400,))   # храним 90 дней


def start_session(u):
    session.clear()
    session["uid"] = u["id"]
    session["sv"] = u["sess_ver"]
    session["sid"] = secrets.token_hex(8)
    new_csrf()
    session.permanent = True


def delete_user(d, uid):
    """Удалить пользователя и всё личное. Турнирные таблицы сохраняются (имя остаётся, связь с аккаунтом — нет)."""
    for t in ("games", "solved", "avatar_img"):
        d.execute(f"DELETE FROM {t} WHERE user_id=?", (uid,))
    d.execute("DELETE FROM challenges WHERE from_uid=? OR to_uid=?", (uid, uid))
    d.execute("DELETE FROM seeks WHERE uid=?", (uid,))
    d.execute("UPDATE online_games SET w_uid=NULL WHERE w_uid=?", (uid,))
    d.execute("UPDATE online_games SET b_uid=NULL WHERE b_uid=?", (uid,))
    d.execute("UPDATE reports SET from_uid=NULL WHERE from_uid=?", (uid,))
    d.execute("DELETE FROM users WHERE id=?", (uid,))   # остальное удалится каскадом


# ---------------------------------------------------------------- регистрация и вход
@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        cfg = current_app.config
        if limited("register", client_ip(), cfg["RATE_REGISTER_IP_PER_HOUR"], 3600):
            abort(429)
        nick = request.form.get("nick", "").strip()
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        err = None
        if not NICK_RE.match(nick):
            err = NICK_RULES + "."
        elif pw != pw2:
            err = "Пароли не совпадают."
        else:
            err = password_problem(pw, nick)
        d = db()
        if not err and find_user(d, nick):
            err = "Этот ник уже занят."
        if not err:
            try:
                cur = d.execute("INSERT INTO users(nick,birth,pw_hash) VALUES(?,'',?)", (nick, hash_pw(pw)))
                u = d.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
                start_session(u)
                flash("Добро пожаловать в клуб!", "ok")
                return redirect(safe_next(request.args.get("next"), url_for("accounts.profile")))
            except sqlite3.IntegrityError:
                err = "Этот ник уже занят."
        flash(err)
    return render_template("register.html", pw_min=PW_MIN)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        cfg = current_app.config
        nick = request.form.get("nick", "").strip()[:40]
        pw = request.form.get("password", "")
        if limited("login-ip", client_ip(), cfg["RATE_LOGIN_IP_PER_MIN"], 60) or \
                limited("login-nick", nick.casefold(), cfg["RATE_LOGIN_NICK_PER_MIN"], 60):
            flash("Слишком много попыток входа. Подожди минуту.")
            return render_template("login.html"), 429
        d = db()
        u = find_user(d, nick) if nick else None
        now = time.time()
        if u and u["locked_until"] > now:
            mins = int((u["locked_until"] - now) // 60) + 1
            check_password_hash(DUMMY, pw)
            log_login(d, nick, u["id"], False, "locked")
            flash(f"Слишком много неудачных попыток. Вход в этот аккаунт закрыт ещё на {mins} мин.")
            return render_template("login.html"), 429
        ok = check_password_hash(u["pw_hash"] if u else DUMMY, pw)
        if u and ok and u["blocked"]:
            log_login(d, nick, u["id"], False, "blocked")
            flash("Аккаунт заблокирован администратором клуба.")
            return render_template("login.html"), 403
        if u and ok:
            d.execute("UPDATE users SET failed_logins=0, locked_until=0, last_login=CURRENT_TIMESTAMP WHERE id=?",
                      (u["id"],))
            log_login(d, nick, u["id"], True)
            start_session(u)
            if u["must_change_pw"]:
                return redirect(url_for("accounts.force_password"))
            return redirect(safe_next(request.args.get("next") or request.form.get("next"), url_for("pages.index")))
        if u:
            fails = u["failed_logins"] + 1
            if fails >= cfg["LOCK_AFTER_FAILS"]:
                d.execute("UPDATE users SET failed_logins=0, locked_until=? WHERE id=?",
                          (now + cfg["LOCK_SECONDS"], u["id"]))
                log_login(d, nick, u["id"], False, "lock")
            else:
                d.execute("UPDATE users SET failed_logins=? WHERE id=?", (fails, u["id"]))
                log_login(d, nick, u["id"], False)
        else:
            log_login(d, nick, None, False, "no-user")
        flash("Неверный ник или пароль.")
        return render_template("login.html"), 401
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    new_csrf()
    return redirect(url_for("pages.index"))


@bp.route("/guest", methods=["POST"])
def guest():
    """Войти как гость: можно играть онлайн, но без рейтинга и сохранения партий."""
    if not g.user and "guest" not in session:
        if limited("guest", client_ip(), 60, 3600):
            abort(429)
        session["guest"] = {"id": secrets.token_hex(6), "name": f"Гость-{secrets.randbelow(9000) + 1000}"}
    return redirect(safe_next(request.form.get("next"), url_for("online.lobby")))


# ---------------------------------------------------------------- пароль
def _set_password(d, uid, pw, must_change=0):
    """Новый пароль. Все прочие сессии этого аккаунта завершаются (sess_ver + 1)."""
    d.execute("UPDATE users SET pw_hash=?, must_change_pw=?, sess_ver=sess_ver+1, failed_logins=0, locked_until=0 "
              "WHERE id=?", (hash_pw(pw), must_change, uid))


def _change_password_form(require_old=True):
    old, new, new2 = (request.form.get(k, "") for k in ("old", "new", "new2"))
    if limited("pwchange", g.user["id"], 10, 600):
        return "Слишком много попыток. Подожди 10 минут."
    if require_old and not check_password_hash(g.user["pw_hash"], old):
        return "Текущий пароль неверный."
    if new != new2:
        return "Новые пароли не совпадают."
    prob = password_problem(new, g.user["nick"])
    if prob:
        return prob
    if check_password_hash(g.user["pw_hash"], new):
        return "Новый пароль совпадает со старым."
    d = db()
    _set_password(d, g.user["id"], new)
    u = d.execute("SELECT * FROM users WHERE id=?", (g.user["id"],)).fetchone()
    start_session(u)          # эта сессия остаётся, остальные (на других устройствах) выходят
    return None


@bp.route("/account/password", methods=["POST"])
@login_required
def change_password():
    err = _change_password_form()
    flash(err or "✔ Пароль изменён. На других устройствах нужно будет войти заново.", None if err else "ok")
    return redirect(url_for("accounts.settings"))


@bp.route("/account/new-password", methods=["GET", "POST"])
@login_required
def force_password():
    """Обязательная смена пароля (аккаунт создан администратором или пароль сброшен)."""
    if not g.user["must_change_pw"]:
        return redirect(url_for("accounts.settings"))
    if request.method == "POST":
        err = _change_password_form()
        if not err:
            flash("✔ Пароль изменён. Добро пожаловать!", "ok")
            return redirect(url_for("pages.index"))
        flash(err)
    return render_template("force_password.html", pw_min=PW_MIN)


# ---------------------------------------------------------------- ник, удаление
@bp.route("/account/nick", methods=["POST"])
@login_required
def change_nick():
    nick = request.form.get("nick", "").strip()
    d = db()
    if not NICK_RE.match(nick):
        flash(NICK_RULES + ".")
    elif not check_password_hash(g.user["pw_hash"], request.form.get("password", "")):
        flash("Пароль неверный — ник не изменён.")
    elif limited("nick", g.user["id"], 3, 86400):
        flash("Ник можно менять не чаще 3 раз в сутки.")
    else:
        other = find_user(d, nick)
        if other and other["id"] != g.user["id"]:
            flash("Этот ник уже занят.")
        else:
            try:
                d.execute("UPDATE users SET nick=? WHERE id=?", (nick, g.user["id"]))
                flash("✔ Ник изменён.", "ok")
            except sqlite3.IntegrityError:
                flash("Этот ник уже занят.")
    return redirect(url_for("accounts.settings"))


@bp.route("/account/delete", methods=["POST"])
@login_required
def delete_account():
    if limited("pwchange", g.user["id"], 10, 600):
        abort(429)
    if not check_password_hash(g.user["pw_hash"], request.form.get("password", "")):
        flash("Пароль неверный — аккаунт не удалён.")
        return redirect(url_for("accounts.settings"))
    d = db()
    if g.user["role"] == "admin" and d.execute(
            "SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] <= 1:
        flash("Ты единственный администратор — сначала назначь другого.")
        return redirect(url_for("accounts.settings"))
    with tx():
        delete_user(d, g.user["id"])
    session.clear()
    new_csrf()
    flash("Аккаунт и все его данные удалены.", "ok")
    return redirect(url_for("pages.index"))


@bp.route("/account")
@login_required
def settings():
    return render_template("settings.html", pw_min=PW_MIN, pw_max=PW_MAX)


# ---------------------------------------------------------------- аватарки
@bp.route("/avatar/<int:uid>")
def avatar(uid):
    row = db().execute("SELECT mime,data FROM avatar_img WHERE user_id=?", (uid,)).fetchone()
    if not row:
        abort(404)
    resp = current_app.response_class(row["data"], mimetype=row["mime"])
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # адрес меняется вместе с ?v=
    resp.headers["Content-Disposition"] = "inline"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp


@bp.route("/api/avatar", methods=["POST"])
@login_required
def api_avatar():
    preset = (request.get_json(silent=True) or {}).get("preset")
    if preset not in AVATAR_PRESETS:
        return jsonify(error="Неизвестная аватарка"), 400
    db().execute("UPDATE users SET avatar=? WHERE id=?", (preset, g.user["id"]))
    return jsonify(ok=True, url=url_for("static", filename=f"avatars/{preset}.svg"))


def image_ok(data):
    """Проверка картинки: формат по сигнатуре (PNG/JPEG), размер, и что файл не обрезан."""
    if not data or len(data) > AVATAR_MAX:
        return None
    mime = next((m for sig, m in MAGIC if data.startswith(sig)), None)
    if mime == "image/png" and not data.rstrip(b"\x00").endswith(b"IEND\xaeB`\x82"):
        return None
    if mime == "image/jpeg" and b"\xff\xd9" not in data[-64:]:
        return None
    return mime


@bp.route("/api/avatar-upload", methods=["POST"])
@login_required
def api_avatar_upload():
    if limited("avatar", g.user["id"], 10, 600):
        return jsonify(error="Слишком часто. Подожди немного."), 429
    data = request.get_data(cache=False)
    mime = image_ok(data)
    if not mime:
        return jsonify(error="Нужна картинка PNG или JPEG до 150 КБ"), 400
    ver = int(time.time())
    with tx() as d:
        d.execute("INSERT INTO avatar_img(user_id,mime,data,ver) VALUES(?,?,?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET mime=excluded.mime, data=excluded.data, ver=excluded.ver",
                  (g.user["id"], mime, data, ver))
        d.execute("UPDATE users SET avatar=? WHERE id=?", (f"u:{ver}", g.user["id"]))
    return jsonify(ok=True, url=url_for("accounts.avatar", uid=g.user["id"], v=ver))


# ---------------------------------------------------------------- профиль
def profile_data(u):
    d = db()
    uid = u["id"]
    stats = {r["result"]: r["n"] for r in d.execute(
        "SELECT result, COUNT(*) n FROM games WHERE user_id=? GROUP BY result", (uid,))}
    from .puzzles import PUZZLES
    solved = d.execute("SELECT COUNT(*) FROM solved WHERE user_id=?", (uid,)).fetchone()[0]
    page = max(1, request.args.get("page", 1, type=int))
    per = 20
    recent = d.execute("SELECT id,bot,result,created,mode,delta,opp,online_id FROM games WHERE user_id=? "
                       "ORDER BY id DESC LIMIT ? OFFSET ?", (uid, per + 1, (page - 1) * per)).fetchall()
    more = len(recent) > per
    hist = {}
    for r in d.execute("SELECT pool, rating, ts FROM rating_history WHERE user_id=? ORDER BY ts", (uid,)):
        hist.setdefault(r["pool"], []).append([int(r["ts"]), r["rating"]])
    clubs = d.execute("SELECT c.id, c.name, m.role FROM club_members m JOIN clubs c ON c.id=m.club_id "
                      "WHERE m.user_id=? ORDER BY c.name", (uid,)).fetchall()
    return dict(stats=stats, solved=solved, total=len(PUZZLES), recent=recent[:per], page=page, more=more,
                ratings=rt.all_for(d, uid), pools=rt.POOLS, hist_json=json.dumps(hist), clubs=clubs,
                ava=avatar_url(u))


@bp.route("/profile")
@login_required
def profile():
    return render_template("profile.html", u=g.user, own=True, **profile_data(g.user))


@bp.route("/u/<nick>")
def public_profile(nick):
    u = find_user(db(), nick)
    if not u:
        abort(404)
    if g.user and u["id"] == g.user["id"]:
        return redirect(url_for("accounts.profile"))
    return render_template("profile.html", u=u, own=False, **profile_data(u))


@bp.route("/api/users")
def api_users():
    """Подсказка ников (вызов на партию, добавление в турнир)."""
    q = request.args.get("q", "").strip().casefold()
    if len(q) < 2 or not g.get("user"):
        return jsonify([])
    rows = db().execute("SELECT nick FROM users WHERE fold(nick) LIKE ? ESCAPE '\\' AND blocked=0 ORDER BY nick LIMIT 10",
                        (q.replace("%", "").replace("_", "\\_") + "%",)).fetchall()
    return jsonify([r["nick"] for r in rows])
