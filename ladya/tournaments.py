"""Турниры: швейцарская или круговая система, онлайн (партии на сайте) или очно (результаты вводит тренер)."""
import time

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import ratings as rt
from . import swiss
from .clubs import can_manage as can_manage_club, managed_clubs, membership
from .db import db, tx
from .notify import notify
from .security import is_admin, is_coach, limited, login_required, role_required

bp = Blueprint("tournaments", __name__)
MODES = {"online": "Онлайн — партии играются на сайте, результат записывается сам",
         "offline": "Очно — за доской в клубе, результаты вводит тренер"}
SYSTEMS = {"swiss": "Швейцарская", "rr": "Круговая (все со всеми)"}
STATUS = {"registration": "регистрация", "running": "идёт", "finished": "завершён"}
RESULTS = ["1-0", "1/2", "0-1", "+-", "-+", "--"]
TCS = [(60, 0), (180, 0), (180, 2), (300, 0), (300, 3), (600, 0), (600, 5), (900, 10), (1800, 0), (2700, 15),
       (3600, 30)]


def tc_text(base, inc):
    m = base // 60
    return f"{m}+{inc}"


def get_t(tid):
    t = db().execute("SELECT * FROM tournaments WHERE id=?", (tid,)).fetchone()
    if not t:
        abort(404)
    return t


def can_manage(t, u=None):
    u = u if u is not None else g.get("user")
    if not u:
        return False
    if is_admin(u) or t["owner_id"] == u["id"]:
        return True
    if t["club_id"]:
        club = db().execute("SELECT * FROM clubs WHERE id=?", (t["club_id"],)).fetchone()
        return bool(club and can_manage_club(club, u))
    return False


def need_manage(t):
    if not can_manage(t):
        abort(403)


def players_of(tid):
    return db().execute("SELECT * FROM t_players WHERE tournament_id=? ORDER BY COALESCE(seed, 9999), rating DESC, id",
                        (tid,)).fetchall()


def pairings_of(tid):
    return db().execute("SELECT * FROM t_pairings WHERE tournament_id=? ORDER BY round, board", (tid,)).fetchall()


def as_swiss(pairings):
    return [{"round": x["round"], "white": x["white_id"], "black": x["black_id"], "result": x["result"]}
            for x in pairings]


def round_complete(tid, rnd):
    d = db()
    n = d.execute("SELECT COUNT(*) FROM t_pairings WHERE tournament_id=? AND round=?", (tid, rnd)).fetchone()[0]
    left = d.execute("SELECT COUNT(*) FROM t_pairings WHERE tournament_id=? AND round=? AND result IS NULL",
                     (tid, rnd)).fetchone()[0]
    return n > 0 and left == 0


def rr_schedule(t):
    seeded = db().execute("SELECT id FROM t_players WHERE tournament_id=? AND seed IS NOT NULL ORDER BY seed",
                          (t["id"],)).fetchall()
    return swiss.round_robin([r["id"] for r in seeded])


def standings_of(t, players=None, pairings=None):
    players = players if players is not None else players_of(t["id"])
    pairings = pairings if pairings is not None else pairings_of(t["id"])
    ps = [{"id": p["id"], "name": p["name"], "rating": p["rating"], "withdrawn": p["withdrawn"],
           "user_id": p["user_id"]} for p in players]
    done = [r for r in range(1, t["current_round"] + 1) if round_complete(t["id"], r)]
    return swiss.standings(ps, as_swiss(pairings), t["bye_points"], rounds_done=max(done) if done else 0)


# ---------------------------------------------------------------- результаты онлайн-партий
def record_online_result(d, pairing_id, result, reason):
    """Вызывается из online.finish(): записать результат партии турнира."""
    res = {"1-0": "1-0", "0-1": "0-1", "1/2-1/2": "1/2"}.get(result)
    if reason == "noshow" and res in ("1-0", "0-1"):
        res = "+-" if res == "1-0" else "-+"
    if not res:
        return
    p = d.execute("SELECT * FROM t_pairings WHERE id=?", (pairing_id,)).fetchone()
    if not p or p["result"]:
        return
    d.execute("UPDATE t_pairings SET result=? WHERE id=?", (res, pairing_id))
    _after_result(d, p["tournament_id"], p["round"])


