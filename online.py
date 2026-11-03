"""Онлайн-игра: поиск соперника, партии между двумя людьми, «кто сейчас на сайте».

Как это работает:
  * Игроки (участники клуба или гости) нажимают «Найти соперника» — попадают в очередь.
    Как только в очереди двое с одинаковым контролем времени, создаётся партия.
  * Браузеры раз в секунду спрашивают сервер о состоянии партии (поэтому работает на любом хостинге,
    в том числе на бесплатном PythonAnywhere, где нет WebSocket).
  * Ходы проверяет сервер (библиотека python-chess), время тоже считает сервер — сжульничать нельзя.
  * Рейтинг меняется, только если оба игрока зарегистрированы (гости играют без рейтинга).

Очередь и «онлайн» хранятся в памяти процесса (на PythonAnywhere он один), партии — в SQLite.
"""
import secrets
import threading
import time

import chess
from flask import Blueprint, g, jsonify, request, session

bp = Blueprint("online", __name__)
LOCK = threading.Lock()
TCS = {180, 300, 600}            # контроль времени: 3, 5, 10 минут
ONLINE_WINDOW = 60               # «на сайте» — кто заходил за последние 60 секунд
SEEK_TTL = 12                    # заявка на поиск живёт, пока браузер её опрашивает
ABORT_AFTER = 30                 # белые не сделали первый ход за 30 с — партия отменяется
presence = {}                    # sid -> время последнего запроса
seeks = {}                       # pid -> {name, rating, uid, tc, ts}

SCHEMA = """
CREATE TABLE IF NOT EXISTS online_games(
  id TEXT PRIMARY KEY, created REAL NOT NULL, tc INTEGER NOT NULL,
  w_pid TEXT NOT NULL, b_pid TEXT NOT NULL, w_name TEXT NOT NULL, b_name TEXT NOT NULL,
  w_uid INTEGER, b_uid INTEGER, w_rating INTEGER, b_rating INTEGER, rated INTEGER NOT NULL DEFAULT 0,
  moves TEXT NOT NULL DEFAULT '', w_ms INTEGER NOT NULL, b_ms INTEGER NOT NULL, last_ts REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'active', result TEXT, reason TEXT, draw_offer TEXT,
  w_delta INTEGER, b_delta INTEGER);
CREATE INDEX IF NOT EXISTS og_status ON online_games(status);
"""
REASONS = {chess.Termination.CHECKMATE: "checkmate", chess.Termination.STALEMATE: "stalemate",
           chess.Termination.INSUFFICIENT_MATERIAL: "material", chess.Termination.SEVENTYFIVE_MOVES: "fifty",
           chess.Termination.FIFTY_MOVES: "fifty", chess.Termination.FIVEFOLD_REPETITION: "repetition",
           chess.Termination.THREEFOLD_REPETITION: "repetition"}

_ctx = {}   # db, rating_change, limited, hits — передаются из app.py


def init(app, db, rating_change, limited, hits):
    _ctx.update(db=db, rating_change=rating_change, limited=limited, hits=hits)
    app.register_blueprint(bp)


def db():
    return _ctx["db"]()


def touch(sid):
    """Отметить, что посетитель сейчас на сайте."""
    now = time.time()
    presence[sid] = now
    if len(presence) > 5000:
        for k in [k for k, t in presence.items() if t < now - ONLINE_WINDOW]:
            presence.pop(k, None)


def me():
    """Кто играет: участник клуба или гость. None — если ни то, ни другое."""
    u = g.get("user")
    if u:
        return {"pid": f"u{u['id']}", "name": u["nick"], "uid": u["id"], "rating": u["rating"], "guest": False}
    gs = session.get("guest")
    if gs:
        return {"pid": f"g{gs['id']}", "name": gs["name"], "uid": None, "rating": None, "guest": True}
    return None


def need_me():
    p = me()
    if not p:
        return None, (jsonify(error="Войди или зайди как гость"), 401)
    return p, None


