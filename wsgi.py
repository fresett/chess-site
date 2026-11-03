# Точка входа для хостинга (PythonAnywhere и любой WSGI-сервер).
# Включает боевой режим: защищённые cookies (только HTTPS) и учёт настоящего IP за прокси хостинга.
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

os.environ.setdefault("PRODUCTION", "1")
os.environ.setdefault("BEHIND_PROXY", "1")

from app import app as application  # noqa: E402