def _after_result(d, tid, rnd):
    t = d.execute("SELECT * FROM tournaments WHERE id=?", (tid,)).fetchone()
    if not t or t["status"] != "running" or rnd != t["current_round"] or not round_complete(tid, rnd):
        return
    if rnd >= t["rounds"]:
        d.execute("UPDATE tournaments SET status='finished' WHERE id=?", (tid,))
        for p in d.execute("SELECT user_id FROM t_players WHERE tournament_id=? AND user_id IS NOT NULL", (tid,)):
            notify(p["user_id"], "tournament", f"Турнир «{t['name']}» завершён — смотри итоговую таблицу",
                   url_for("tournaments.view", tid=tid), d=d)
    elif t["owner_id"]:
        notify(t["owner_id"], "tournament", f"«{t['name']}»: тур {rnd} завершён — можно делать жеребьёвку "
               f"тура {rnd + 1}", url_for("tournaments.view", tid=tid), d=d)


# ---------------------------------------------------------------- списки и создание
@bp.route("/tournaments")
def index():
    d = db()
    rows = d.execute("""SELECT t.*, c.name AS club, u.nick AS owner,
                          (SELECT COUNT(*) FROM t_players WHERE tournament_id=t.id) AS n
                        FROM tournaments t LEFT JOIN clubs c ON c.id=t.club_id LEFT JOIN users u ON u.id=t.owner_id
                        ORDER BY CASE t.status WHEN 'running' THEN 0 WHEN 'registration' THEN 1 ELSE 2 END, t.id DESC
                        LIMIT 200""").fetchall()
    return render_template("tournaments.html", rows=rows, STATUS=STATUS, MODES=MODES, SYSTEMS=SYSTEMS, tc_text=tc_text)


def _form(t=None):
    f = request.form
    name = " ".join(f.get("name", "").split())[:80]
    desc = f.get("description", "").strip()[:2000]
    try:
        rounds = int(f.get("rounds", 5))
        base, inc = (int(x) for x in f.get("tc", "600+0").split("+"))
        bye = float(f.get("bye_points", 1))
    except ValueError:
        return None, "Проверь числа в настройках."
    mode = f.get("mode") if f.get("mode") in MODES else "offline"
    system = f.get("system") if f.get("system") in SYSTEMS else "swiss"
    club_id = f.get("club_id", type=int) or None
    if len(name) < 3:
        return None, "Название турнира — от 3 до 80 символов."
    if not 1 <= rounds <= 15:
        return None, "Число туров — от 1 до 15."
    if (base, inc) not in TCS:
        return None, "Выбери контроль времени из списка."
    if bye not in (0, 0.5, 1):
        return None, "Очки за свободный тур: 0, ½ или 1."
    if club_id:
        club = db().execute("SELECT * FROM clubs WHERE id=?", (club_id,)).fetchone()
        if not club or not can_manage_club(club):
            return None, "Можно выбрать только клуб, которым ты управляешь."
    starts = f.get("starts", "").strip()[:16] or None
    return dict(name=name, description=desc, rounds=rounds, tc=base, inc=inc, bye_points=bye, mode=mode,
                system=system, club_id=club_id, starts=starts), None


@bp.route("/tournaments/new", methods=["GET", "POST"])
@role_required("coach")
def new():
    if request.method == "POST":
        data, err = _form()
        if err:
            flash(err)
        elif limited("t-new", g.user["id"], 30, 86400):
            flash("Слишком много турниров за сутки.")
        else:
            cur = db().execute("INSERT INTO tournaments(name,description,club_id,owner_id,rounds,tc,inc,mode,bye_points,"
                               "system,starts) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                               (data["name"], data["description"], data["club_id"], g.user["id"], data["rounds"],
                                data["tc"], data["inc"], data["mode"], data["bye_points"], data["system"],
                                data["starts"]))
            flash("✔ Турнир создан. Добавь участников или дождись их регистрации.", "ok")
            return redirect(url_for("tournaments.view", tid=cur.lastrowid))
    club_id = request.args.get("club", type=int)
    return render_template("tournament_form.html", t=None, clubs=managed_clubs(g.user), club_id=club_id,
                           MODES=MODES, SYSTEMS=SYSTEMS, TCS=TCS, tc_text=tc_text)


