"""Админка /admin: пользователи (поиск, роль, блокировка, сброс пароля), клубы, турниры, жалобы, журнал входов."""
import secrets
import time

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .accounts import _set_password, delete_user
from .db import db, tx
from .security import ASSIGNABLE, ROLE_NAMES, is_owner, role_required

bp = Blueprint("admin", __name__, url_prefix="/admin")
PER = 50


def log(action, details=""):
    db().execute("INSERT INTO admin_log(ts,admin_id,action,details) VALUES(?,?,?,?)",
                 (time.time(), g.user["id"], action, details[:500]))


def temp_password():
    """Временный пароль, который легко продиктовать: 3 слога + 2 цифры + слог (≈ 40 бит)."""
    cons, vow = "bcdfghjkmnprstvz", "aeiou"
    syl = lambda: secrets.choice(cons) + secrets.choice(vow) + secrets.choice(cons)  # noqa: E731
    return f"{syl()}{syl()}-{secrets.randbelow(90) + 10}-{syl()}"


@bp.route("/")
@role_required("admin")
def index():
    d = db()
    counts = {
        "users": d.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "blocked": d.execute("SELECT COUNT(*) FROM users WHERE blocked=1").fetchone()[0],
        "clubs": d.execute("SELECT COUNT(*) FROM clubs").fetchone()[0],
        "tournaments": d.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0],
        "reports": d.execute("SELECT COUNT(*) FROM reports WHERE status='open'").fetchone()[0],
        "games": d.execute("SELECT COUNT(*) FROM online_games WHERE status='active'").fetchone()[0],
        "fails24": d.execute("SELECT COUNT(*) FROM login_log WHERE ok=0 AND ts>?", (time.time() - 86400,)).fetchone()[0],
    }
    return render_template("admin/index.html", counts=counts, tab="index")


@bp.route("/users")
@role_required("admin")
def users():
    q = request.args.get("q", "").strip()
    role = request.args.get("role", "")
    page = max(1, request.args.get("page", 1, type=int))
    where, args = ["1=1"], []
    if q:
        where.append("fold(nick) LIKE ? ESCAPE '\\'")
        args.append("%" + q.casefold().replace("\\", "").replace("%", "\\%").replace("_", "\\_") + "%")
    if role in ROLE_NAMES:
        where.append("role=?")
        args.append(role)
    if request.args.get("blocked"):
        where.append("blocked=1")
    rows = db().execute(f"SELECT id,nick,role,blocked,must_change_pw,rating,created,last_login,locked_until FROM users "
                        f"WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ? OFFSET ?",
                        (*args, PER + 1, (page - 1) * PER)).fetchall()
    return render_template("admin/users.html", rows=rows[:PER], more=len(rows) > PER, page=page, q=q, role=role,
                           tab="users", now=time.time())


def _target(uid):
    u = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        abort(404)
    return u


def _last_admin(u):
    return u["role"] in ("owner", "admin") and         db().execute("SELECT COUNT(*) FROM users WHERE role IN ('owner','admin')").fetchone()[0] <= 1


