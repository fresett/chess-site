"""Онлайн-игра: поиск соперника, вызовы, партии между двумя людьми, реванш, «кто сейчас на сайте».

Как это работает:
  * Игроки (участники клуба или гости) нажимают «Найти соперника» с выбранным контролем времени — попадают
    в очередь (таблица seeks). Как только в очереди двое с одинаковым контролем, создаётся партия.
  * Можно вызвать конкретного игрока по нику или отправить ссылку-приглашение (таблица challenges).
  * Браузеры раз в секунду спрашивают сервер о состоянии партии (короткий опрос — работает на любом хостинге,
    в том числе на бесплатном PythonAnywhere без WebSocket). Если ничего не изменилось, ответ — пара байт.
  * Ходы проверяет сервер (python-chess), время тоже считает сервер — сжульничать нельзя.
  * Часы запускаются после первого хода чёрных. Если первый ход не сделан за 30 секунд — партия отменяется
    (в турнире — засчитывается неявка).
  * Если соперник пропал (закрыл вкладку, пропала связь) больше чем на 30 секунд, можно забрать победу
    или согласиться на ничью. Часы при этом продолжают идти.
  * Glicko-2 рейтинг меняется, только если оба игрока зарегистрированы и партия рейтинговая.
"""
import secrets
import time

import chess
from flask import Blueprint, Response, abort, current_app, g, jsonify, render_template, request, session, url_for

from . import ratings as rt
from .db import db, tx
from .notify import notify
from .pro import looks_for
from .security import limited

bp = Blueprint("online", __name__)

# (база в секундах, прибавка в секундах)
TIME_CONTROLS = [(60, 0), (120, 1), (180, 0), (180, 2), (300, 0), (300, 3), (600, 0), (600, 5), (900, 10),
                 (1800, 0)]
TC_SET = set(TIME_CONTROLS)
ONLINE_WINDOW = 60       # «на сайте» — кто заходил за последние 60 секунд
SEEK_TTL = 12            # заявка на поиск живёт, пока браузер её опрашивает
START_WAIT = 30          # столько секунд на первый ход каждой стороны (в турнире — TOURNAMENT_WAIT)
TOURNAMENT_WAIT = 180
GONE_AFTER = 10          # соперник «не на связи» — не опрашивал партию столько секунд
CLAIM_AFTER = 30         # через столько секунд отсутствия соперника можно забрать победу
CHALLENGE_TTL = 24 * 3600
REASONS = {chess.Termination.CHECKMATE: "checkmate", chess.Termination.STALEMATE: "stalemate",
           chess.Termination.INSUFFICIENT_MATERIAL: "material", chess.Termination.SEVENTYFIVE_MOVES: "fifty",
           chess.Termination.FIFTY_MOVES: "fifty", chess.Termination.FIVEFOLD_REPETITION: "repetition",
           chess.Termination.THREEFOLD_REPETITION: "repetition"}


def tc_label(base, inc):
    m = base // 60 if base % 60 == 0 else base / 60
    return f"{m}+{inc}"


def parse_tc(data):
    """{tc: "5+3"} или старый формат {tc: 300} → (300, 3) или None."""
    tc = data.get("tc", "5+0")
    try:
        if isinstance(tc, str) and "+" in tc:
            a, b = tc.split("+", 1)
            base, inc = int(float(a) * 60), int(b)
        else:
            base, inc = int(tc), int(data.get("inc", 0))
    except (TypeError, ValueError):
        return None
    return (base, inc) if (base, inc) in TC_SET else None


def _mem():
    return current_app.extensions["ladya"].setdefault("presence", {})


def touch(sid):
    """Отметить, что посетитель сейчас на сайте."""
    pres = _mem()
    now = time.time()
    pres[sid] = now
    if len(pres) > 5000:
        for k in [k for k, t in pres.items() if t < now - ONLINE_WINDOW]:
            pres.pop(k, None)


def online_count():
    now = time.time()
    return sum(1 for t in _mem().values() if t > now - ONLINE_WINDOW)