@bp.route("/t/<int:tid>/edit", methods=["GET", "POST"])
@login_required
def edit(tid):
    t = get_t(tid)
    need_manage(t)
    if request.method == "POST":
        data, err = _form(t)
        if err:
            flash(err)
        elif t["status"] != "registration" and (data["mode"], data["system"], data["tc"], data["inc"]) != \
                (t["mode"], t["system"], t["tc"], t["inc"]):
            flash("После старта можно менять только название, описание, число туров и очки за bye.")
        elif t["status"] != "registration" and data["rounds"] < t["current_round"]:
            flash("Туров не может быть меньше, чем уже сыграно.")
        else:
            db().execute("UPDATE tournaments SET name=?, description=?, club_id=?, rounds=?, tc=?, inc=?, mode=?, "
                         "bye_points=?, system=?, starts=? WHERE id=?",
                         (data["name"], data["description"], data["club_id"], data["rounds"] if t["system"] != "rr"
                          or t["status"] == "registration" else t["rounds"], data["tc"], data["inc"], data["mode"],
                          data["bye_points"], data["system"], data["starts"], tid))
            flash("✔ Сохранено.", "ok")
            return redirect(url_for("tournaments.view", tid=tid))
    return render_template("tournament_form.html", t=t, clubs=managed_clubs(g.user), club_id=t["club_id"],
                           MODES=MODES, SYSTEMS=SYSTEMS, TCS=TCS, tc_text=tc_text)


# ---------------------------------------------------------------- страница турнира
def _view_data(t):
    d = db()
    players = players_of(t["id"])
    pairings = pairings_of(t["id"])
    names = {p["id"]: p for p in players}
    rounds = {}
    for x in pairings:
        rounds.setdefault(x["round"], []).append(x)
    table = standings_of(t, players, pairings)
    club = d.execute("SELECT * FROM clubs WHERE id=?", (t["club_id"],)).fetchone() if t["club_id"] else None
    return dict(players=players, names=names, rounds=rounds, table=table, club=club)


@bp.route("/t/<int:tid>")
def view(tid):
    t = get_t(tid)
    if t["mode"] == "online" and t["status"] == "running":
        from .online import sweep
        sweep()
        t = get_t(tid)
    data = _view_data(t)
    me_p = None
    if g.user:
        me_p = next((p for p in data["players"] if p["user_id"] == g.user["id"]), None)
    can_join = bool(g.user and not me_p and t["status"] == "registration" and
                    (not t["club_id"] or membership(t["club_id"]) or can_manage(t)))
    my_game = None
    if me_p and t["status"] == "running":
        my_game = db().execute("SELECT g.id FROM online_games g JOIN t_pairings p ON p.id=g.pairing_id "
                               "WHERE p.tournament_id=? AND g.status='active' AND (p.white_id=? OR p.black_id=?)",
                               (tid, me_p["id"], me_p["id"])).fetchone()
    manage = can_manage(t)
    can_pair = manage and t["status"] == "running" and t["current_round"] < t["rounds"] and \
        (t["current_round"] == 0 or round_complete(tid, t["current_round"]))
    sel_round = request.args.get("round", type=int) or t["current_round"]
    return render_template("tournament.html", t=t, manage=manage, me_p=me_p, can_join=can_join, my_game=my_game,
                           can_pair=can_pair, sel_round=sel_round, STATUS=STATUS, MODES=MODES, SYSTEMS=SYSTEMS,
                           RESULTS=RESULTS, LABEL=swiss.RESULT_LABEL, tc_text=tc_text, **data)


@bp.route("/api/t/<int:tid>/sig")
def signature(tid):
    """Короткая «подпись» состояния турнира — страница перезагружается, только если что-то изменилось."""
    d = db()
    t = get_t(tid)
    if t["mode"] == "online" and t["status"] == "running":
        from .online import sweep
        sweep()
    r = d.execute("SELECT COUNT(*), COUNT(result), COALESCE(MAX(id),0) FROM t_pairings WHERE tournament_id=?",
                  (tid,)).fetchone()
    n = d.execute("SELECT COUNT(*), COALESCE(SUM(withdrawn),0) FROM t_players WHERE tournament_id=?", (tid,)).fetchone()
    t = get_t(tid)
    return jsonify(sig=f"{t['status']}:{t['current_round']}:{r[0]}:{r[1]}:{r[2]}:{n[0]}:{n[1]}")


