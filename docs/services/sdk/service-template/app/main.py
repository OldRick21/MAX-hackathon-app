"""Шаблон своего сервиса вуза на минимальном SDK (app/sdk.py).

Платформенная часть — в sdk.py, её не нужно менять. Здесь заполните:
1. SERVICE_CODE — код, указанный администратором при подключении («Свой сервис»);
2. MANIFEST — название, меню и виджеты главного экрана; ROLES — роли сервиса (права только вида <код>.<право>);
3. хранилище и доменные маршруты. Каждый маршрут получает sdk.Ctx через Depends(authenticate),
   а все данные хранит и ищет по ctx.tenant = (institution_id, service_id).
"""
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI

from app import sdk

SERVICE_CODE = "my-service"  # TODO: код из админки; тип в ядре — custom.<код>

MANIFEST = {
    "titles": {"ru": "Мой сервис", "en": "My service"},  # TODO
    "menus": [
        # entrypoint_path — страница клиента из ENTRYPOINTS; profiles — из выбранных при подключении.
        {"id": "main", "titles": {"ru": "Мой сервис", "en": "My service"}, "entrypoint_path": "/app",
         "profiles": ["student", "teacher", "admin"], "required_permissions": [], "order": 0},
    ],
    # Виджеты главного экрана (docs/services/sdk/WIDGETS_SPEC.md). Оболочка рисует карточку сама по kind
    # (profile, events, list, stat, progress, notice) из данных, которые сервис отдаёт по data_path
    # (путь от api_base_url, т. е. здесь — GET /api/v1/widgets/summary). Включать и выключать виджет
    # по профилям может администратор вуза («Сервисы» → «Виджеты на главной»). Виджет не обязателен.
    "widgets": [
        {"id": "summary", "titles": {"ru": "Мой сервис", "en": "My service"}, "kind": "list", "size": "small",
         "profiles": ["student", "teacher", "admin"], "required_permissions": [], "data_path": "/widgets/summary",
         "open_menu": "main", "order": 0},
    ],
}
ROLES = [
    # TODO: например {"code": "editor", "titles": {"ru": "Редактор"}, "allowed_profiles": ["admin"],
    #                 "permissions": [f"{SERVICE_CODE}.manage"]}
]
ENTRYPOINTS = ["/app"]

settings = sdk.Settings.from_env()
core = sdk.CoreClient(settings)
state = {"onboarding": "pending"}
CLIENT = Path(__file__).resolve().parent.parent / "client"


@asynccontextmanager
async def lifespan(app):
    missing = settings.missing()
    if missing:
        raise RuntimeError(f"Не заданы настройки из админки: {', '.join(missing)}")
    # TODO: подготовить хранилище (таблицы с колонками institution_id, service_id).
    threading.Thread(target=sdk.onboard, args=(core, MANIFEST, ROLES, state), daemon=True).start()
    yield


# Без редиректа «/путь/ → /путь»: за прокси он указывает на внутренний адрес (http, без порта, http://svc).
app = FastAPI(lifespan=lifespan, redirect_slashes=False)
sdk.install(app, core, settings, MANIFEST, f"custom.{SERVICE_CODE}", state, CLIENT, ENTRYPOINTS)
authenticate = app.state.authenticate


@app.get("/api/v1/widgets/summary")
def widget_summary(ctx: sdk.Ctx = Depends(authenticate)):
    """Данные виджета «summary» (шаблон list). Права — как у остальных маршрутов: показывайте только то,
    что пользователь увидел бы в самом сервисе. Нечего показывать — верните Response(status_code=204)."""
    # TODO: замените своими данными, например последними записями пользователя из хранилища.
    items = [{"title": "Сервис подключён", "subtitle": f"Профиль: {ctx.profile}", "badge": "готов", "tone": "accent"}]
    return {"kind": "list", "items": items, "total": len(items), "empty_text": "Пока пусто"}


@app.get("/api/v1/whoami")
def whoami(ctx: sdk.Ctx = Depends(authenticate)):
    """Проверка подключения: кого и в каком вузе увидел сервис. Замените своими маршрутами."""
    return {"institution_id": ctx.institution_id, "service_id": ctx.service_id, "user_id": ctx.user_id,
            "profile": ctx.profile, "permissions": sorted(ctx.permissions)}