def me():
    """Кто играет: участник клуба или гость. None — если ни то, ни другое."""
    u = g.get("user")
    if u:
        return {"pid": f"u{u['id']}", "name": u["nick"], "uid": u["id"], "guest": False}
    gs = session.get("guest")
    if gs:
        return {"pid": f"g{gs['id']}", "name": gs["name"], "uid": None, "guest": True}
    return None


def need_me():
    p = me()
    if not p:
        return None, (jsonify(error="Войди или зайди как гость"), 401)
    return p, None


# ---------------------------------------------------------------- партии
def board_of(row):
    b = chess.Board()
    for u in row["moves"].split():
        b.push_uci(u)
    return b


def nmoves(row):
    return len(row["moves"].split()) if row["moves"] else 0


def clocks(row, now=None):
    """Оставшееся время (мс) у обеих сторон на текущий момент."""
    now = now or time.time()
    w, b = row["w_ms"], row["b_ms"]
    n = nmoves(row)
    if row["status"] == "active" and n >= 2:          # до первого хода чёрных часы стоят
        spent = int((now - row["last_ts"]) * 1000)
        if n % 2 == 0:
            w -= spent
        else:
            b -= spent
    return max(0, w), max(0, b)


def get_row(gid):
    return db().execute("SELECT * FROM online_games WHERE id=?", (gid,)).fetchone()


def create_game(d, white, black, base, inc, rated, pairing_id=None):
    """white/black: {pid, name, uid}. Возвращает id новой партии."""
    pool = rt.pool_of(base, inc)
    rated = 1 if (rated and white["uid"] and black["uid"] and white["uid"] != black["uid"]) else 0

    def rating_of(p):
        return round(rt.get(d, p["uid"], pool)["rating"]) if p["uid"] else None
    gid = secrets.token_urlsafe(8)
    now = time.time()
    d.execute("INSERT INTO online_games(id,created,tc,inc,pool,w_pid,b_pid,w_name,b_name,w_uid,b_uid,w_rating,"
              "b_rating,rated,w_ms,b_ms,last_ts,pairing_id,start_wait,w_seen,b_seen) "
              "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (gid, now, base, inc, pool, white["pid"], black["pid"], white["name"], black["name"], white["uid"],
               black["uid"], rating_of(white), rating_of(black), rated, base * 1000, base * 1000, now, pairing_id,
               TOURNAMENT_WAIT if pairing_id else START_WAIT, now, now))
    return gid


def finish(row, result, reason):
    """Завершить партию: рейтинг, история партий, результат в турнире. Вызывать внутри tx()."""
    d = db()
    cur = d.execute("SELECT status FROM online_games WHERE id=?", (row["id"],)).fetchone()
    if not cur or cur["status"] != "active":
        return
    wd = bd = None
    now = time.time()
    if result in ("1-0", "0-1", "1/2-1/2"):
        score_w = {"1-0": 1.0, "0-1": 0.0}.get(result, 0.5)
        if row["rated"] and row["w_uid"] and row["b_uid"] and nmoves(row) >= 2:
            wd, bd = rt.rate_game(d, row["w_uid"], row["b_uid"], row["pool"], score_w)
        wr = {1.0: "win", 0.0: "loss", 0.5: "draw"}[score_w]
        br = {"win": "loss", "loss": "win", "draw": "draw"}[wr]
        for uid, color, res, delta, opp_r, opp_n in (
                (row["w_uid"], "w", wr, wd, row["b_rating"], row["b_name"]),
                (row["b_uid"], "b", br, bd, row["w_rating"], row["w_name"])):
            if uid and nmoves(row) >= 2:
                d.execute("INSERT INTO games(user_id,bot,result,moves,mode,delta,opp,online_id,color) "
                          "VALUES(?,?,?,?,?,?,?,?,?)",
                          (uid, opp_r or 0, res, row["moves"], "online", delta or 0, opp_n, row["id"], color))
    w, b = clocks(row, now)
    d.execute("UPDATE online_games SET status='over', result=?, reason=?, draw_offer=NULL, w_delta=?, b_delta=?, "
              "w_ms=?, b_ms=?, last_ts=?, ended=?, ver=ver+1 WHERE id=? AND status='active'",
              (result, reason, wd, bd, w, b, now, now, row["id"]))
    if row["pairing_id"]:
        from .tournaments import record_online_result
        record_online_result(d, row["pairing_id"], result, reason)


