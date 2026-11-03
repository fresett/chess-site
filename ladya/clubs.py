"""Клубы: создание (тренер или админ), приглашение по ссылке/коду, заявки, участники, рейтинг внутри клуба."""
import secrets

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import avatar_url
from . import ratings as rt
from .db import db, tx
from .notify import notify
from .security import is_admin, limited, login_required, role_required

bp = Blueprint("clubs", __name__)
POLICIES = {"request": "по заявке (тренер одобряет)", "open": "свободный (вступает кто угодно)",
            "invite": "только по приглашению"}


def get_club(cid):
    c = db().execute("SELECT * FROM clubs WHERE id=?", (cid,)).fetchone()
    if not c:
        abort(404)
    return c


def membership(cid, uid=None):
    uid = uid or (g.user["id"] if g.get("user") else None)
    if not uid:
        return None
    return db().execute("SELECT * FROM club_members WHERE club_id=? AND user_id=?", (cid, uid)).fetchone()


def can_manage(club, u=None):
    u = u if u is not None else g.get("user")
    if not u:
        return False
    if is_admin(u) or club["owner_id"] == u["id"]:
        return True
    m = membership(club["id"], u["id"])
    return bool(m and m["role"] == "coach")


def managed_clubs(u):
    if is_admin(u):
        return db().execute("SELECT * FROM clubs ORDER BY name").fetchall()
    return db().execute("SELECT DISTINCT c.* FROM clubs c LEFT JOIN club_members m ON m.club_id=c.id AND m.user_id=? "
                        "WHERE c.owner_id=? OR m.role='coach' ORDER BY c.name", (u["id"], u["id"])).fetchall()


def _clean(form):
    name = " ".join(form.get("name", "").split())[:60]
    desc = form.get("description", "").strip()[:2000]
    policy = form.get("join_policy", "request")
    return name, desc, policy if policy in POLICIES else "request"


@bp.route("/clubs")
def index():
    d = db()
    clubs = d.execute("""SELECT c.*, u.nick AS owner, (SELECT COUNT(*) FROM club_members WHERE club_id=c.id) AS n
                         FROM clubs c LEFT JOIN users u ON u.id=c.owner_id ORDER BY n DESC, c.name""").fetchall()
    mine = set()
    if g.user:
        mine = {r[0] for r in d.execute("SELECT club_id FROM club_members WHERE user_id=?", (g.user["id"],))}
    return render_template("clubs.html", clubs=clubs, mine=mine, policies=POLICIES)


@bp.route("/clubs/new", methods=["GET", "POST"])
@role_required("coach")
def new():
    if request.method == "POST":
        name, desc, policy = _clean(request.form)
        if len(name) < 3:
            flash("Название клуба — от 3 до 60 символов.")
        elif limited("club-new", g.user["id"], 10, 86400):
            flash("Слишком много новых клубов за сутки.")
        else:
            with tx() as d:
                cur = d.execute("INSERT INTO clubs(name,description,owner_id,invite_code,join_policy) VALUES(?,?,?,?,?)",
                                (name, desc, g.user["id"], secrets.token_urlsafe(6), policy))
                d.execute("INSERT INTO club_members(club_id,user_id,role) VALUES(?,?,'coach')",
                          (cur.lastrowid, g.user["id"]))
            flash("✔ Клуб создан. Пригласи учеников ссылкой со страницы клуба.", "ok")
            return redirect(url_for("clubs.view", cid=cur.lastrowid))
    return render_template("club_form.html", club=None, policies=POLICIES)