def _print(tid, kind, rnd=None):
    t = get_t(tid)
    data = _view_data(t)
    return render_template("tournament_print.html", t=t, kind=kind, rnd=rnd, LABEL=swiss.RESULT_LABEL,
                           tc_text=tc_text, MODES=MODES, SYSTEMS=SYSTEMS, **data)


@bp.route("/t/<int:tid>/print")
def print_standings(tid):
    return _print(tid, "standings")


@bp.route("/t/<int:tid>/print/round/<int:rnd>")
def print_round(tid, rnd):
    return _print(tid, "round", rnd)


# ---------------------------------------------------------------- регистрация игроков
def _user_rating(d, uid, t):
    pool = rt.pool_of(t["tc"], t["inc"])
    r = rt.get(d, uid, pool)
    if r["games"]:
        return round(r["rating"])
    return d.execute("SELECT rating FROM users WHERE id=?", (uid,)).fetchone()[0]


@bp.route("/t/<int:tid>/join", methods=["POST"])
@login_required
def join(tid):
    t = get_t(tid)
    if t["status"] != "registration":
        flash("Регистрация уже закрыта — попроси тренера добавить тебя вручную.")
    elif t["club_id"] and not membership(t["club_id"]) and not can_manage(t):
        flash("Это турнир клуба — сначала вступи в клуб.")
    else:
        d = db()
        d.execute("INSERT OR IGNORE INTO t_players(tournament_id,user_id,name,rating) VALUES(?,?,?,?)",
                  (tid, g.user["id"], g.user["nick"], _user_rating(d, g.user["id"], t)))
        flash("✔ Ты в турнире!", "ok")
    return redirect(url_for("tournaments.view", tid=tid))


@bp.route("/t/<int:tid>/leave", methods=["POST"])
@login_required
def leave(tid):
    t = get_t(tid)
    if t["status"] == "registration":
        db().execute("DELETE FROM t_players WHERE tournament_id=? AND user_id=?", (tid, g.user["id"]))
        flash("Ты снялся(ась) с турнира.", "ok")
    else:
        flash("Турнир уже идёт — чтобы сняться, напиши тренеру.")
    return redirect(url_for("tournaments.view", tid=tid))


@bp.route("/t/<int:tid>/players/add", methods=["POST"])
@login_required
def add_player(tid):
    t = get_t(tid)
    need_manage(t)
    d = db()
    nick = request.form.get("nick", "").strip()
    name = " ".join(request.form.get("name", "").split())[:40]
    rating = request.form.get("rating", type=int) or 0
    if t["status"] == "finished":
        flash("Турнир завершён.")
    elif t["system"] == "rr" and t["status"] == "running":
        flash("В круговой турнир после старта добавлять игроков нельзя.")
    elif nick:
        u = d.execute("SELECT id, nick FROM users WHERE fold(nick)=?", (nick.casefold(),)).fetchone()
        if not u:
            flash("Нет участника с таким ником. Для игрока без аккаунта впиши имя в поле «Имя».")
        elif d.execute("SELECT 1 FROM t_players WHERE tournament_id=? AND user_id=?", (tid, u["id"])).fetchone():
            flash("Этот игрок уже в турнире.")
        else:
            d.execute("INSERT INTO t_players(tournament_id,user_id,name,rating,joined_round) VALUES(?,?,?,?,?)",
                      (tid, u["id"], u["nick"], rating or _user_rating(d, u["id"], t), t["current_round"] + 1))
            notify(u["id"], "tournament", f"Тебя добавили в турнир «{t['name']}»", url_for("tournaments.view", tid=tid))
            flash(f"✔ {u['nick']} добавлен(а).", "ok")
    elif len(name) >= 2:
        if t["mode"] == "online":
            flash("В онлайн-турнире играют только участники с аккаунтом (партии идут на сайте).")
        else:
            d.execute("INSERT INTO t_players(tournament_id,user_id,name,rating,joined_round) VALUES(?,NULL,?,?,?)",
                      (tid, name, max(0, min(3500, rating)), t["current_round"] + 1))
            flash(f"✔ {name} добавлен(а).", "ok")
    else:
        flash("Впиши ник участника сайта или имя игрока без аккаунта.")
    return redirect(url_for("tournaments.view", tid=tid) + "#players")