def check_time(row):
    """Флажок упал или первый ход так и не сделан — завершаем партию. Возвращает свежую строку."""
    if row["status"] != "active":
        return row
    now = time.time()
    n = nmoves(row)
    if n < 2:
        if now - row["last_ts"] > row["start_wait"]:
            with tx():
                if row["pairing_id"]:
                    # турнир: не сделавший первый ход проигрывает (неявка)
                    finish(row, "0-1" if n == 0 else "1-0", "noshow")
                else:
                    finish(row, "aborted", "aborted")
            return get_row(row["id"])
        return row
    w, b = clocks(row, now)
    side_white = n % 2 == 0
    if (w if side_white else b) <= 0:
        board = board_of(row)
        winner_white = not side_white
        with tx():
            # у соперника не хватает материала для мата — ничья, иначе он выиграл по времени
            if board.has_insufficient_material(chess.WHITE if winner_white else chess.BLACK):
                finish(row, "1/2-1/2", "timeout-material")
            else:
                finish(row, "1-0" if winner_white else "0-1", "timeout")
        return get_row(row["id"])
    return row


def sweep(force=False):
    """Завершить брошенные партии (вызывается не чаще раза в 5 секунд)."""
    mem = current_app.extensions["ladya"]
    now = time.time()
    if not force and now - mem.get("last_sweep", 0) < 5:
        return
    mem["last_sweep"] = now
    for row in db().execute("SELECT * FROM online_games WHERE status='active'").fetchall():
        check_time(row)


def color_of(row, p):
    if not p:
        return None
    return "w" if p["pid"] == row["w_pid"] else "b" if p["pid"] == row["b_pid"] else None


def state(row, p, now=None):
    now = now or time.time()
    w, b = clocks(row, now)
    you = color_of(row, p)
    gone = {c: max(0, int(now - row[f"{c}_seen"])) for c in ("w", "b")}
    look = looks_for(db(), row["w_uid"], row["b_uid"])
    wl, bl = look.get(row["w_uid"], {}), look.get(row["b_uid"], {})
    out = {
        "id": row["id"], "ver": row["ver"], "tc": row["tc"], "inc": row["inc"], "pool": row["pool"],
        "tcLabel": tc_label(row["tc"], row["inc"]), "you": you, "rated": bool(row["rated"]),
        "white": {"name": row["w_name"], "rating": row["w_rating"], "guest": row["w_uid"] is None,
                  "online": gone["w"] < GONE_AFTER, "neon": wl.get("neon", ""),
                  "pro": wl.get("pro", False)},
        "black": {"name": row["b_name"], "rating": row["b_rating"], "guest": row["b_uid"] is None,
                  "online": gone["b"] < GONE_AFTER, "neon": bl.get("neon", ""),
                  "pro": bl.get("pro", False)},
        "moves": row["moves"].split(), "clock": {"w": w, "b": b}, "running": row["status"] == "active" and nmoves(row) >= 2,
        "status": row["status"], "result": row["result"], "reason": row["reason"],
        "draw_offer": row["draw_offer"], "delta": {"w": row["w_delta"], "b": row["b_delta"]},
        "rematch_offer": row["rematch_offer"], "rematch": row["rematch_gid"],
        "tournament": None, "startWait": max(0, int(row["start_wait"] - (now - row["last_ts"]))) if nmoves(row) < 2 else None,
    }
    if you and row["status"] == "active":
        opp = "b" if you == "w" else "w"
        out["oppGone"] = gone[opp] if gone[opp] >= GONE_AFTER else 0
        out["canClaim"] = gone[opp] >= CLAIM_AFTER and nmoves(row) >= 2
    if row["pairing_id"]:
        t = db().execute("SELECT t.id, t.name, p.round FROM t_pairings p JOIN tournaments t ON t.id=p.tournament_id "
                         "WHERE p.id=?", (row["pairing_id"],)).fetchone()
        if t:
            out["tournament"] = {"id": t["id"], "name": t["name"], "round": t["round"],
                                 "url": url_for("tournaments.view", tid=t["id"])}
    return out