@bp.route("/users/<int:uid>/<action>", methods=["POST"])
@role_required("admin")
def user_action(uid, action):
    u = _target(uid)
    d = db()
    back = request.form.get("back") or url_for("admin.users")
    if not back.startswith("/admin"):
        back = url_for("admin.users")
    # владельца сайта не может трогать никто, кроме самого владельца
    if u["role"] == "owner" and not is_owner():
        flash("Это владелец сайта — его аккаунт может менять только он сам.")
        return redirect(back)
    if action == "role":
        role = request.form.get("role")
        if not is_owner():
            flash("Выдавать роли может только владелец сайта.")
            return redirect(back)
        if role not in ASSIGNABLE or u["role"] == "owner":
            abort(400)
        if _last_admin(u) and role != "admin":
            flash("Нельзя снять последнего администратора.")
        else:
            d.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
            log("role", f"{u['nick']}: {u['role']} → {role}")
            flash(f"✔ {u['nick']}: роль «{ROLE_NAMES[role]}».", "ok")
    elif action == "block":
        if u["id"] == g.user["id"] or _last_admin(u):
            flash("Нельзя заблокировать себя или последнего администратора.")
        else:
            d.execute("UPDATE users SET blocked=1, sess_ver=sess_ver+1 WHERE id=?", (uid,))
            d.execute("DELETE FROM seeks WHERE uid=?", (uid,))
            log("block", u["nick"])
            flash(f"{u['nick']} заблокирован(а) и выкинут(а) со всех устройств.", "ok")
    elif action == "unblock":
        d.execute("UPDATE users SET blocked=0, failed_logins=0, locked_until=0 WHERE id=?", (uid,))
        log("unblock", u["nick"])
        flash(f"{u['nick']} разблокирован(а).", "ok")
    elif action == "unlock":
        d.execute("UPDATE users SET failed_logins=0, locked_until=0 WHERE id=?", (uid,))
        from .security import clear_fails
        clear_fails(uid)
        flash("Временная блокировка входа снята.", "ok")
    elif action == "reset":
        pw = temp_password()
        _set_password(d, uid, pw, must_change=1)
        log("reset-password", u["nick"])
        flash(f"Временный пароль для {u['nick']}: {pw} — продиктуй его. При входе система попросит придумать свой.",
              "ok")
    elif action == "delete":
        if u["id"] == g.user["id"] or _last_admin(u):
            flash("Нельзя удалить себя или последнего администратора.")
        elif request.form.get("confirm", "").casefold() != u["nick"].casefold():
            flash("Для удаления впиши ник пользователя.")
        else:
            with tx():
                delete_user(d, uid)
                log("delete-user", u["nick"])
            flash(f"Аккаунт {u['nick']} удалён.", "ok")
            return redirect(url_for("admin.users"))
    else:
        abort(404)
    return redirect(back)


@bp.route("/clubs")
@role_required("admin")
def clubs():
    rows = db().execute("""SELECT c.*, u.nick AS owner, (SELECT COUNT(*) FROM club_members WHERE club_id=c.id) AS n
                           FROM clubs c LEFT JOIN users u ON u.id=c.owner_id ORDER BY c.id DESC""").fetchall()
    return render_template("admin/clubs.html", rows=rows, tab="clubs")


@bp.route("/tournaments")
@role_required("admin")
def tournaments():
    rows = db().execute("""SELECT t.*, u.nick AS owner, c.name AS club,
                             (SELECT COUNT(*) FROM t_players WHERE tournament_id=t.id) AS n
                           FROM tournaments t LEFT JOIN users u ON u.id=t.owner_id LEFT JOIN clubs c ON c.id=t.club_id
                           ORDER BY t.id DESC LIMIT 300""").fetchall()
    return render_template("admin/tournaments.html", rows=rows, tab="tournaments")


@bp.route("/reports")
@role_required("admin")
def reports():
    st = request.args.get("status", "open")
    rows = db().execute("""SELECT r.*, f.nick AS from_nick FROM reports r LEFT JOIN users f ON f.id=r.from_uid
                           WHERE r.status=? ORDER BY r.ts DESC LIMIT 200""", (st if st in ("open", "closed") else "open",)
                        ).fetchall()
    return render_template("admin/reports.html", rows=rows, st=st, tab="reports")


@bp.route("/reports/<int:rid>/close", methods=["POST"])
@role_required("admin")
def close_report(rid):
    db().execute("UPDATE reports SET status='closed', resolved_by=?, resolved_ts=? WHERE id=?",
                 (g.user["id"], time.time(), rid))
    log("close-report", str(rid))
    return redirect(url_for("admin.reports"))


@bp.route("/logins")
@role_required("admin")
def logins():
    only_fail = request.args.get("fail") == "1"
    q = request.args.get("q", "").strip()
    where, args = ["1=1"], []
    if only_fail:
        where.append("ok=0")
    if q:
        where.append("(fold(nick)=? OR ip=?)")
        args += [q.casefold(), q]
    rows = db().execute(f"SELECT * FROM login_log WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT 300", args).fetchall()
    top = db().execute("SELECT ip, COUNT(*) n FROM login_log WHERE ok=0 AND ts>? GROUP BY ip ORDER BY n DESC LIMIT 10",
                       (time.time() - 86400,)).fetchall()
    actions = db().execute("SELECT a.*, u.nick FROM admin_log a LEFT JOIN users u ON u.id=a.admin_id "
                           "ORDER BY a.ts DESC LIMIT 100").fetchall()
    return render_template("admin/logins.html", rows=rows, top=top, actions=actions, only_fail=only_fail, q=q,
                           tab="logins")
