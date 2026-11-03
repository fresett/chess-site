"""Уведомления: вызов на партию, начало тура, заявка в клуб и т. п. Браузер опрашивает их раз в 20 секунд."""
import time

from flask import Blueprint, g, jsonify, render_template, request

from .db import db
from .security import login_required

bp = Blueprint("notify", __name__)


def notify(uid, kind, text, url=None, d=None):
    if not uid:
        return
    d = d or db()
    d.execute("INSERT INTO notifications(user_id,ts,kind,text,url) VALUES(?,?,?,?,?)",
              (uid, time.time(), kind, text[:300], url))
    # храним не больше 200 последних уведомлений на человека
    d.execute("DELETE FROM notifications WHERE user_id=? AND id NOT IN "
              "(SELECT id FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 200)", (uid, uid))


@bp.route("/api/notifications")
def api_list():
    if not g.user:
        return jsonify(unread=0, items=[])
    d = db()
    since = request.args.get("since", type=int) or 0
    rows = d.execute("SELECT id,ts,kind,text,url,read FROM notifications WHERE user_id=? AND id>? "
                     "ORDER BY id DESC LIMIT 20", (g.user["id"], since)).fetchall()
    unread = d.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read=0", (g.user["id"],)).fetchone()[0]
    return jsonify(unread=unread, items=[dict(r) for r in rows])


@bp.route("/api/notifications/read", methods=["POST"])
@login_required
def api_read():
    db().execute("UPDATE notifications SET read=1 WHERE user_id=? AND read=0", (g.user["id"],))
    return jsonify(ok=True)


@bp.route("/notifications")
@login_required
def page():
    d = db()
    rows = d.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100", (g.user["id"],)).fetchall()
    d.execute("UPDATE notifications SET read=1 WHERE user_id=? AND read=0", (g.user["id"],))
    return render_template("notifications.html", items=rows)
