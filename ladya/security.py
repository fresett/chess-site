"""Защита: ограничение частоты запросов, CSRF, заголовки безопасности, проверка паролей, роли."""
import secrets
import time
from collections import defaultdict, deque
from functools import wraps

from flask import abort, current_app, g, jsonify, redirect, request, session, url_for

PW_MIN = 8
PW_MAX = 128

# Самые частые пароли (в т. ч. «русские» раскладки) — такие не принимаем.
COMMON = set("""
12345678 123456789 1234567890 0123456789 87654321 11111111 00000000 12341234 12121212 11223344 1q2w3e4r
1q2w3e4r5t qwertyui qwertyuiop qwerty123 qwerty12 asdfghjk asdfghjkl zxcvbnm1 password password1 passw0rd
iloveyou sunshine princess football baseball welcome1 letmein1 trustno1 superman batman12 dragon12 monkey12
abc12345 abcd1234 aaaaaaaa zzzzzzzz 1qaz2wsx qazwsxedc 123qweasd 123123123 12344321 55555555 77777777
йцукенгш йцукенгшщз фывапрол фывапролд пароль12 пароль123 парольпароль ячсмитьб привет12 привет123
шахматы1 шахматы12 шахматы123 chess123 chess1234 chessclub ladya123 ладья123 ладья1234
""".split())


def password_problem(pw, nick=""):
    """Текст ошибки, если пароль не годится, иначе None."""
    if len(pw) < PW_MIN:
        return f"Пароль слишком короткий — нужно не меньше {PW_MIN} символов."
    if len(pw) > PW_MAX:
        return f"Пароль слишком длинный — не больше {PW_MAX} символов."
    low = pw.casefold()
    if low in COMMON:
        return "Этот пароль слишком известный — его подбирают первым. Придумай другой."
    if len(set(low)) <= 2:
        return "Пароль из одного-двух повторяющихся символов слишком простой."
    if low.isdigit():
        digits = [int(ch) for ch in low]
        steps = {b - a for a, b in zip(digits, digits[1:])}
        if steps <= {1, -9} or steps <= {-1, 9}:
            return "Пароль из цифр подряд слишком простой."
    if nick and len(nick) >= 3 and nick.casefold() in low:
        return "Пароль не должен содержать ник."
    return None


# ---------------------------------------------------------------- лимиты
def _stores():
    return current_app.extensions["ladya"]


def limited(bucket, key, limit, window):
    """True — если превышен лимит `limit` событий за `window` секунд. Иначе событие учитывается."""
    store = _stores().setdefault(bucket, defaultdict(deque))
    now = time.time()
    q = store[key]
    while q and q[0] <= now - window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    if len(store) > 20000:                       # защита памяти от «распыления» ключей
        for k in [k for k, v in store.items() if not v or v[-1] < now - 3600]:
            store.pop(k, None)
    return False


def _fails():
    return _stores().setdefault("login-fails", {})


def lock_left(uid, ip):
    """Сколько секунд ещё закрыт вход в аккаунт uid с адреса ip (0 — открыт).
    Блокировка — по паре «аккаунт + адрес»: подбирающий пароль чужак не закрывает вход настоящему владельцу."""
    rec = _fails().get((uid, ip))
    return max(0.0, rec["until"] - time.time()) if rec else 0.0


def add_fail(uid, ip, limit, seconds):
    """Учесть неверный пароль. True — если после этого вход с этого адреса закрыт."""
    store = _fails()
    now = time.time()
    rec = store.get((uid, ip))
    if not rec or (rec["until"] and rec["until"] <= now):
        rec = store[(uid, ip)] = {"n": 0, "until": 0.0}
    rec["n"] += 1
    if rec["n"] >= limit:
        rec["n"], rec["until"] = 0, now + seconds
    if len(store) > 20000:
        for k in [k for k, v in store.items() if v["until"] < now and not v["n"]]:
            store.pop(k, None)
    return rec["until"] > now


def clear_fails(uid, ip=None):
    store = _fails()
    for k in [k for k in store if k[0] == uid and (ip is None or k[1] == ip)]:
        store.pop(k, None)


def client_ip():
    return request.remote_addr or "?"


def too_many(msg="Слишком много запросов, подожди минуту"):
    if request.path.startswith("/api/"):
        return jsonify(error=msg), 429
    abort(429)


# ---------------------------------------------------------------- роли
ROLE_NAMES = {"owner": "владелец", "admin": "администратор", "coach": "преподаватель", "user": "участник"}
ASSIGNABLE = ("admin", "coach", "user")      # «владельца» выдают только командой manage.py set-role НИК owner
ADMINS = ("owner", "admin")


def is_admin(u=None):
    u = u if u is not None else g.get("user")
    return bool(u) and u["role"] in ADMINS


def is_owner(u=None):
    u = u if u is not None else g.get("user")
    return bool(u) and u["role"] == "owner"


def is_coach(u=None):
    u = u if u is not None else g.get("user")
    return bool(u) and u["role"] in ("owner", "admin", "coach")


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not g.get("user"):
            if request.path.startswith("/api/"):
                return jsonify(error="Нужно войти в аккаунт"), 401
            return redirect(url_for("accounts.login", next=request.full_path.rstrip("?")))
        return f(*a, **k)
    return w


def role_required(*roles):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            u = g.get("user")
            if not u:
                return login_required(f)(*a, **k)
            if u["role"] not in roles and u["role"] not in ADMINS:
                if request.path.startswith("/api/"):
                    return jsonify(error="Недостаточно прав"), 403
                abort(403)
            return f(*a, **k)
        return w
    return deco


def safe_next(nxt, default="/"):
    # браузеры выкидывают из адреса табы и переводы строк: "/\t/evil.com" превратится в "//evil.com"
    if nxt and nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt and \
            not any(ord(ch) < 32 or ord(ch) == 127 for ch in nxt):
        return nxt
    return default


def new_csrf():
    session["csrf"] = secrets.token_hex(16)


def check_csrf():
    tok = request.form.get("csrf") or request.headers.get("X-CSRF-Token") or ""
    if not session.get("csrf") or not secrets.compare_digest(tok, session["csrf"]):
        abort(400)


CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; worker-src 'self'; media-src 'self' data: blob:; font-src 'self'; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


def security_headers(resp):
    h = resp.headers
    h.setdefault("Content-Security-Policy", CSP)
    h["X-Frame-Options"] = "DENY"
    h["X-Content-Type-Options"] = "nosniff"
    h["Referrer-Policy"] = "same-origin"
    h["Permissions-Policy"] = "geolocation=(), camera=(), microphone=(), payment=()"
    h["Cross-Origin-Opener-Policy"] = "same-origin"
    if request.is_secure:
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.path.startswith("/api/") or request.method == "POST" or g.get("user") is not None:
        h.setdefault("Cache-Control", "no-store")
    return resp
