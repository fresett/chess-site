"""Утилита администратора клуба.

    python manage.py users                      — список участников (ник, рейтинг, партий)
    python manage.py reset-password НИК ПАРОЛЬ  — задать новый пароль (если ученик забыл свой)
    python manage.py delete НИК                 — удалить участника со всеми данными
    python manage.py backup                     — сделать копию базы в instance/backup-ДАТА.db
"""
import os
import sqlite3
import sys
import time

from werkzeug.security import generate_password_hash

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "chess.db")


def find(c, nick):
    rows = c.execute("SELECT id, nick FROM users").fetchall()
    return next((r for r in rows if r[1].casefold() == nick.casefold()), None)


def main(argv):
    if not os.path.exists(DB):
        print("База ещё не создана — сначала запусти сайт хотя бы один раз.")
        return 1
    c = sqlite3.connect(DB)
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "users":
        rows = c.execute("""SELECT nick, rating, (SELECT COUNT(*) FROM games g WHERE g.user_id=u.id), created
                            FROM users u ORDER BY rating DESC""").fetchall()
        print(f"{'Ник':22} {'Рейтинг':>8} {'Партий':>7}  В клубе с")
        for nick, rating, games, created in rows:
            print(f"{nick:22} {rating:>8} {games:>7}  {created[:10]}")
        print(f"Всего: {len(rows)}")
    elif cmd == "reset-password" and len(argv) == 4:
        u = find(c, argv[2])
        if not u:
            print("Нет такого ника."); return 1
        if len(argv[3]) < 6:
            print("Пароль должен быть не короче 6 символов."); return 1
        c.execute("UPDATE users SET pw_hash=? WHERE id=?", (generate_password_hash(argv[3]), u[0])); c.commit()
        print(f"Пароль для «{u[1]}» изменён.")
    elif cmd == "delete" and len(argv) == 3:
        u = find(c, argv[2])
        if not u:
            print("Нет такого ника."); return 1
        if input(f"Удалить «{u[1]}» со всеми партиями? Напиши ДА: ").strip().upper() != "ДА":
            print("Отменено."); return 0
        for t in ("games", "solved", "avatar_img"):
            c.execute(f"DELETE FROM {t} WHERE user_id=?", (u[0],))
        c.execute("DELETE FROM users WHERE id=?", (u[0],)); c.commit()
        print("Удалено.")
    elif cmd == "backup":
        dst = os.path.join(os.path.dirname(DB), time.strftime("backup-%Y-%m-%d_%H-%M.db"))
        with sqlite3.connect(dst) as b:
            c.backup(b)
        print("Копия сохранена:", dst)
    else:
        print(__doc__); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