def my_active_game(p):
    return db().execute("SELECT * FROM online_games WHERE status='active' AND (w_pid=? OR b_pid=?) "
                        "ORDER BY created DESC LIMIT 1", (p["pid"], p["pid"])).fetchone()


def load_game(gid, p, need_player=True):
    row = get_row(gid)
    if not row:
        return None, (jsonify(error="Партия не найдена"), 404)
    if need_player and (not p or p["pid"] not in (row["w_pid"], row["b_pid"])):
        return None, (jsonify(error="Это не твоя партия"), 403)
    return row, None


def mark_seen(row, p):
    c = color_of(row, p)
    if c and time.time() - row[f"{c}_seen"] > 3:
        db().execute(f"UPDATE online_games SET {c}_seen=? WHERE id=?", (time.time(), row["id"]))


# ---------------------------------------------------------------- страницы
@bp.route("/online")
def lobby():
    p = me()
    return render_template("online.html", me=p, gid="", tcs=[(tc_label(b, i), b, i, rt.POOLS[rt.pool_of(b, i)])
                                                             for b, i in TIME_CONTROLS])


@bp.route("/online/game/<gid>")
def game_page(gid):
    row = get_row(gid)
    if not row:
        abort(404)
    return render_template("online.html", me=me(), gid=gid, tcs=[])


@bp.route("/online/game/<gid>.pgn")
def game_pgn(gid):
    from .pages import build_pgn
    row = get_row(gid)
    if not row:
        abort(404)
    res = {"1-0": "1-0", "0-1": "0-1", "1/2-1/2": "1/2-1/2"}.get(row["result"] or "", "*")
    pgn = build_pgn({"Event": "Ладья: онлайн-партия", "Site": request.host_url.rstrip("/"),
                     "Date": time.strftime("%Y.%m.%d", time.localtime(row["created"])),
                     "White": row["w_name"], "Black": row["b_name"],
                     "WhiteElo": row["w_rating"], "BlackElo": row["b_rating"],
                     "TimeControl": f"{row['tc']}+{row['inc']}", "Termination": row["reason"] or None},
                    row["moves"].split(), res)
    return Response(pgn, mimetype="application/x-chess-pgn",
                    headers={"Content-Disposition": f'attachment; filename="ladya-online-{gid}.pgn"'})


@bp.route("/challenge/<cid>")
def challenge_page(cid):
    ch = db().execute("SELECT * FROM challenges WHERE id=?", (cid,)).fetchone()
    if not ch:
        abort(404)
    p = me()
    return render_template("challenge.html", ch=ch, me=p, mine=bool(p and p["pid"] == ch["from_pid"]),
                           expired=ch["status"] == "open" and time.time() - ch["created"] > CHALLENGE_TTL,
                           label=tc_label(ch["tc"], ch["inc"]), pool=rt.POOLS[rt.pool_of(ch["tc"], ch["inc"])],
                           link=request.host_url.rstrip("/") + url_for("online.challenge_page", cid=cid))


# ---------------------------------------------------------------- API: лобби
@bp.route("/api/ping")
def ping():
    return jsonify(online=online_count())


