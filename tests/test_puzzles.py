"""Все задачи корректны: позиция валидна, линия решения состоит из законных ходов,
матовая задача заканчивается матом ровно за n ходов, у каждой задачи есть тема, идея и подсказка."""
import chess

from ladya.puzzles import PUZZLES, THEMES


def test_fifty_puzzles_with_unique_ids():
    assert len(PUZZLES) == 50
    assert [p["id"] for p in PUZZLES] == list(range(1, 51))
    assert len({p["title"] for p in PUZZLES}) == 50


def test_every_puzzle_line_is_legal():
    for p in PUZZLES:
        b = chess.Board(p["fen"])
        assert b.is_valid(), p["title"]
        assert p["theme"] in THEMES and p["idea"] and p["hint"] and p["goal"], p["title"]
        assert len(p["sol"]) % 2 == 1, p["title"]          # линия кончается ходом игрока
        for u in p["sol"]:
            m = chess.Move.from_uci(u)
            assert m in b.legal_moves, (p["title"], u)
            b.push(m)
        if p["kind"] == "mate":
            assert b.is_checkmate(), p["title"]
            assert len(p["sol"]) == 2 * p["n"] - 1, p["title"]


def test_api_gives_full_puzzle(browser):
    d = [p for p in browser().get("/api/puzzles").get_json() if not p.get("weekly")]
    assert len(d) == 50
    assert {"sol", "goal", "idea", "hint", "themeName", "icon", "group", "line"} <= set(d[0])
