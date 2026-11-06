"""Утилита администратора клуба (запускать в консоли рядом с app.py).

    python manage.py create-admin НИК [--must-change]   — создать администратора или повысить существующий аккаунт.
                                                           Пароль спрашивается (не виден при вводе) или берётся
                                                           из переменной окружения ADMIN_PASSWORD.
                                                           --must-change: при первом входе попросить сменить пароль.
    python manage.py set-role НИК owner|admin|coach|user  — назначить роль (owner — владелец, может всё)
    python manage.py users                                — список участников (ник, роль, рейтинг, партий)
    python manage.py reset-password НИК [--must-change]   — задать новый пароль (спросит его; или ADMIN_PASSWORD)
    python manage.py unlock НИК                           — снять блокировку (и временную — после неудачных входов)
    python manage.py delete НИК                           — удалить участника со всеми данными
    python manage.py migrate                              — обновить схему базы (делается и само при запуске сайта)
    python manage.py backup                               — сделать копию базы в instance/backup-ДАТА.db
    python manage.py check                                — проверить базу на целостность

Пароли не передаются в командной строке (они бы остались в истории консоли).
"""
import getpass
import os
import sqlite3
import sys
import time

from werkzeug.security import generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("LADYA_DB") or os.path.join(BASE, "instance", "chess.db")
sys.path.insert(0, BASE)

from ladya import db as dbm  # noqa: E402
from ladya.security import password_problem  # noqa: E402

ROLES = ("owner", "admin", "coach", "user")


def find(c, nick):
    rows = c.execute("SELECT * FROM users").fetchall()
    return next((r for r in rows if r["nick"].casefold() == nick.casefold()), None)


def ask_password(nick):
    pw = os.environ.get("ADMIN_PASSWORD")
    if pw:
        print("Пароль взят из переменной окружения ADMIN_PASSWORD.")
    else:
        if not sys.stdin.isatty():
            print("Нет терминала для ввода пароля — задай переменную окружения ADMIN_PASSWORD.")
            return None
        pw = getpass.getpass(f"Новый пароль для «{nick}» (не отображается): ")
        if getpass.getpass("Повтори пароль: ") != pw:
            print("Пароли не совпадают.")
            return None
    prob = password_problem(pw, nick)
    if prob:
        print(prob)
        return None
    return pw


def set_password(c, uid, pw, must_change):
    c.execute("UPDATE users SET pw_hash=?, must_change_pw=?, sess_ver=sess_ver+1, failed_logins=0, locked_until=0 "
              "WHERE id=?", (generate_password_hash(pw, method="scrypt"), 1 if must_change else 0, uid))


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = {a for a in argv[1:] if a.startswith("--")}
    cmd = args[0] if args else ""
    if cmd not in ("create-admin", "set-role", "users", "reset-password", "unlock", "delete", "migrate", "backup",
                   "check"):
        print(__doc__)
        return 1
    if cmd == "backup":
        if not os.path.exists(DB):
            print("База ещё не создана.")
            return 1
        dst = os.path.join(os.path.dirname(DB), time.strftime("backup-%Y-%m-%d_%H-%M-%S.db"))
        src = sqlite3.connect(DB)
        with sqlite3.connect(dst) as b:
            src.backup(b)       # «горячая» копия: безопасно даже пока сайт работает
        src.close()
        ok = sqlite3.connect(dst).execute("PRAGMA integrity_check").fetchone()[0]
        print(f"Копия сохранена: {dst} ({os.path.getsize(dst) // 1024} КБ), проверка: {ok}")
        return 0 if ok == "ok" else 1
    applied = dbm.migrate(DB)          # перед любой командой база приводится к последней версии
    if cmd == "migrate":
        print("База уже последней версии." if not applied else f"Применено миграций: {len(applied)}")
        return 0
    c = dbm.connect(DB)
    if cmd == "check":
        ok = c.execute("PRAGMA integrity_check").fetchone()[0]
        ver = dbm.current_version(c)
        print(f"Целостность: {ok}; версия схемы: {ver} (последняя {dbm.LATEST})")
        return 0 if ok == "ok" else 1
    if cmd == "users":
        rows = c.execute("""SELECT nick, role, blocked, rating, (SELECT COUNT(*) FROM games g WHERE g.user_id=u.id) n,
                            created FROM users u ORDER BY role='owner' DESC, role='admin' DESC, role='coach' DESC, rating DESC""").fetchall()
        print(f"{'Ник':22} {'Роль':6} {'Рейтинг':>8} {'Партий':>7}  В клубе с")
        for r in rows:
            print(f"{r['nick']:22} {r['role']:6} {r['rating']:>8} {r['n']:>7}  {r['created'][:10]}"
                  f"{'  [заблокирован]' if r['blocked'] else ''}")
        print(f"Всего: {len(rows)}")
        return 0
    if len(args) < 2:
        print(__doc__)
        return 1
    nick = args[1]
    u = find(c, nick)
    must = "--must-change" in flags
    if cmd == "create-admin":
        if u:
            if input(f"Аккаунт «{u['nick']}» уже есть. Сделать его администратором и задать новый пароль? "
                     f"Напиши ДА (или Enter — только повысить): ").strip().upper() == "ДА":
                pw = ask_password(u["nick"])
                if not pw:
                    return 1
                set_password(c, u["id"], pw, must)
            c.execute("UPDATE users SET role=CASE WHEN role='owner' THEN 'owner' ELSE 'admin' END, blocked=0 WHERE id=?",
                      (u["id"],))
            print(f"«{u['nick']}» теперь администратор.")
            return 0
        from ladya.accounts import NICK_RE
        if not NICK_RE.match(nick):
            print("Ник: 3–20 символов — буквы, цифры и _.")
            return 1
        pw = ask_password(nick)
        if not pw:
            return 1
        c.execute("INSERT INTO users(nick,birth,pw_hash,role,must_change_pw) VALUES(?,'',?,'admin',?)",
                  (nick, generate_password_hash(pw, method="scrypt"), 1 if must else 0))
        print(f"Администратор «{nick}» создан." + (" При первом входе он сменит пароль." if must else ""))
        return 0
    if not u:
        print("Нет такого ника.")
        return 1
    if cmd == "set-role":
        role = args[2] if len(args) > 2 else ""
        if role not in ROLES:
            print("Роль: admin, coach или user.")
            return 1
        c.execute("UPDATE users SET role=? WHERE id=?", (role, u["id"]))
        print(f"«{u['nick']}»: роль {role}.")
    elif cmd == "reset-password":
        if len(args) > 2:
            print("Пароль в командной строке больше не принимается (он остаётся в истории консоли).")
            print("Запусти без пароля — он будет спрошен — или задай ADMIN_PASSWORD.")
            return 1
        pw = ask_password(u["nick"])
        if not pw:
            return 1
        set_password(c, u["id"], pw, must)
        print(f"Пароль для «{u['nick']}» изменён. Все его сессии завершены.")
    elif cmd == "unlock":
        c.execute("UPDATE users SET blocked=0, failed_logins=0, locked_until=0 WHERE id=?", (u["id"],))
        print("Блокировка снята.")
    elif cmd == "delete":
        if input(f"Удалить «{u['nick']}» со всеми партиями? Напиши ДА: ").strip().upper() != "ДА":
            print("Отменено.")
            return 0
        from ladya.accounts import delete_user
        c.execute("BEGIN IMMEDIATE")
        delete_user(c, u["id"])
        c.execute("COMMIT")
        print("Удалено.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