@bp.route("/api/online/status")
def status():
    p = me()
    sweep()
    d = db()
    now = time.time()
    d.execute("DELETE FROM seeks WHERE ts < ?", (now - SEEK_TTL,))
    searching = d.execute("SELECT COUNT(*) FROM seeks").fetchone()[0]
    playing = d.execute("SELECT COUNT(*) FROM online_games WHERE status='active'").fetchone()[0] * 2
    active = my_active_game(p) if p else None
    incoming = []
    if p and p["uid"]:
        incoming = [{"id": r["id"], "from": r["from_name"], "tc": tc_label(r["tc"], r["inc"]),
                     "rated": bool(r["rated"]), "url": url_for("online.challenge_page", cid=r["id"])}
                    for r in d.execute("SELECT * FROM challenges WHERE to_uid=? AND status='open' AND created>? "
                                       "ORDER BY created DESC LIMIT 5", (p["uid"], now - CHALLENGE_TTL))]
    games = [{"id": r["id"], "white": r["w_name"], "black": r["b_name"], "tc": tc_label(r["tc"], r["inc"]),
              "moves": nmoves(r)}
             for r in d.execute("SELECT * FROM online_games WHERE status='active' ORDER BY created DESC LIMIT 8")]
    return jsonify(online=online_count(), searching=searching, playing=playing,
                   me=p and {"name": p["name"], "guest": p["guest"]},
                   game=active["id"] if active else None, challenges=incoming, games=games)


@bp.route("/api/online/seek", methods=["POST"])
def seek():
    p, err = need_me()
    if err:
        return err
    if limited("seek", p["pid"], 120, 60):
        return jsonify(error="Слишком часто"), 429
    data = request.get_json(silent=True) or {}
    tc = parse_tc(data)
    if not tc:
        return jsonify(error="Неверный контроль времени"), 400
    base, inc = tc
    with tx() as d:
        active = my_active_game(p)
        if active:
            return jsonify(game=active["id"])
        now = time.time()
        d.execute("DELETE FROM seeks WHERE ts < ?", (now - SEEK_TTL,))
        # ищем соперника с тем же контролем времени (не себя самого из второй вкладки)
        opp = d.execute("SELECT * FROM seeks WHERE pid<>? AND tc=? AND inc=? AND (uid IS NULL OR ? IS NULL OR uid<>?) "
                        "ORDER BY since LIMIT 1", (p["pid"], base, inc, p["uid"], p["uid"])).fetchone()
        if not opp:
            old = d.execute("SELECT since, tc, inc FROM seeks WHERE pid=?", (p["pid"],)).fetchone()
            since = old["since"] if old and (old["tc"], old["inc"]) == (base, inc) else now
            d.execute("INSERT INTO seeks(pid,name,uid,rating,tc,inc,ts,since) VALUES(?,?,?,?,?,?,?,?) "
                      "ON CONFLICT(pid) DO UPDATE SET ts=excluded.ts, since=excluded.since, tc=excluded.tc, "
                      "inc=excluded.inc, name=excluded.name", (p["pid"], p["name"], p["uid"], None, base, inc, now, since))
            return jsonify(searching=True, waited=int(now - since))
        d.execute("DELETE FROM seeks WHERE pid IN (?,?)", (opp["pid"], p["pid"]))
        a = {"pid": opp["pid"], "name": opp["name"], "uid": opp["uid"]}
        w, b = (a, p) if secrets.randbelow(2) else (p, a)   # цвета — случайно
        gid = create_game(d, w, b, base, inc, rated=True)
    return jsonify(game=gid)


@bp.route("/api/online/seek/cancel", methods=["POST"])
def seek_cancel():
    p = me()
    if p:
        db().execute("DELETE FROM seeks WHERE pid=?", (p["pid"],))
    return jsonify(ok=True)