@bp.route("/t/<int:tid>/players/<int:pid>/<action>", methods=["POST"])
@login_required
def player_action(tid, pid, action):
    t = get_t(tid)
    d = db()
    p = d.execute("SELECT * FROM t_players WHERE id=? AND tournament_id=?", (pid, tid)).fetchone()
    if not p:
        abort(404)
    need_manage(t)
    if action == "remove":
        if t["status"] != "registration" and d.execute(
                "SELECT 1 FROM t_pairings WHERE white_id=? OR black_id=?", (pid, pid)).fetchone():
            flash("Игрок уже играл — его можно только снять (результаты сохранятся).")
        else:
            d.execute("DELETE FROM t_players WHERE id=?", (pid,))
            flash("Игрок удалён из турнира.", "ok")
    elif action == "withdraw":
        d.execute("UPDATE t_players SET withdrawn=1 WHERE id=?", (pid,))
        flash(f"{p['name']} снят(а) с турнира: в следующих турах пар не будет.", "ok")
    elif action == "reinstate":
        d.execute("UPDATE t_players SET withdrawn=0 WHERE id=?", (pid,))
        flash(f"{p['name']} снова участвует.", "ok")
    elif action == "rating":
        r = request.form.get("rating", type=int)
        if r is not None and 0 <= r <= 3500:
            d.execute("UPDATE t_players SET rating=? WHERE id=?", (r, pid))
            flash("✔ Рейтинг для посева изменён.", "ok")
    return redirect(url_for("tournaments.view", tid=tid) + "#players")


# ---------------------------------------------------------------- старт, жеребьёвка, результаты
@bp.route("/t/<int:tid>/start", methods=["POST"])
@login_required
def start(tid):
    t = get_t(tid)
    need_manage(t)
    players = [p for p in players_of(tid) if not p["withdrawn"]]
    if t["status"] != "registration":
        return redirect(url_for("tournaments.view", tid=tid))
    if len(players) < 2:
        flash("Нужно хотя бы 2 участника.")
        return redirect(url_for("tournaments.view", tid=tid))
    with tx() as d:
        order = sorted(players, key=lambda p: (-p["rating"], p["name"].casefold(), p["id"]))
        for i, p in enumerate(order, 1):
            d.execute("UPDATE t_players SET seed=? WHERE id=?", (i, p["id"]))
        rounds = t["rounds"]
        if t["system"] == "rr":
            rounds = len(order) - 1 if len(order) % 2 == 0 else len(order)
            d.execute("UPDATE tournaments SET bye_points=0 WHERE id=?", (tid,))
        elif rounds > len(order) - 1 + len(order) % 2:
            rounds = len(order) - 1 + len(order) % 2
            flash(f"Туров больше, чем возможно без повторных встреч, — уменьшено до {rounds}.")
        d.execute("UPDATE tournaments SET status='running', rounds=? WHERE id=?", (rounds, tid))
    flash("✔ Турнир начался! Теперь сделай жеребьёвку 1-го тура.", "ok")
    return redirect(url_for("tournaments.view", tid=tid))