# ---------------- партии ----------------
def board_of(row):
    b = chess.Board()
    for u in row["moves"].split():
        b.push_uci(u)
    return b


def clocks(row, now=None):
    """Оставшееся время (мс) у обеих сторон на текущий момент."""
    now = now or time.time()
    w, b = row["w_ms"], row["b_ms"]
    n = len(row["moves"].split())
    if row["status"] == "active" and n > 0:          # до первого хода белых часы стоят
        spent = int((now - row["last_ts"]) * 1000)
        if n % 2 == 0:
            w -= spent
        else:
            b -= spent
    return max(0, w), max(0, b)


def finish(row, result, reason):
    """Завершить партию и (если оба зарегистрированы) пересчитать рейтинг."""
    d = db()
    wd = bd = None
    if row["rated"] and result in ("1-0", "0-1", "1/2-1/2"):
        wr = {"1-0": "win", "0-1": "loss"}.get(result, "draw")
        br = {"win": "loss", "loss": "win", "draw": "draw"}[wr]
        cur_w = d.execute("SELECT rating FROM users WHERE id=?", (row["w_uid"],)).fetchone()
        cur_b = d.execute("SELECT rating FROM users WHERE id=?", (row["b_uid"],)).fetchone()
        if cur_w and cur_b:
            nw, wd = _ctx["rating_change"](cur_w[0], cur_b[0], wr)
            nb, bd = _ctx["rating_change"](cur_b[0], cur_w[0], br)
            d.execute("UPDATE users SET rating=? WHERE id=?", (nw, row["w_uid"]))
            d.execute("UPDATE users SET rating=? WHERE id=?", (nb, row["b_uid"]))
            for uid, res, delta, opp_r, opp_n in ((row["w_uid"], wr, wd, cur_b[0], row["b_name"]),
                                                  (row["b_uid"], br, bd, cur_w[0], row["w_name"])):
                d.execute("INSERT INTO games(user_id,bot,result,moves,mode,delta,opp) VALUES(?,?,?,?,?,?,?)",
                          (uid, opp_r, res, row["moves"], "online", delta, opp_n))
    d.execute("UPDATE online_games SET status='over', result=?, reason=?, draw_offer=NULL, w_delta=?, b_delta=?, "
              "w_ms=?, b_ms=?, last_ts=? WHERE id=? AND status='active'",
              (result, reason, wd, bd, *clocks(row), time.time(), row["id"]))
    d.commit()


def check_time(row):
    """Флажок упал или белые так и не сходили — завершаем партию. Возвращает свежую строку."""
    if row["status"] != "active":
        return row
    now = time.time()
    n = len(row["moves"].split())
    if n == 0 and now - row["created"] > ABORT_AFTER:
        finish(row, "aborted", "aborted")
    else:
        w, b = clocks(row, now)
        side_white = n % 2 == 0
        if (w if side_white else b) <= 0:
            board = board_of(row)
            # у соперника не хватает материала для мата — ничья, иначе он выиграл по времени
            winner_white = not side_white
            if board.has_insufficient_material(chess.WHITE if winner_white else chess.BLACK):
                finish(row, "1/2-1/2", "timeout-material")
            else:
                finish(row, "1-0" if winner_white else "0-1", "timeout")
    return db().execute("SELECT * FROM online_games WHERE id=?", (row["id"],)).fetchone()


def sweep():
    """Завершить брошенные партии (вызывается при запросах статистики)."""
    for row in db().execute("SELECT * FROM online_games WHERE status='active'").fetchall():
        check_time(row)


def state(row, p):
    w, b = clocks(row)
    you = "w" if p and p["pid"] == row["w_pid"] else "b" if p and p["pid"] == row["b_pid"] else None
    return {
        "id": row["id"], "tc": row["tc"], "you": you, "rated": bool(row["rated"]),
        "white": {"name": row["w_name"], "rating": row["w_rating"], "guest": row["w_uid"] is None},
        "black": {"name": row["b_name"], "rating": row["b_rating"], "guest": row["b_uid"] is None},
        "moves": row["moves"].split(), "clock": {"w": w, "b": b},
        "status": row["status"], "result": row["result"], "reason": row["reason"],
        "draw_offer": row["draw_offer"], "delta": {"w": row["w_delta"], "b": row["b_delta"]},
    }