# ---------------------------------------------------------------- API: вызовы
@bp.route("/api/online/challenge", methods=["POST"])
def challenge_create():
    p, err = need_me()
    if err:
        return err
    if limited("challenge", p["pid"], 20, 600):
        return jsonify(error="Слишком много вызовов. Подожди немного."), 429
    data = request.get_json(silent=True) or {}
    tc = parse_tc(data)
    if not tc:
        return jsonify(error="Неверный контроль времени"), 400
    color = data.get("color") if data.get("color") in ("white", "black", "random") else "random"
    to_uid, to_name = None, None
    nick = str(data.get("nick") or "").strip()
    d = db()
    if nick:
        if p["guest"]:
            return jsonify(error="Гость может позвать только по ссылке. Войди, чтобы вызывать по нику."), 403
        u = d.execute("SELECT id, nick, blocked FROM users WHERE fold(nick)=?", (nick.casefold(),)).fetchone()
        if not u or u["blocked"]:
            return jsonify(error="Нет игрока с таким ником"), 404
        if u["id"] == p["uid"]:
            return jsonify(error="Нельзя вызвать самого себя"), 400
        to_uid, to_name = u["id"], u["nick"]
    cid = secrets.token_urlsafe(9)
    rated = 1 if (data.get("rated", True) and not p["guest"]) else 0
    with tx():
        d.execute("INSERT INTO challenges(id,created,from_pid,from_name,from_uid,to_uid,tc,inc,color,rated) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)", (cid, time.time(), p["pid"], p["name"], p["uid"], to_uid,
                                                  tc[0], tc[1], color, rated))
        if to_uid:
            notify(to_uid, "challenge", f"{p['name']} вызывает тебя на партию {tc_label(*tc)}",
                   url_for("online.challenge_page", cid=cid))
    return jsonify(id=cid, url=url_for("online.challenge_page", cid=cid), to=to_name)


@bp.route("/api/online/challenge/<cid>")
def challenge_state(cid):
    ch = db().execute("SELECT status, game_id, created FROM challenges WHERE id=?", (cid,)).fetchone()
    if not ch:
        return jsonify(error="Вызов не найден"), 404
    st = ch["status"]
    if st == "open" and time.time() - ch["created"] > CHALLENGE_TTL:
        st = "expired"
    return jsonify(status=st, game=ch["game_id"])


@bp.route("/api/online/challenge/<cid>/<action>", methods=["POST"])
def challenge_action(cid, action):
    p, err = need_me()
    if err:
        return err
    with tx() as d:
        ch = d.execute("SELECT * FROM challenges WHERE id=?", (cid,)).fetchone()
        if not ch:
            return jsonify(error="Вызов не найден"), 404
        if ch["status"] != "open" or time.time() - ch["created"] > CHALLENGE_TTL:
            return jsonify(error="Вызов уже неактуален", status=ch["status"], game=ch["game_id"]), 409
        mine = p["pid"] == ch["from_pid"]
        if action == "cancel" and mine:
            d.execute("UPDATE challenges SET status='cancelled' WHERE id=?", (cid,))
            return jsonify(status="cancelled")
        if mine:
            return jsonify(error="Это твой собственный вызов"), 400
        if ch["to_uid"] and ch["to_uid"] != p["uid"]:
            return jsonify(error="Этот вызов адресован другому игроку"), 403
        if action == "decline":
            d.execute("UPDATE challenges SET status='declined' WHERE id=?", (cid,))
            if ch["from_uid"]:
                notify(ch["from_uid"], "challenge", f"{p['name']} отклонил(а) вызов")
            return jsonify(status="declined")
        if action != "accept":
            return jsonify(error="Неизвестное действие"), 400
        if my_active_game(p):
            return jsonify(error="Сначала доиграй текущую партию"), 409
        creator = {"pid": ch["from_pid"], "name": ch["from_name"], "uid": ch["from_uid"]}
        if my_active_game(creator):
            return jsonify(error="Соперник сейчас играет другую партию — попробуй чуть позже"), 409
        if ch["color"] == "white":
            w, b = creator, p
        elif ch["color"] == "black":
            w, b = p, creator
        else:
            w, b = (creator, p) if secrets.randbelow(2) else (p, creator)
        gid = create_game(d, w, b, ch["tc"], ch["inc"], rated=ch["rated"])
        d.execute("UPDATE challenges SET status='accepted', game_id=? WHERE id=?", (gid, cid))
        if ch["from_uid"]:
            notify(ch["from_uid"], "challenge", f"{p['name']} принял(а) вызов — партия началась!",
                   url_for("online.game_page", gid=gid))
    return jsonify(status="accepted", game=gid)


