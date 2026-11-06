"""«Ладья» — сайт шахматного клуба. Фабрика приложения Flask."""
import os
import secrets
import time

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db as dbm
from . import security as sec

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AVATAR_PRESETS = {f"a{i}" for i in range(1, 13)}
AVATAR_MAX = 150 * 1024


def _secret(instance):
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    p = os.path.join(instance, "secret.key")
    if not os.path.exists(p):
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    with open(p) as f:
        return f.read().strip()


def create_app(config=None):
    instance = os.path.join(ROOT, "instance")
    app = Flask(__name__, root_path=ROOT, instance_path=instance,
                template_folder=os.path.join(ROOT, "templates"), static_folder=os.path.join(ROOT, "static"))
    config = dict(config or {})
    prod = os.environ.get("PRODUCTION") == "1"
    app.config.update(
        DATABASE=os.environ.get("LADYA_DB") or os.path.join(instance, "chess.db"),
        PRODUCTION=prod,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=prod,
        SESSION_COOKIE_NAME="ladya",
        PERMANENT_SESSION_LIFETIME=14 * 24 * 3600,
        MAX_CONTENT_LENGTH=AVATAR_MAX + 64 * 1024,
        TEMPLATES_AUTO_RELOAD=False,
        # лимиты (можно ослабить в тестах)
        RATE_GLOBAL_PER_MIN=1200,       # все запросы с одного IP (весь клуб может сидеть в одном Wi-Fi)
        RATE_STATIC_PER_MIN=6000,
        RATE_LOGIN_IP_PER_MIN=30,       # попыток входа с одного IP в минуту
        RATE_LOGIN_NICK_PER_MIN=10,     # попыток входа в один аккаунт в минуту (с любых IP)
        RATE_REGISTER_IP_PER_HOUR=30,
        RATE_API_USER_PER_MIN=400,      # запросов к /api/ от одного игрока
        LOCK_AFTER_FAILS=8,             # после стольких неудачных входов подряд аккаунт блокируется…
        LOCK_SECONDS=15 * 60,           # …на 15 минут
    )
    if "SECRET_KEY" not in config:
        os.makedirs(instance, exist_ok=True)
        config["SECRET_KEY"] = _secret(instance)
    app.config.update(config)
    app.extensions["ladya"] = {}          # память процесса: лимиты, «кто на сайте»

    hops = int(os.environ.get("PROXY_HOPS", "1" if os.environ.get("BEHIND_PROXY") == "1" else "0"))
    if hops > 0:   # за прокси хостинга (PythonAnywhere) и/или Cloudflare
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)

    dbm.migrate(app.config["DATABASE"], log=None if app.config.get("TESTING") else print)
    app.teardown_appcontext(dbm.close_db)

    from . import accounts, pages, online, clubs, tournaments, admin, notify
    for bp in (accounts.bp, pages.bp, online.bp, clubs.bp, tournaments.bp, admin.bp, notify.bp):
        app.register_blueprint(bp)

    # ---------------------------------------------------------------- каждый запрос
    @app.before_request
    def guard():
        ip = sec.client_ip()
        static = request.path.startswith("/static/")
        cfg = app.config
        if static:
            if sec.limited("ip", ("s", ip), cfg["RATE_STATIC_PER_MIN"], 60):
                return "Too many requests", 429
            return None
        if sec.limited("ip", ("p", ip), cfg["RATE_GLOBAL_PER_MIN"], 60):
            return sec.too_many()
        if "csrf" not in session:
            sec.new_csrf()
        if "sid" not in session:                 # анонимный идентификатор посетителя — для счётчика «на сайте»
            session["sid"] = secrets.token_hex(8)
        online.touch(session["sid"])
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            sec.check_csrf()
        g.user = None
        uid = session.get("uid")
        if uid:
            u = dbm.db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if not u or u["blocked"] or u["sess_ver"] != session.get("sv", 0):
                session.clear()
                sec.new_csrf()
            else:
                g.user = u
        if g.user and request.path.startswith("/api/") and \
                sec.limited("api", g.user["id"], cfg["RATE_API_USER_PER_MIN"], 60):
            return sec.too_many()
        # «сменить пароль при первом входе»: пока не сменит — пускаем только на страницу смены пароля
        if g.user and g.user["must_change_pw"] and request.endpoint not in (
                "accounts.force_password", "accounts.logout", "static", "pages.favicon"):
            if request.path.startswith("/api/"):
                return jsonify(error="Сначала смени пароль"), 403
            return redirect(url_for("accounts.force_password"))
        return None

    @app.after_request
    def after(resp):
        return sec.security_headers(resp)

    @app.context_processor
    def inject():
        u = g.get("user")
        unread = 0
        if u:
            unread = dbm.db().execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read=0",
                                      (u["id"],)).fetchone()[0]
        return {"user": u, "csrf": session.get("csrf", ""), "avatar": avatar_url(u) if u else None,
                "presets": sorted(AVATAR_PRESETS, key=lambda s: int(s[1:])), "is_admin": sec.is_admin(u),
                "is_coach": sec.is_coach(u), "unread": unread, "ROLE_NAMES": sec.ROLE_NAMES,
                "avatar_url": avatar_url, "now": time.time()}

    @app.template_filter("dt")
    def fmt_dt(v):
        if v is None or v == "":
            return ""
        if isinstance(v, (int, float)):
            return time.strftime("%d.%m.%Y %H:%M", time.localtime(v))
        s = str(v)
        return f"{s[8:10]}.{s[5:7]}.{s[0:4]}" + (f" {s[11:16]}" if len(s) > 11 else "")

    @app.template_filter("d")
    def fmt_d(v):
        return fmt_dt(v)[:10]

    @app.template_filter("pts")
    def fmt_pts(v):
        """Очки: 2.5 → «2½», 0.5 → «½», 7.25 → «7¼» (Бергер бывает с четвертями)."""
        v = float(v or 0)
        whole = int(v + 1e-9)
        frac = {0: "", 25: "¼", 50: "½", 75: "¾"}.get(round((v - whole) * 100))
        if frac is None:
            return f"{v:.2f}".rstrip("0").rstrip(".")
        return (str(whole) if whole or not frac else "") + frac

    # ---------------------------------------------------------------- ошибки
    MSG = {400: "Неверный запрос (возможно, страница устарела — обнови её)", 403: "Сюда нельзя — недостаточно прав",
           404: "Страница не найдена", 405: "Так эту страницу открыть нельзя", 413: "Файл слишком большой",
           429: "Слишком много запросов, подожди минуту", 500: "Что-то сломалось на сервере. Попробуй ещё раз"}

    def err(e):
        code = getattr(e, "code", 500) or 500
        msg = MSG.get(code, "Ошибка")
        if request.path.startswith("/api/"):
            return jsonify(error=msg), code
        try:
            return render_template("error.html", code=code, msg=msg), code
        except Exception:      # если сломан даже шаблон — отвечаем текстом
            return f"{code}: {msg}", code

    for code in (400, 403, 404, 405, 413, 429, 500):
        app.register_error_handler(code, err)

    return app


def avatar_url(u):
    if not u:
        return url_for("static", filename="avatars/a1.svg")
    a = u["avatar"] or "a1"
    if a.startswith("u:"):
        return url_for("accounts.avatar", uid=u["id"], v=a[2:])
    return url_for("static", filename=f"avatars/{a if a in AVATAR_PRESETS else 'a1'}.svg")