def make_pairings(t):
    """Сделать жеребьёвку следующего тура. Возвращает номер тура."""
    tid, rnd = t["id"], t["current_round"] + 1
    d = db()
    players = players_of(tid)
    pairings = pairings_of(tid)
    active = [p for p in players if not p["withdrawn"]]
    rows = []      # (white, black|None, result)
    if t["system"] == "rr":
        sched = rr_schedule(t)
        withdrawn = {p["id"] for p in players if p["withdrawn"]}
        for w, b in sched[rnd - 1]:
            if b is None:
                rows.append((w, None, "bye"))
            elif w in withdrawn and b in withdrawn:
                rows.append((w, b, "--"))
            elif w in withdrawn:
                rows.append((w, b, "-+"))
            elif b in withdrawn:
                rows.append((w, b, "+-"))
            else:
                rows.append((w, b, None))
    else:
        info = swiss.history([dict(p) for p in players],
                             [dict(x, bye_points=t["bye_points"]) for x in as_swiss(pairings)])
        pairs, bye = swiss.pair_round([info[p["id"]] for p in active], rnd, rounds_left=t["rounds"] - rnd)
        rows = [(w, b, None) for w, b in pairs]
        if bye:
            rows.append((bye, None, "bye"))
    with tx():
        for board, (w, b, res) in enumerate(rows, 1):
            cur = d.execute("INSERT INTO t_pairings(tournament_id,round,board,white_id,black_id,result) "
                            "VALUES(?,?,?,?,?,?)", (tid, rnd, board, w, b, res))
            if t["mode"] == "online" and b is not None and res is None:
                _start_online_game(d, t, cur.lastrowid, w, b, rnd, board)
            elif b is None:
                pl = d.execute("SELECT user_id FROM t_players WHERE id=?", (w,)).fetchone()
                if pl and pl["user_id"]:
                    notify(pl["user_id"], "tournament", f"«{t['name']}», тур {rnd}: у тебя свободный тур (bye)",
                           url_for("tournaments.view", tid=tid), d=d)
        d.execute("UPDATE tournaments SET current_round=? WHERE id=?", (rnd, tid))
        if t["mode"] == "offline":
            for (w, b, res) in rows:
                for pid in (w, b):
                    pl = pid and d.execute("SELECT user_id FROM t_players WHERE id=?", (pid,)).fetchone()
                    if pl and pl["user_id"] and b is not None:
                        notify(pl["user_id"], "tournament", f"«{t['name']}»: опубликованы пары {rnd}-го тура",
                               url_for("tournaments.view", tid=tid, round=rnd), d=d)
        _after_result(d, tid, rnd)        # тур мог сразу оказаться завершённым (bye/неявки)
    return rnd


def _start_online_game(d, t, pairing_id, w, b, rnd, board):
    from .online import create_game
    pw = d.execute("SELECT * FROM t_players WHERE id=?", (w,)).fetchone()
    pb = d.execute("SELECT * FROM t_players WHERE id=?", (b,)).fetchone()
    if not (pw["user_id"] and pb["user_id"]):
        return
    white = {"pid": f"u{pw['user_id']}", "name": pw["name"], "uid": pw["user_id"]}
    black = {"pid": f"u{pb['user_id']}", "name": pb["name"], "uid": pb["user_id"]}
    gid = create_game(d, white, black, t["tc"], t["inc"], rated=True, pairing_id=pairing_id)
    d.execute("UPDATE t_pairings SET game_id=? WHERE id=?", (gid, pairing_id))
    for uid, opp in ((pw["user_id"], pb["name"]), (pb["user_id"], pw["name"])):
        notify(uid, "tournament", f"«{t['name']}», тур {rnd}, доска {board}: твоя партия против {opp} началась!",
               url_for("online.game_page", gid=gid), d=d)


@bp.route("/t/<int:tid>/pair", methods=["POST"])
@login_required
def pair(tid):
    t = get_t(tid)
    need_manage(t)
    if t["status"] != "running":
        flash("Сначала начни турнир.")
    elif t["current_round"] >= t["rounds"]:
        flash("Все туры уже сыграны.")
    elif t["current_round"] and not round_complete(tid, t["current_round"]):
        flash(f"Тур {t['current_round']} ещё не закончен — внеси все результаты.")
    elif len([p for p in players_of(tid) if not p["withdrawn"]]) < 2:
        flash("Недостаточно активных игроков.")
    else:
        rnd = make_pairings(t)
        flash(f"✔ Жеребьёвка {rnd}-го тура готова.", "ok")
        return redirect(url_for("tournaments.view", tid=tid, round=rnd))
    return redirect(url_for("tournaments.view", tid=tid))