# ---------------------------------------------------------------- API: партия
@bp.route("/api/online/game/<gid>")
def game_state(gid):
    p = me()
    row, err = load_game(gid, p, need_player=False)
    if err:
        return err
    row = check_time(row)
    mark_seen(row, p)
    v = request.args.get("v", type=int)
    if v is not None and v == row["ver"] and row["status"] == "active":
        # ничего не изменилось — короткий ответ (только присутствие соперника)
        s = state(row, p)
        return jsonify(same=True, ver=row["ver"], oppGone=s.get("oppGone", 0), canClaim=s.get("canClaim", False),
                       white=s["white"]["online"], black=s["black"]["online"], startWait=s["startWait"])
    return jsonify(state(row, p))


def _player_action(gid):
    p, err = need_me()
    if err:
        return None, None, err
    row, err = load_game(gid, p)
    if err:
        return None, None, err
    return p, row, None


@bp.route("/api/online/game/<gid>/move", methods=["POST"])
def game_move(gid):
    p, row, err = _player_action(gid)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    uci, ply = str(data.get("uci", ""))[:5], data.get("ply")
    row = check_time(row)
    with tx() as d:
        row = get_row(gid)
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
        if mv and mv not in board.legal_moves and len(uci) == 4:
            # пешка дошла до последней горизонтали без указания фигуры — превращаем в ферзя
            q = chess.Move.from_uci(uci + "q")
            mv = q if q in board.legal_moves else mv
        if not mv or mv not in board.legal_moves:
            return jsonify(error="Недопустимый ход", state=state(row, p)), 400
        now = time.time()
        left = row[f"{color}_ms"]
        if len(moves) >= 2:          # часы идут с первого хода чёрных; за первые ходы время не тратится
            left = left - int((now - row["last_ts"]) * 1000)
            if left <= 0:
                row = check_time(row)
                return jsonify(error="Время вышло", state=state(get_row(gid), p)), 409
            left += row["inc"] * 1000
        board.push(mv)
        moves.append(mv.uci())
        d.execute(f"UPDATE online_games SET moves=?, {color}_ms=?, last_ts=?, ver=ver+1, {color}_seen=?, "
                  "draw_offer=CASE WHEN draw_offer IS NOT NULL AND draw_offer<>? THEN NULL ELSE draw_offer END "
                  "WHERE id=?", (" ".join(moves), left, now, now, color, gid))
        row = get_row(gid)
        out = board.outcome(claim_draw=True)
        if out:
            finish(row, out.result(), REASONS.get(out.termination, "draw"))
            row = get_row(gid)
    return jsonify(state(row, p))


@bp.route("/api/online/game/<gid>/resign", methods=["POST"])
def game_resign(gid):
    p, row, err = _player_action(gid)
    if err:
        return err
    row = check_time(row)
    with tx():
        row = get_row(gid)
        if row["status"] == "active":
            if nmoves(row) < 2 and not row["pairing_id"]:
                finish(row, "aborted", "aborted")          # до первых ходов — просто отмена
            else:
                finish(row, "0-1" if p["pid"] == row["w_pid"] else "1-0", "resign")
    return jsonify(state(get_row(gid), p))


@bp.route("/api/online/game/<gid>/abort", methods=["POST"])
def game_abort(gid):
    p, row, err = _player_action(gid)
    if err:
        return err
    with tx():
        row = get_row(gid)
        if row["status"] == "active" and nmoves(row) < 2 and not row["pairing_id"]:
            finish(row, "aborted", "aborted")
    return jsonify(state(get_row(gid), p))


@bp.route("/api/online/game/<gid>/claim", methods=["POST"])
def game_claim(gid):
    """Соперник пропал: забрать победу или зафиксировать ничью."""
    p, row, err = _player_action(gid)
    if err:
        return err
    action = (request.get_json(silent=True) or {}).get("action")
    check_time(row)          # если у самого заявителя упал флажок — он проиграл по времени, забирать нечего
    with tx():
        row = get_row(gid)
        s = state(row, p)
        if row["status"] == "active" and s.get("canClaim"):
            if action == "draw":
                finish(row, "1/2-1/2", "left-draw")
            else:
                finish(row, "1-0" if s["you"] == "w" else "0-1", "left")
    return jsonify(state(get_row(gid), p))


