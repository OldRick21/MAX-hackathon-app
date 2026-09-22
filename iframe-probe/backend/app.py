"""Минимальная заглушка API платформы."""

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__, static_folder=None, template_folder="clients")
# Backend доступен только за одним доверенным прокси Nginx.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "backend", "mode": "placeholder"}


# Диагностические клиенты: это не реестр сервисов и не авторизация.
DEMO_SERVICES = {"schedule": "Расписание", "profile": "Профиль"}


@app.get("/api/demo/services")
def demo_services():
    return {"services": [
        {"id": key, "title": title, "url": f"/api/demo/clients/{key}"}
        for key, title in DEMO_SERVICES.items()
    ]}


@app.get("/api/demo/clients/<service_id>")
def demo_client(service_id):
    from flask import abort, render_template, request

    if service_id not in DEMO_SERVICES:
        abort(404)
    return render_template(
        "demo.html", title=DEMO_SERVICES[service_id],
        core_origin=request.host_url.rstrip("/"),
    )
