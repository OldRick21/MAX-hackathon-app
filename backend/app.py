"""Минимальная заглушка API платформы."""

from flask import Flask

app = Flask(__name__, static_folder=None)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "backend", "mode": "placeholder"}