@bp.route("/clubs/<int:cid>")
def view(cid):
    club = get_club(cid)
    d = db()
    pool = request.args.get("pool", "club")
    members = d.execute("""SELECT u.id, u.nick, u.avatar, u.rating, m.role, m.joined,
                             (SELECT COUNT(*) FROM games WHERE user_id=u.id) AS games
                           FROM club_members m JOIN users u ON u.id=m.user_id WHERE m.club_id=?""", (cid,)).fetchall()
    rows = []
    for m in members:
        r = {**dict(m), "ava": avatar_url(m)}
        if pool in rt.GAME_POOLS or pool == "puzzle":
            gr = rt.get(d, m["id"], pool)
            r["score"], r["show"] = gr["rating"] if gr["games"] else -1, rt.display(gr) if gr["games"] else "—"
        else:
            r["score"], r["show"] = m["rating"], str(m["rating"])
        rows.append(r)
    rows.sort(key=lambda r: (-r["score"], r["nick"].casefold()))
    tours = d.execute("SELECT * FROM tournaments WHERE club_id=? ORDER BY status='finished', id DESC LIMIT 20",
                      (cid,)).fetchall()
    manage = can_manage(club)
    requests_ = d.execute("""SELECT r.*, u.nick, u.rating FROM club_requests r JOIN users u ON u.id=r.user_id
                             WHERE r.club_id=? ORDER BY r.created""", (cid,)).fetchall() if manage else []
    me_m = membership(cid)
    pending = bool(g.user and d.execute("SELECT 1 FROM club_requests WHERE club_id=? AND user_id=?",
                                        (cid, g.user["id"])).fetchone())
    owner = d.execute("SELECT nick FROM users WHERE id=?", (club["owner_id"],)).fetchone() if club["owner_id"] else None
    invite = request.host_url.rstrip("/") + url_for("clubs.join_by_code", code=club["invite_code"])
    return render_template("club.html", club=club, members=rows, tours=tours, manage=manage, requests=requests_,
                           me_m=me_m, pending=pending, owner=owner, invite=invite, pool=pool, pools=rt.POOLS,
                           policies=POLICIES)


@bp.route("/clubs/<int:cid>/edit", methods=["GET", "POST"])
@login_required
def edit(cid):
    club = get_club(cid)
    if not can_manage(club):
        abort(403)
    if request.method == "POST":
        name, desc, policy = _clean(request.form)
        if len(name) < 3:
            flash("Название клуба — от 3 до 60 символов.")
        else:
            db().execute("UPDATE clubs SET name=?, description=?, join_policy=? WHERE id=?", (name, desc, policy, cid))
            flash("✔ Сохранено.", "ok")
            return redirect(url_for("clubs.view", cid=cid))
    return render_template("club_form.html", club=club, policies=POLICIES)


@bp.route("/clubs/<int:cid>/join", methods=["POST"])
@login_required
def join(cid):
    club = get_club(cid)
    d = db()
    if membership(cid):
        return redirect(url_for("clubs.view", cid=cid))
    if club["join_policy"] == "open":
        d.execute("INSERT OR IGNORE INTO club_members(club_id,user_id) VALUES(?,?)", (cid, g.user["id"]))
        flash(f"✔ Ты вступил(а) в клуб «{club['name']}».", "ok")
    elif club["join_policy"] == "request":
        if limited("club-req", g.user["id"], 20, 3600):
            abort(429)
        with tx():
            d.execute("INSERT OR IGNORE INTO club_requests(club_id,user_id,message) VALUES(?,?,?)",
                      (cid, g.user["id"], request.form.get("message", "").strip()[:300]))
            for m in _managers(club):
                notify(m, "club", f"{g.user['nick']} хочет вступить в клуб «{club['name']}»",
                       url_for("clubs.view", cid=cid))
        flash("✔ Заявка отправлена. Тренер скоро её рассмотрит.", "ok")
    else:
        flash("В этот клуб вступают только по приглашению — попроси ссылку у тренера.")
    return redirect(url_for("clubs.view", cid=cid))


def _managers(club):
    ids = {club["owner_id"]} if club["owner_id"] else set()
    ids |= {r[0] for r in db().execute("SELECT user_id FROM club_members WHERE club_id=? AND role='coach'", (club["id"],))}
    return ids


