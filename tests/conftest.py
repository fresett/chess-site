"""Общие заготовки для тестов: приложение на временной базе и «браузеры» (тестовые клиенты Flask)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ladya import create_app  # noqa: E402
from ladya.db import connect  # noqa: E402

PW = "Lad'ya-test-2024"


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": str(tmp_path / "t.db"),
                      "RATE_LOGIN_IP_PER_MIN": 1000, "RATE_REGISTER_IP_PER_HOUR": 1000})
    return app


class Browser:
    """Тестовый клиент со своими cookie — как отдельный браузер."""

    def __init__(self, app):
        self.app = app
        self.c = app.test_client()
        self.c.get("/")                      # получить сессию и CSRF-токен

    @property
    def csrf(self):
        with self.c.session_transaction() as s:
            return s.get("csrf", "")

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, data=None, **kw):
        data = dict(data or {})
        data.setdefault("csrf", self.csrf)
        return self.c.post(url, data=data, **kw)

    def api(self, url, body=None):
        return self.c.post(url, json=body or {}, headers={"X-CSRF-Token": self.csrf})

    def register(self, nick, pw=PW):
        r = self.post("/register", {"nick": nick, "password": pw, "password2": pw})
        assert r.status_code == 302, r.data[:500]
        return r

    def login(self, nick, pw=PW):
        return self.post("/login", {"nick": nick, "password": pw})


@pytest.fixture
def browser(app):
    return lambda: Browser(app)


def sql(app, q, args=()):
    c = connect(app.config["DATABASE"])
    try:
        cur = c.execute(q, args)
        return cur.fetchall()
    finally:
        c.close()


@pytest.fixture
def db_exec(app):
    return lambda q, args=(): sql(app, q, args)
