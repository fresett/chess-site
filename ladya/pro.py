"""Подписка Rookline Pro.

Оплата идёт вне сайта (например, на FunPay): владелец генерирует коды командой
`python manage.py gen-codes 10 --days=30`, продаёт их, а покупатель вводит код на странице /pro.
Код одноразовый; дни подписки складываются (купил второй код — срок продлился).
Владелец, админы и тренеры получают Pro бесплатно.
"""
import secrets
import time

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, session, url_for

from .db import db, tx
from .security import limited, login_required

bp = Blueprint("pro", __name__)

FREE_PUZZLES = 20              # задачи 1…20 — бесплатно, дальше — только с Pro
STAFF = ("owner", "admin", "coach")
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # без похожих 0/O и 1/I

# Тарифы: цена в рублях и лот на FunPay, где покупают код
PLANS = [
    {"days": 30, "name": "1 месяц", "price": 129, "url": "https://funpay.com/lots/offer?id=81563087"},
    {"days": 90, "name": "3 месяца", "price": 249, "url": "https://funpay.com/lots/offer?id=81563385", "tag": "Выгодно"},
    {"days": 365, "name": "1 год", "price": 579, "url": "https://funpay.com/lots/offer?id=81563325", "tag": "Лучшая цена"},
]
for _p in PLANS:
    _p["per_month"] = round(_p["price"] / (_p["days"] / 30))

# Неоновый ник (только с Pro): ключ → название
NEON = {"pink": "Розовый", "cyan": "Голубой", "lime": "Кислотный", "violet": "Фиолетовый", "gold": "Золотой"}
# Рамка аватарки (только с Pro)
FRAMES = {"gold": "Золотая", "rainbow": "Радужная", "fire": "Огненная"}
COACH_FREE_PER_DAY = 3


def is_pro(u):
    if not u:
        return False
    if u["role"] in STAFF:
        return True
    return (u["pro_until"] or 0) > time.time()


def _field(r, k):
    try:
        return r[k]
    except (KeyError, IndexError, TypeError):
        return None


def _active(r):
    return bool(r) and (_field(r, "role") in STAFF or (_field(r, "pro_until") or 0) > time.time())


def neon_of(r):
    """Цвет неонового ника или '' — только если у человека сейчас есть Pro.
    r — строка/словарь с полями neon, role, pro_until (если полей нет — без неона)."""
    c = _field(r, "neon")
    return c if c in NEON and _active(r) else ""


def frame_class(r):
    c = _field(r, "frame")
    return f"fr fr-{c}" if c in FRAMES and _active(r) else ""


def paid_pro(r):
    """Значок PRO — только у тех, кто купил подписку (у тренеров и админов Pro бесплатный — без значка)."""
    return bool(r) and (_field(r, "pro_until") or 0) > time.time()


def neon_class(r):
    c = neon_of(r)
    return f"neon neon-{c}" if c else ""


def neon_for_uid(d, uid):
    if not uid:
        return ""
    return neon_of(d.execute("SELECT neon, role, pro_until FROM users WHERE id=?", (uid,)).fetchone())


def pro_for_uid(d, uid):
    if not uid:
        return False
    return paid_pro(d.execute("SELECT pro_until FROM users WHERE id=?", (uid,)).fetchone())


def looks_for(d, *uids):
    """Неон и значок PRO для игроков онлайн-партии одним запросом: {uid: {"neon": .., "pro": ..}}."""
    ids = [u for u in uids if u]
    if not ids:
        return {}
    rows = d.execute(f"SELECT id, neon, role, pro_until FROM users WHERE id IN ({','.join('?' * len(ids))})",
                     ids).fetchall()
    return {r["id"]: {"neon": neon_of(r), "pro": paid_pro(r)} for r in rows}