def my_active_game(p):
    return db().execute("SELECT * FROM online_games WHERE status='active' AND (w_pid=? OR b_pid=?) "
                        "ORDER BY created DESC LIMIT 1", (p["pid"], p["pid"])).fetchone()


def load_game(gid, p):
    row = db().execute("SELECT * FROM online_games WHERE id=?", (gid,)).fetchone()
    if not row:
        return None, (jsonify(error="Партия не найдена"), 404)
    if p["pid"] not in (row["w_pid"], row["b_pid"]):
        return None, (jsonify(error="Это не твоя партия"), 403)
    return row, None


# ---------------- API ----------------
@bp.route("/api/ping")
def ping():
    now = time.time()
    return jsonify(online=sum(1 for t in presence.values() if t > now - ONLINE_WINDOW))


@bp.route("/api/online/status")
def status():
    p = me()
    with LOCK:
        sweep()
        now = time.time()
        for pid in [k for k, s in seeks.items() if s["ts"] < now - SEEK_TTL]:
            seeks.pop(pid, None)
        playing = db().execute("SELECT COUNT(*) FROM online_games WHERE status='active'").fetchone()[0] * 2
        active = my_active_game(p) if p else None
    return jsonify(online=sum(1 for t in presence.values() if t > now - ONLINE_WINDOW),
                   searching=len(seeks), playing=playing,
                   me=p and {"name": p["name"], "guest": p["guest"], "rating": p["rating"]},
                   game=active["id"] if active else None)


@bp.route("/api/online/seek", methods=["POST"])
def seek():
    p, err = need_me()
    if err:
        return err
    if _ctx["limited"](_ctx["hits"], ("seek", p["pid"]), 120, 60):
        return jsonify(error="Слишком часто"), 429
    tc = (request.get_json(silent=True) or {}).get("tc", 300)
    if tc not in TCS:
        return jsonify(error="Неверный контроль времени"), 400
    with LOCK:
        active = my_active_game(p)
        if active:
            return jsonify(game=active["id"])
        now = time.time()
        # ищем соперника с тем же контролем времени (не себя самого из второй вкладки)
        opp_pid = next((pid for pid, s in sorted(seeks.items(), key=lambda kv: kv[1]["ts"])
                        if pid != p["pid"] and s["tc"] == tc and s["ts"] > now - SEEK_TTL), None)
        if not opp_pid:
            seeks[p["pid"]] = {"name": p["name"], "rating": p["rating"], "uid": p["uid"], "tc": tc,
                               "ts": now, "since": seeks.get(p["pid"], {}).get("since", now)}
            return jsonify(searching=True, waited=int(now - seeks[p["pid"]]["since"]))
        o = seeks.pop(opp_pid)
        seeks.pop(p["pid"], None)
        a = {"pid": opp_pid, **o}
        b_ = {"pid": p["pid"], "name": p["name"], "rating": p["rating"], "uid": p["uid"]}
        w, b = (a, b_) if secrets.randbelow(2) else (b_, a)   # цвета — случайно
        gid = secrets.token_urlsafe(8)
        rated = 1 if (w["uid"] and b["uid"]) else 0
        db().execute("INSERT INTO online_games(id,created,tc,w_pid,b_pid,w_name,b_name,w_uid,b_uid,w_rating,b_rating,"
                     "rated,w_ms,b_ms,last_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (gid, now, tc, w["pid"], b["pid"], w["name"], b["name"], w["uid"], b["uid"],
                      w["rating"], b["rating"], rated, tc * 1000, tc * 1000, now))
        db().commit()
    return jsonify(game=gid)


@bp.route("/api/online/seek/cancel", methods=["POST"])
def seek_cancel():
    p = me()
    if p:
        with LOCK:
            seeks.pop(p["pid"], None)
    return jsonify(ok=True)


