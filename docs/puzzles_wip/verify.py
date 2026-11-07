"""Проверка задач: решение работает, оно единственное, ответы соперника — лучшая защита.

Вход: puzzles_src.py (список SRC). Выход: печать отчёта + out.json с готовыми линиями (UCI).
mate-задачи: точный перебор матов. tactic: перебор с материальной оценкой и форсированными взятиями.
"""
import json
import sys
import time

import chess

sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else ".")
import importlib  # noqa: E402
MOD = sys.argv[2] if len(sys.argv) > 2 else "puzzles_src"
SRC = importlib.import_module(MOD).SRC  # noqa: E402

VAL = {chess.PAWN: 100, chess.KNIGHT: 300, chess.BISHOP: 310, chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}
MATE = 100000


def material(b):
    s = 0
    for p in b.piece_map().values():
        s += VAL[p.piece_type] if p.color == b.turn else -VAL[p.piece_type]
    return s


# ---------------------------------------------------------------- маты
TT = {}


def mates_in(b, n):
    """Сторона, чей ход, ставит мат не позже чем за n ходов."""
    key = (b._transposition_key(), n)
    if key in TT:
        return TT[key]
    res = False
    moves = list(b.legal_moves)
    if n == 1:
        for m in moves:
            if b.gives_check(m):
                b.push(m)
                if b.is_checkmate():
                    res = True
                b.pop()
                if res:
                    break
    else:
        moves.sort(key=lambda m: (not b.gives_check(m), not b.is_capture(m)))
        for m in moves:
            b.push(m)
            if b.is_checkmate():
                ok = True
            elif b.is_game_over():
                ok = False
            else:
                ok = all_replies_lose(b, n - 1)
            b.pop()
            if ok:
                res = True
                break
    TT[key] = res
    return res


def all_replies_lose(b, n):
    for r in list(b.legal_moves):
        b.push(r)
        ok = mates_in(b, n)
        b.pop()
        if not ok:
            return False
    return True


def mate_moves(b, n):
    """Все ходы, после которых мат форсирован не позже чем за n ходов."""
    out = []
    for m in list(b.legal_moves):
        b.push(m)
        if b.is_checkmate():
            ok = True
        elif b.is_game_over() or n == 1:
            ok = False
        else:
            ok = all_replies_lose(b, n - 1)
        b.pop()
        if ok:
            out.append(m)
    return out


def longest_defence(b, n):
    """Защита, которая дольше всех тянет до мата (n — сколько ходов у атакующего осталось)."""
    best, best_len = None, -1
    for r in list(b.legal_moves):
        b.push(r)
        k = next(k for k in range(1, n + 1) if mates_in(b, k))
        b.pop()
        # при равной длине — взятие или ход королём выглядит естественнее; иначе первый
        score = (k, b.is_capture(r))
        if best is None or score > best_len:
            best, best_len = r, score
    return best


def solve_mate(b, n):
    line = []
    for k in range(n, 0, -1):
        mm = mate_moves(b, k)
        if k == 1:
            if not mm:
                return None, "нет мата в 1 в конце линии"
            pick = next((m for m in mm if line and m.uci() == getattr(b, "_want", "")), mm[0])
            line.append(pick)
            b.push(pick)
            return line, ("" if len(mm) == 1 else f"матов в 1: {len(mm)} (это ок)")
        if len(mm) != 1:
            return None, f"на ходу {n - k + 1} подходящих ходов: {[b.san(m) for m in mm]}"
        line.append(mm[0])
        b.push(mm[0])
        d = longest_defence(b, k - 1)
        line.append(d)
        b.push(d)
    return line, ""


# ---------------------------------------------------------------- тактика
def qsearch(b, alpha, beta, depth=0):
    if b.is_checkmate():
        return -MATE
    stand = material(b)
    if depth > 6:
        return stand
    if stand >= beta:
        return stand
    alpha = max(alpha, stand)
    caps = [m for m in b.legal_moves if b.is_capture(m) or m.promotion]
    caps.sort(key=lambda m: -(VAL[b.piece_type_at(m.to_square)] if b.piece_type_at(m.to_square) else 0))
    for m in caps:
        b.push(m)
        s = -qsearch(b, -beta, -alpha, depth + 1)
        b.pop()
        if s >= beta:
            return s
        alpha = max(alpha, s)
    return alpha