@bp.route("/clubs/join/<code>", methods=["GET", "POST"])
def join_by_code(code):
    club = db().execute("SELECT * FROM clubs WHERE invite_code=?", (code,)).fetchone()
    if not club:
        flash("Ссылка-приглашение недействительна (возможно, тренер её обновил).")
        return redirect(url_for("clubs.index"))
    if request.method == "POST":
        if not g.user:
            return redirect(url_for("accounts.login", next=request.path))
        with tx() as d:
            d.execute("INSERT OR IGNORE INTO club_members(club_id,user_id) VALUES(?,?)", (club["id"], g.user["id"]))
            d.execute("DELETE FROM club_requests WHERE club_id=? AND user_id=?", (club["id"], g.user["id"]))
        flash(f"✔ Добро пожаловать в клуб «{club['name']}»!", "ok")
        return redirect(url_for("clubs.view", cid=club["id"]))
    return render_template("club_join.html", club=club, member=bool(membership(club["id"])))


@bp.route("/clubs/<int:cid>/requests/<int:uid>/<action>", methods=["POST"])
@login_required
def handle_request(cid, uid, action):
    club = get_club(cid)
    if not can_manage(club):
        abort(403)
    with tx() as d:
        if not d.execute("SELECT 1 FROM club_requests WHERE club_id=? AND user_id=?", (cid, uid)).fetchone():
            abort(404)
        d.execute("DELETE FROM club_requests WHERE club_id=? AND user_id=?", (cid, uid))
        if action == "approve":
            d.execute("INSERT OR IGNORE INTO club_members(club_id,user_id) VALUES(?,?)", (cid, uid))
            notify(uid, "club", f"Тебя приняли в клуб «{club['name']}»", url_for("clubs.view", cid=cid))
        else:
            notify(uid, "club", f"Заявка в клуб «{club['name']}» отклонена")
    return redirect(url_for("clubs.view", cid=cid))


@bp.route("/clubs/<int:cid>/members/<int:uid>/<action>", methods=["POST"])
@login_required
def member_action(cid, uid, action):
    club = get_club(cid)
    if not can_manage(club):
        abort(403)
    d = db()
    if not membership(cid, uid):
        abort(404)
    if uid == club["owner_id"]:
        flash("Владельца клуба нельзя удалить или понизить.")
    elif action == "remove":
        d.execute("DELETE FROM club_members WHERE club_id=? AND user_id=?", (cid, uid))
        flash("Участник удалён из клуба.", "ok")
    elif action in ("coach", "member"):
        d.execute("UPDATE club_members SET role=? WHERE club_id=? AND user_id=?", (action, cid, uid))
        flash("✔ Роль в клубе изменена.", "ok")
    return redirect(url_for("clubs.view", cid=cid))


@bp.route("/clubs/<int:cid>/leave", methods=["POST"])
@login_required
def leave(cid):
    club = get_club(cid)
    if club["owner_id"] == g.user["id"]:
        flash("Владелец не может выйти из своего клуба — можно только удалить клуб.")
    else:
        db().execute("DELETE FROM club_members WHERE club_id=? AND user_id=?", (cid, g.user["id"]))
        flash("Ты вышел(ла) из клуба.", "ok")
    return redirect(url_for("clubs.view", cid=cid))


@bp.route("/clubs/<int:cid>/new-code", methods=["POST"])
@login_required
def new_code(cid):
    club = get_club(cid)
    if not can_manage(club):
        abort(403)
    db().execute("UPDATE clubs SET invite_code=? WHERE id=?", (secrets.token_urlsafe(6), cid))
    flash("✔ Новая ссылка-приглашение создана, старая больше не работает.", "ok")
    return redirect(url_for("clubs.view", cid=cid))


@bp.route("/clubs/<int:cid>/delete", methods=["POST"])
@login_required
def delete(cid):
    club = get_club(cid)
    if not (is_admin() or club["owner_id"] == g.user["id"]):
        abort(403)
    if request.form.get("confirm", "").strip().casefold() != club["name"].casefold():
        flash("Для удаления впиши точное название клуба.")
        return redirect(url_for("clubs.view", cid=cid))
    db().execute("DELETE FROM clubs WHERE id=?", (cid,))
    flash("Клуб удалён. Его турниры сохранились как открытые.", "ok")
    return redirect(url_for("clubs.index"))