@bp.route("/t/<int:tid>/unpair", methods=["POST"])
@login_required
def unpair(tid):
    """Отменить жеребьёвку последнего тура (если в нём ещё нет результатов)."""
    t = get_t(tid)
    need_manage(t)
    rnd = t["current_round"]
    d = db()
    played = d.execute("SELECT COUNT(*) FROM t_pairings WHERE tournament_id=? AND round=? AND result IS NOT NULL "
                       "AND black_id IS NOT NULL", (tid, rnd)).fetchone()[0]
    moved = d.execute("SELECT COUNT(*) FROM online_games g JOIN t_pairings p ON p.id=g.pairing_id "
                      "WHERE p.tournament_id=? AND p.round=? AND g.moves<>''", (tid, rnd)).fetchone()[0]
    if not rnd or t["status"] != "running":
        flash("Нечего отменять.")
    elif played or moved:
        flash("В туре уже есть результаты или сделанные ходы — отменить жеребьёвку нельзя. Исправь результаты вручную.")
    else:
        with tx():
            d.execute("UPDATE online_games SET status='over', result='aborted', reason='aborted', ver=ver+1 "
                      "WHERE status='active' AND pairing_id IN (SELECT id FROM t_pairings WHERE tournament_id=? "
                      "AND round=?)", (tid, rnd))
            d.execute("DELETE FROM t_pairings WHERE tournament_id=? AND round=?", (tid, rnd))
            d.execute("UPDATE tournaments SET current_round=? WHERE id=?", (rnd - 1, tid))
        flash(f"Жеребьёвка {rnd}-го тура отменена.", "ok")
    return redirect(url_for("tournaments.view", tid=tid))


@bp.route("/t/<int:tid>/result/<int:pid>", methods=["POST"])
@login_required
def set_result(tid, pid):
    t = get_t(tid)
    need_manage(t)
    res = request.form.get("result", "")
    with tx() as d:
        p = d.execute("SELECT * FROM t_pairings WHERE id=? AND tournament_id=?", (pid, tid)).fetchone()
        if not p or p["black_id"] is None:
            abort(404)
        if res == "":
            if p["round"] != t["current_round"]:
                flash("Очистить можно только результат текущего тура.")
                return redirect(url_for("tournaments.view", tid=tid, round=p["round"]))
            d.execute("UPDATE t_pairings SET result=NULL WHERE id=?", (pid,))
        elif res in RESULTS:
            if p["game_id"]:      # тренер решил исход онлайн-партии — останавливаем её
                d.execute("UPDATE online_games SET status='over', result=?, reason='arbiter', ver=ver+1 "
                          "WHERE id=? AND status='active'",
                          ({"1-0": "1-0", "0-1": "0-1", "+-": "1-0", "-+": "0-1"}.get(res, "1/2-1/2"), p["game_id"]))
            d.execute("UPDATE t_pairings SET result=? WHERE id=?", (res, pid))
            if t["status"] == "finished":
                pass
            else:
                _after_result(d, tid, p["round"])
        else:
            abort(400)
    flash("✔ Результат записан.", "ok")
    return redirect(url_for("tournaments.view", tid=tid, round=p["round"]))


@bp.route("/t/<int:tid>/finish", methods=["POST"])
@login_required
def finish(tid):
    t = get_t(tid)
    need_manage(t)
    if t["current_round"] and not round_complete(tid, t["current_round"]):
        flash("Сначала внеси результаты последнего тура.")
    else:
        db().execute("UPDATE tournaments SET status='finished', rounds=MAX(current_round,1) WHERE id=?", (tid,))
        flash("Турнир завершён.", "ok")
    return redirect(url_for("tournaments.view", tid=tid))


@bp.route("/t/<int:tid>/reopen", methods=["POST"])
@login_required
def reopen(tid):
    t = get_t(tid)
    need_manage(t)
    if t["status"] == "finished":
        db().execute("UPDATE tournaments SET status='running' WHERE id=?", (tid,))
        flash("Турнир снова открыт: можно исправлять результаты или добавить туры.", "ok")
    return redirect(url_for("tournaments.view", tid=tid))


@bp.route("/t/<int:tid>/delete", methods=["POST"])
@login_required
def delete(tid):
    t = get_t(tid)
    need_manage(t)
    if request.form.get("confirm", "").strip().casefold() != t["name"].casefold():
        flash("Для удаления впиши точное название турнира.")
        return redirect(url_for("tournaments.view", tid=tid))
    with tx() as d:
        d.execute("UPDATE online_games SET status='over', result='aborted', reason='aborted' WHERE status='active' AND "
                  "pairing_id IN (SELECT id FROM t_pairings WHERE tournament_id=?)", (tid,))
        d.execute("UPDATE online_games SET pairing_id=NULL WHERE pairing_id IN "
                  "(SELECT id FROM t_pairings WHERE tournament_id=?)", (tid,))
        d.execute("DELETE FROM tournaments WHERE id=?", (tid,))
    flash("Турнир удалён.", "ok")
    return redirect(url_for("tournaments.index"))