def search(b, depth, alpha=-10 * MATE, beta=10 * MATE, ply=0):
    if b.is_checkmate():
        return -MATE + ply
    if b.is_stalemate() or b.is_insufficient_material():
        return 0
    if depth <= 0:
        return qsearch(b, alpha, beta)
    moves = list(b.legal_moves)
    moves.sort(key=lambda m: (not b.gives_check(m), not b.is_capture(m), not m.promotion))
    best = -10 * MATE
    for m in moves:
        b.push(m)
        s = -search(b, depth - 1, -beta, -alpha, ply + 1)
        b.pop()
        if s > best:
            best = s
        alpha = max(alpha, s)
        if alpha >= beta:
            break
    return best


def scored(b, depth):
    out = []
    for m in list(b.legal_moves):
        b.push(m)
        out.append((-search(b, depth - 1), m))
        b.pop()
    out.sort(key=lambda x: -x[0])
    return out


def solve_tactic(b, n, first=None):
    line, notes = [], []
    for k in range(n, 0, -1):
        depth = 2 * k + 1
        sc = scored(b, depth)
        best_s, best_m = sc[0]
        if first and k == n and best_m.uci() != first:
            return None, f"лучший ход не {first}, а {b.san(best_m)} ({best_s}); топ: " + \
                ", ".join(f"{b.san(m)}={s}" for s, m in sc[:4])
        second = sc[1][0] if len(sc) > 1 else -10 * MATE
        if best_s - second < 150 and not (best_s > MATE // 2 and second < MATE // 2):
            if k == n:
                return None, f"на ходу 1 не единственный: " + ", ".join(f"{b.san(m)}={s}" for s, m in sc[:4])
            # дальше — из равных выбираем самое естественное: взятие самой дорогой фигуры
            tied = [m for s, m in sc if s >= best_s - 50]
            caps = [m for m in tied if b.is_capture(m)]
            if caps:
                best_m = max(caps, key=lambda m: VAL.get(b.piece_type_at(m.to_square) or 1, 100))
            notes.append(f"ход {n - k + 1}: равные {[b.san(m) for m in tied]}")
        line.append(best_m)
        b.push(best_m)
        if b.is_game_over():
            break
        if k > 1:
            rep = scored(b, 2 * (k - 1))
            line.append(rep[0][1])
            b.push(rep[0][1])
    return line, "; ".join(notes)


# ---------------------------------------------------------------- запуск
def start_board(p):
    if "moves" in p:
        b = chess.Board()
        for s in p["moves"].split():
            if s[0].isdigit():
                continue
            b.push_san(s)
        return b
    return chess.Board(p["fen"])


def main():
    out, bad = [], 0
    for i, p in enumerate(SRC, 1):
        TT.clear()
        t0 = time.time()
        try:
            b = start_board(p)
        except Exception as e:  # noqa: BLE001
            print(f"#{i} {p['title']}: ПОЗИЦИЯ НЕВЕРНА {e}")
            bad += 1
            continue
        fen = b.fen()
        if not b.is_valid():
            print(f"#{i} {p['title']}: невалидная позиция {b.status()}")
            bad += 1
            continue
        n = p["n"]
        if p["kind"] == "mate":
            if n > 1 and mates_in(b.copy(), n - 1):
                print(f"#{i} {p['title']}: есть мат быстрее чем в {n}")
                bad += 1
                continue
            line, note = solve_mate(b.copy(), n)
        else:
            first = None
            if p.get("first"):
                first = b.parse_san(p["first"]).uci()
            line, note = solve_tactic(b.copy(), n, first)
        dt = time.time() - t0
        if not line:
            print(f"#{i} {p['title']}: ✗ {note}  ({dt:.1f}s)")
            bad += 1
            continue
        if p.get("first") and p["kind"] == "mate" and line[0] != b.parse_san(p["first"]):
            print(f"#{i} {p['title']}: ✗ решение {b.san(line[0])}, а ожидал {p['first']}")
            bad += 1
            continue
        bb = b.copy()
        sans = []
        for m in line:
            sans.append(bb.san(m))
            bb.push(m)
        res = "мат" if bb.is_checkmate() else f"материал {material(bb) * (1 if len(line) % 2 == 0 else -1) - material(b):+d}"
        print(f"#{i} {p['title']}: ✓ {' '.join(sans)}  [{res}] {note} ({dt:.1f}s)")
        out.append({**{k: v for k, v in p.items() if k not in ('moves', 'first')}, "fen": fen,
                    "sol": [m.uci() for m in line], "san": sans})
    json.dump(out, open(MOD + ".out.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nГотово: {len(out)}, с ошибками: {bad}")


if __name__ == "__main__":
    main()
