"""Обычные страницы, задачи, партии с ботами, рейтинг клуба, просмотр партий и PGN."""
import io
import json
import time

import chess
import chess.pgn
from flask import Blueprint, Response, abort, g, jsonify, redirect, render_template, request, url_for

from . import avatar_url
from . import ratings as rt
from .db import db, tx
from .puzzles import BY_ID, PUZZLES, WEEKLY, weekly_open
from .pro import FREE_PUZZLES, is_pro, puzzle_locked
from .security import limited, login_required

bp = Blueprint("pages", __name__)
BOTS = {200, 600, 1000, 1500, 2000}
COACH_LEVELS = {600, 1200, 1800}


def puzzle_of_day():
    """Задача дня — всегда из бесплатных, чтобы её мог решить каждый."""
    return PUZZLES[int(time.time() // 86400) % FREE_PUZZLES]


@bp.route("/")
def index():
    d = db()
    upcoming = d.execute("SELECT t.id, t.name, t.status, t.mode, c.name AS club FROM tournaments t "
                         "LEFT JOIN clubs c ON c.id=t.club_id WHERE t.status IN ('registration','running') "
                         "ORDER BY t.status='running' DESC, t.id DESC LIMIT 4").fetchall()
    return render_template("index.html", pod=puzzle_of_day(), upcoming=upcoming, npuzzles=len(PUZZLES))


@bp.route("/play")
def play():
    return render_template("play.html")


@bp.route("/puzzles")
def puzzles():
    pr = rt.get(db(), g.user["id"], "puzzle") if g.user else None
    return render_template("puzzles.html", pod=puzzle_of_day(), prating=pr and rt.display(pr),
                           pro=is_pro(g.user), free=FREE_PUZZLES)


@bp.route("/knowledge")
def knowledge():
    return render_template("knowledge.html")


@bp.route("/training")
def training():  # старый адрес раздела
    return redirect(url_for("pages.knowledge"), 301)


@bp.route("/about")
def about():
    return render_template("about.html")


@bp.route("/analysis")
def analysis():
    """Доска анализа: свободная расстановка, движок, стрелки, импорт/экспорт PGN и FEN."""
    moves, fen, title = [], request.args.get("fen", "")[:100], ""
    gid = request.args.get("game", type=int)
    og = request.args.get("online", "")[:20]
    if gid:
        row = db().execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
        if row:
            moves = game_ucis(row)
            title = "Разбор партии"
    elif og:
        row = db().execute("SELECT moves FROM online_games WHERE id=?", (og,)).fetchone()
        if row:
            moves = row["moves"].split()
            title = "Разбор онлайн-партии"
    return render_template("analysis.html", moves_json=json.dumps(moves), fen=fen, title=title)


@bp.route("/favicon.ico")
def favicon():
    return redirect(url_for("static", filename="logo.svg"), 301)


@bp.route("/leaderboard")
def leaderboard():
    pool = request.args.get("pool", "club")
    d = db()
    if pool in rt.POOLS:
        rows = d.execute("""
            SELECT u.id, u.nick, u.avatar, u.neon, u.frame, u.role, u.pro_until, r.rating, r.rd, r.games FROM ratings r JOIN users u ON u.id=r.user_id
            WHERE r.pool=? AND r.games>0 AND u.blocked=0 ORDER BY r.rating DESC LIMIT 100""", (pool,)).fetchall()
        players = [{**dict(r), "ava": avatar_url(r), "rating": round(r["rating"]),
                    "prov": r["rd"] > 110} for r in rows]
    else:
        pool = "club"
        rows = d.execute("""
            SELECT u.id, u.nick, u.avatar, u.neon, u.frame, u.role, u.pro_until, u.rating,
                   (SELECT COUNT(*) FROM games WHERE user_id=u.id) AS games,
                   (SELECT COUNT(*) FROM games WHERE user_id=u.id AND result='win') AS wins,
                   (SELECT COUNT(*) FROM solved WHERE user_id=u.id) AS puzzles
            FROM users u WHERE u.blocked=0 ORDER BY u.rating DESC, wins DESC, u.id ASC LIMIT 100""").fetchall()
        players = [{**dict(r), "ava": avatar_url(r)} for r in rows]
    return render_template("leaderboard.html", players=players, pool=pool, pools=rt.POOLS)


# ---------------------------------------------------------------- задачи
@bp.route("/api/puzzles")
def api_puzzles():
    done, tried = set(), set()
    if g.user:
        done = {r[0] for r in db().execute("SELECT puzzle_id FROM solved WHERE user_id=?", (g.user["id"],))}
        tried = {r[0] for r in db().execute("SELECT puzzle_id FROM puzzle_attempts WHERE user_id=?", (g.user["id"],))}
    pod = puzzle_of_day()["id"]
    out = []
    for p in PUZZLES + WEEKLY[:weekly_open()][::-1]:      # задачи недели — новые первыми
        st = {"solved": p["id"] in done, "tried": p["id"] in tried, "pod": p["id"] == pod}
        if puzzle_locked(g.user, p["id"]):      # без Pro — только название, позицию и решение не отдаём
            out.append({k: p[k] for k in ("id", "title", "theme", "themeName", "icon", "group", "rating")}
                       | st | {"locked": True, "weekly": bool(p.get("weekly"))})
        else:
            out.append({**p, **st, "locked": False})
    return jsonify(out)


@bp.route("/api/puzzle-attempt", methods=["POST"])
@login_required
def api_puzzle_attempt():
    """Первая попытка решить задачу меняет рейтинг в задачах; решённые отмечаются."""
    data = request.get_json(silent=True) or {}
    pid, ok = data.get("id"), bool(data.get("ok"))
    if pid not in BY_ID:
        return jsonify(error="Нет такой задачи"), 400
    if puzzle_locked(g.user, pid):
        return jsonify(error="Эта задача доступна с Rookline Pro"), 403
    if BY_ID[pid].get("weekly") and BY_ID[pid] not in WEEKLY[:weekly_open()]:
        return jsonify(error="Эта задача ещё не открылась"), 400
    if limited("puzzle", g.user["id"], 60, 60):
        return jsonify(error="Слишком часто"), 429
    out = {"ok": True}
    with tx() as d:
        first = not d.execute("SELECT 1 FROM puzzle_attempts WHERE user_id=? AND puzzle_id=?",
                              (g.user["id"], pid)).fetchone()
        if first:
            d.execute("INSERT INTO puzzle_attempts(user_id,puzzle_id,ok,ts) VALUES(?,?,?,?)",
                      (g.user["id"], pid, int(ok), time.time()))
            new, delta = rt.rate_puzzle(d, g.user["id"], BY_ID[pid]["rating"], ok)
            out.update(rating=new, delta=delta)
        if ok:
            d.execute("INSERT OR IGNORE INTO solved VALUES(?,?)", (g.user["id"], pid))
    return jsonify(out)


@bp.route("/api/puzzle-solved", methods=["POST"])
@login_required
def api_solved():   # старый адрес — для совместимости со старыми вкладками
    pid = (request.get_json(silent=True) or {}).get("id")
    if pid not in BY_ID or puzzle_locked(g.user, pid):
        return jsonify(error="bad id"), 400
    db().execute("INSERT OR IGNORE INTO solved VALUES(?,?)", (g.user["id"], pid))
    return jsonify(ok=True)


# ---------------------------------------------------------------- партии с ботами
def bot_game_problem(moves, result, color):
    """Партию с ботом играет браузер, поэтому сервер переигрывает присланные ходы.
    Победа засчитывается, только если партия правда закончилась матом боту или у бота кончилось время
    (то есть сейчас ход бота). Ничья — только если партия длилась хоть сколько-то. Иначе — текст ошибки."""
    if result == "loss":
        return None                      # проигрыш подделывать незачем
    b = chess.Board()
    for tok in moves.split():
        try:
            b.push_san(tok)
        except ValueError:
            return "Ходы партии не сходятся"
    bot_to_move = b.turn != (chess.WHITE if color == "w" else chess.BLACK)
    if result == "win":
        if b.is_checkmate() and bot_to_move:
            return None
        if not b.is_game_over() and bot_to_move and len(b.move_stack) >= 10:
            return None                  # у бота вышло время
        return "Победа не подтверждается ходами партии"
    if b.is_game_over(claim_draw=True) or len(b.move_stack) >= 20:
        return None
    return "Слишком короткая партия для ничьей"


@bp.route("/api/game-result", methods=["POST"])
@login_required
def api_game():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "bot")
    allowed = COACH_LEVELS if mode == "coach" else BOTS if mode == "bot" else set()
    if data.get("bot") not in allowed or data.get("result") not in ("win", "loss", "draw"):
        return jsonify(error="bad data"), 400
    if limited("game", g.user["id"], 30, 60):
        return jsonify(error="Слишком часто"), 429
    moves = str(data.get("moves", ""))[:4000]
    color = data.get("color") if data.get("color") in ("w", "b") else None
    problem = bot_game_problem(moves, data["result"], color or "w")
    if problem:
        return jsonify(error=problem), 400
    with tx() as d:
        cur = d.execute("SELECT rating FROM users WHERE id=?", (g.user["id"],)).fetchone()[0]
        new, delta = rt.club_rating_change(cur, data["bot"], data["result"])
        gid = d.execute("INSERT INTO games(user_id,bot,result,moves,mode,delta,color) VALUES(?,?,?,?,?,?,?)",
                        (g.user["id"], data["bot"], data["result"], moves, mode, delta, color)).lastrowid
        d.execute("UPDATE users SET rating=? WHERE id=?", (new, g.user["id"]))
    return jsonify(ok=True, rating=new, delta=delta, gid=gid)


# ---------------------------------------------------------------- просмотр партий и PGN
def game_ucis(row):
    """Ходы партии из таблицы games в формате UCI (у ботов хранится SAN, у онлайн — UCI)."""
    b, out = chess.Board(), []
    for tok in row["moves"].split():
        try:
            mv = chess.Move.from_uci(tok) if row["mode"] == "online" else b.parse_san(tok)
            if mv not in b.legal_moves:
                break
        except ValueError:
            break
        b.push(mv)
        out.append(mv.uci())
    return out


def build_pgn(headers, ucis, result="*"):
    game = chess.pgn.Game()
    for k, v in headers.items():
        if v is not None:
            game.headers[k] = str(v)
    game.headers["Result"] = result
    node, b = game, chess.Board()
    for u in ucis:
        mv = chess.Move.from_uci(u)
        if mv not in b.legal_moves:
            break
        node = node.add_variation(mv)
        b.push(mv)
    return str(game) + "\n"


def _game_meta(row):
    u = db().execute("SELECT nick FROM users WHERE id=?", (row["user_id"],)).fetchone()
    me = u["nick"] if u else "Игрок"
    if row["mode"] == "online":
        opp = f"{row['opp']}"
    elif row["mode"] == "coach":
        opp = f"Тренер Альберт ({row['bot']})"
    else:
        opp = f"Бот {row['bot']}"
    return me, opp


@bp.route("/game/<int:gid>")
def view_game(gid):
    row = db().execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
    if not row:
        abort(404)
    if row["online_id"]:
        return redirect(url_for("online.game_page", gid=row["online_id"]))
    me, opp = _game_meta(row)
    ucis = game_ucis(row)
    return render_template("game.html", row=row, me=me, opp=opp, moves_json=json.dumps(ucis),
                           pgn_url=url_for("pages.game_pgn", gid=gid))


@bp.route("/game/<int:gid>.pgn")
def game_pgn(gid):
    row = db().execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
    if not row:
        abort(404)
    me, opp = _game_meta(row)
    ucis = game_ucis(row)
    # у старых партий цвет не хранился — тогда считаем, что игрок играл белыми
    black = row["color"] == "b"
    res = {"win": "1-0", "loss": "0-1", "draw": "1/2-1/2"}[row["result"]]
    if black and res != "1/2-1/2":
        res = "0-1" if res == "1-0" else "1-0"
    event = {"coach": "Ладья: партия с тренером", "online": "Ладья: онлайн-партия"}.get(row["mode"], "Ладья: партия с ботом")
    pgn = build_pgn({"Event": event, "Site": request.host_url.rstrip("/"), "Date": row["created"][:10].replace("-", "."),
                     "White": opp if black else me, "Black": me if black else opp}, ucis, res)
    return Response(pgn, mimetype="application/x-chess-pgn",
                    headers={"Content-Disposition": f'attachment; filename="ladya-{gid}.pgn"'})
