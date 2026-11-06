"""Запуск сайта «Ладья» на своём компьютере: python app.py → http://127.0.0.1:5000

Весь код — в пакете ladya/ (фабрика приложения create_app). На хостинге используется wsgi.py.
"""
import os

from ladya import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")), debug=False,
            threaded=True)