@bp.route("/api/online/game/<gid>/draw", methods=["POST"])
def game_draw(gid):
    p, row, err = _player_action(gid)
    if err:
        return err
    action = (request.get_json(silent=True) or {}).get("action")
    row = check_time(row)
    with tx() as d:
        row = get_row(gid)
        if row["status"] == "active" and nmoves(row) >= 2:
            mine = color_of(row, p)
            offer = row["draw_offer"]
            if action == "offer" and not offer:
                d.execute("UPDATE online_games SET draw_offer=?, ver=ver+1 WHERE id=?", (mine, gid))
            elif action == "offer" and offer and offer != mine:      # встречное предложение = согласие
                finish(row, "1/2-1/2", "agreement")
            elif action == "accept" and offer and offer != mine:
                finish(row, "1/2-1/2", "agreement")
            elif action == "decline" and offer and offer != mine:
                d.execute("UPDATE online_games SET draw_offer=NULL, ver=ver+1 WHERE id=?", (gid,))
    return jsonify(state(get_row(gid), p))


@bp.route("/api/online/game/<gid>/rematch", methods=["POST"])
def game_rematch(gid):
    p, row, err = _player_action(gid)
    if err:
        return err
    action = (request.get_json(silent=True) or {}).get("action", "offer")
    with tx() as d:
        row = get_row(gid)
        if row["status"] != "over" or row["pairing_id"] or row["rematch_gid"]:
            return jsonify(state(row, p))
        mine = color_of(row, p)
        offer = row["rematch_offer"]
        if action == "offer" and not offer:
            d.execute("UPDATE online_games SET rematch_offer=?, ver=ver+1 WHERE id=?", (mine, gid))
        elif action in ("offer", "accept") and offer and offer != mine:
            if my_active_game(p):
                return jsonify(error="Сначала доиграй текущую партию", state=state(row, p)), 409
            # цвета меняются
            white = {"pid": row["b_pid"], "name": row["b_name"], "uid": row["b_uid"]}
            black = {"pid": row["w_pid"], "name": row["w_name"], "uid": row["w_uid"]}
            if my_active_game(white if mine == "w" else black):
                return jsonify(error="Соперник уже играет другую партию", state=state(row, p)), 409
            new = create_game(d, white, black, row["tc"], row["inc"], rated=row["rated"])
            d.execute("UPDATE online_games SET rematch_gid=?, rematch_offer=NULL, ver=ver+1 WHERE id=?", (new, gid))
        elif action == "decline" and offer and offer != mine:
            d.execute("UPDATE online_games SET rematch_offer='x', ver=ver+1 WHERE id=?", (gid,))
        elif action == "cancel" and offer == mine:
            d.execute("UPDATE online_games SET rematch_offer=NULL, ver=ver+1 WHERE id=?", (gid,))
    return jsonify(state(get_row(gid), p))


@bp.route("/api/online/game/<gid>/report", methods=["POST"])
def game_report(gid):
    p, row, err = _player_action(gid)
    if err:
        return err
    if limited("report", p["pid"], 5, 3600):
        return jsonify(error="Слишком много жалоб"), 429
    reason = str((request.get_json(silent=True) or {}).get("reason", "")).strip()[:500]
    if len(reason) < 3:
        return jsonify(error="Опиши, что случилось"), 400
    opp = "b" if color_of(row, p) == "w" else "w"
    db().execute("INSERT INTO reports(ts,from_uid,target_uid,target_name,game_id,reason) VALUES(?,?,?,?,?,?)",
                 (time.time(), p["uid"], row[f"{opp}_uid"], row[f"{opp}_name"], gid, reason))
    return jsonify(ok=True)