def pro_days_left(u):
    if not u or u["role"] in STAFF:
        return None
    left = (u["pro_until"] or 0) - time.time()
    return max(0, int(left // 86400) + (1 if left % 86400 else 0)) if left > 0 else 0


def puzzle_locked(u, pid):
    return pid > FREE_PUZZLES and not is_pro(u)


def new_code():
    raw = "".join(secrets.choice(ALPHABET) for _ in range(12))
    return f"RL-{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def norm(code):
    s = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    if s.startswith("RL"):
        s = s[2:]
    return f"RL-{s[:4]}-{s[4:8]}-{s[8:]}" if len(s) == 12 else ""


def gen_codes(c, n, days):
    out = []
    for _ in range(n):
        code = new_code()
        c.execute("INSERT INTO pro_codes(code, days, created) VALUES(?,?,?)", (code, days, time.time()))
        out.append(code)
    return out


def redeem(d, uid, code):
    """Активировать код. Возвращает (новый срок, None) или (None, текст ошибки). Вызывать внутри tx()."""
    code = norm(code)
    row = d.execute("SELECT * FROM pro_codes WHERE code=?", (code,)).fetchone() if code else None
    if not row:
        return None, "Такого кода нет. Проверь, что ввёл его без ошибок."
    if row["used_by"]:
        return None, "Этот код уже активирован."
    cur = d.execute("SELECT pro_until FROM users WHERE id=?", (uid,)).fetchone()["pro_until"] or 0
    until = max(cur, time.time()) + row["days"] * 86400
    d.execute("UPDATE pro_codes SET used_by=?, used_at=? WHERE code=?", (uid, time.time(), code))
    d.execute("UPDATE users SET pro_until=? WHERE id=?", (until, uid))
    return until, None


@bp.route("/pro")
def page():
    return render_template("pro.html", pro=is_pro(g.user), days_left=pro_days_left(g.user),
                           free=FREE_PUZZLES, plans=PLANS, neons=NEON, my_neon=g.user["neon"] if g.user else "",
                           frames=FRAMES, my_frame=g.user["frame"] if g.user else "", coach_free=COACH_FREE_PER_DAY)


@bp.route("/pro/frame", methods=["POST"])
@login_required
def set_frame():
    c = request.form.get("frame", "")
    if c and c not in FRAMES:
        c = ""
    if c and not is_pro(g.user):
        flash("Рамка доступна только с Rookline Pro.", "err")
        return redirect(url_for("pro.page"))
    db().execute("UPDATE users SET frame=? WHERE id=?", (c, g.user["id"]))
    flash("Рамка снята." if not c else f"Готово! {FRAMES[c]} рамка надета 🖼", "ok")
    return redirect(url_for("pro.page") + "#frame")


@bp.route("/api/coach/start", methods=["POST"])
def coach_start():
    """Партия с тренером Альбертом: бесплатно 3 в день, с Pro — без ограничений."""
    if is_pro(g.user):
        return jsonify(ok=True, left=None)
    who = ("u:%s" % g.user["id"]) if g.user else ("s:%s" % session.get("sid", ""))
    day = time.strftime("%Y-%m-%d")
    with tx() as d:
        row = d.execute("SELECT n FROM coach_uses WHERE who=? AND day=?", (who, day)).fetchone()
        n = row["n"] if row else 0
        if n >= COACH_FREE_PER_DAY:
            return jsonify(ok=False, left=0, error=f"Бесплатно — {COACH_FREE_PER_DAY} партии с Альбертом в день. "
                                                   "С Rookline Pro — без ограничений.")
        d.execute("INSERT INTO coach_uses(who, day, n) VALUES(?,?,1) ON CONFLICT(who, day) DO UPDATE SET n=n+1",
                  (who, day))
    return jsonify(ok=True, left=COACH_FREE_PER_DAY - n - 1)


@bp.route("/pro/neon", methods=["POST"])
@login_required
def set_neon():
    c = request.form.get("neon", "")
    if c and c not in NEON:
        c = ""
    if c and not is_pro(g.user):
        flash("Неоновый ник доступен только с Rookline Pro.", "err")
        return redirect(url_for("pro.page"))
    db().execute("UPDATE users SET neon=? WHERE id=?", (c, g.user["id"]))
    flash("Неон выключен." if not c else f"Готово! Теперь твой ник светится: {NEON[c].lower()} неон ✨", "ok")
    return redirect(url_for("pro.page") + "#neon")


@bp.route("/pro/redeem", methods=["POST"])
@login_required
def redeem_form():
    if limited("redeem", g.user["id"], 10, 3600):        # от подбора кодов
        flash("Слишком много попыток. Попробуй через час.", "err")
        return redirect(url_for("pro.page"))
    with tx() as d:
        until, err = redeem(d, g.user["id"], request.form.get("code", ""))
    if err:
        flash(err, "err")
    else:
        flash("Готово! Rookline Pro активирован до " + time.strftime("%d.%m.%Y", time.localtime(until)) + " 🎉", "ok")
    return redirect(url_for("pro.page"))