@bp.route("/api/online/game/<gid>")
def game_state(gid):
    p, err = need_me()
    if err:
        return err
    with LOCK:
        row, err = load_game(gid, p)
        if err:
            return err
        row = check_time(row)
    return jsonify(state(row, p))


@bp.route("/api/online/game/<gid>/move", methods=["POST"])
def game_move(gid):
    p, err = need_me()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    uci, ply = str(data.get("uci", ""))[:5], data.get("ply")
    with LOCK:
        row, err = load_game(gid, p)
        if err:
            return err
        row = check_time(row)
        if row["status"] != "active":
            return jsonify(error="Партия уже закончена", state=state(row, p)), 409
        moves = row["moves"].split()
        color = "w" if len(moves) % 2 == 0 else "b"
        if p["pid"] != row[f"{color}_pid"]:
            return jsonify(error="Сейчас не твой ход", state=state(row, p)), 409
        if ply != len(moves):
            return jsonify(error="Позиция устарела", state=state(row, p)), 409
        board = board_of(row)
        try:
            mv = chess.Move.from_uci(uci)
        except ValueError:
            mv = None
        if not mv or mv not in board.legal_moves:
            return jsonify(error="Недопустимый ход", state=state(row, p)), 400
        now = time.time()
        spent = int((now - row["last_ts"]) * 1000)
        left = row[f"{color}_ms"] - spent
        board.push(mv)
        moves.append(mv.uci())
        # первый ход белых часы не расходует (успеть сориентироваться)
        if len(moves) == 1:
            left = row["w_ms"]
        db().execute(f"UPDATE online_games SET moves=?, {color}_ms=?, last_ts=?, "
                     "draw_offer=CASE WHEN draw_offer IS NOT NULL AND draw_offer<>? THEN NULL ELSE draw_offer END WHERE id=?",
                     (" ".join(moves), max(0, left), now, color, gid))
        db().commit()
        row = db().execute("SELECT * FROM online_games WHERE id=?", (gid,)).fetchone()
        out = board.outcome(claim_draw=True)
        if out:
            finish(row, out.result(), REASONS.get(out.termination, "draw"))
            row = db().execute("SELECT * FROM online_games WHERE id=?", (gid,)).fetchone()
    return jsonify(state(row, p))


@bp.route("/api/online/game/<gid>/resign", methods=["POST"])
def game_resign(gid):
    p, err = need_me()
    if err:
        return err
    with LOCK:
        row, err = load_game(gid, p)
        if err:
            return err
        row = check_time(row)
        if row["status"] == "active":
            if not row["moves"]:
                finish(row, "aborted", "aborted")          # до первого хода — просто отмена
            else:
                finish(row, "0-1" if p["pid"] == row["w_pid"] else "1-0", "resign")
        row = db().execute("SELECT * FROM online_games WHERE id=?", (gid,)).fetchone()
    return jsonify(state(row, p))


@bp.route("/api/online/game/<gid>/draw", methods=["POST"])
def game_draw(gid):
    p, err = need_me()
    if err:
        return err
    action = (request.get_json(silent=True) or {}).get("action")
    with LOCK:
        row, err = load_game(gid, p)
        if err:
            return err
        row = check_time(row)
        if row["status"] == "active":
            mine = "w" if p["pid"] == row["w_pid"] else "b"
            offer = row["draw_offer"]
            if action == "offer" and not offer:
                db().execute("UPDATE online_games SET draw_offer=? WHERE id=?", (mine, gid)); db().commit()
            elif action == "accept" and offer and offer != mine:
                finish(row, "1/2-1/2", "agreement")
            elif action == "decline" and offer and offer != mine:
                db().execute("UPDATE online_games SET draw_offer=NULL WHERE id=?", (gid,)); db().commit()
        row = db().execute("SELECT * FROM online_games WHERE id=?", (gid,)).fetchone()
    return jsonify(state(row, p))
