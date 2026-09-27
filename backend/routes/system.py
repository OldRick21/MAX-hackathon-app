from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

@router.get("/institution", response_class=HTMLResponse, tags=["Shell"])
def get_institution_shell():
    """Отдает HTML-оболочку выбора ВУЗа (Shell)."""
    return HTMLResponse(content="""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <title>Вузы России — Выбор ВУЗа</title>
        <style>body { font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #f4f6f8; }</style>
    </head>
    <body>
        <div id="app">Загрузка платформы Вузы России...</div>
        <script>
            // Фронтенд Shell: считывает WebApp.initData и выполняет JSON POST /api/v1/auth/token
            console.log("Shell initialized");
        </script>
    </body>
    </html>
    """)


@router.get("/institution/{institution_id}", response_class=HTMLResponse, tags=["Shell"])
def get_institution_workspace_shell(institution_id: str):
    """Отдает HTML-оболочку рабочего пространства ВУЗа."""
    return HTMLResponse(content=f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <title>Рабочее пространство ВУЗа</title>
        <style>body {{ font-family: sans-serif; margin: 0; background: #fff; }}</style>
    </head>
    <body>
        <div id="workspace">Рабочее пространство ВУЗа: {institution_id}</div>
        <script>
            // Загрузка динамических микрофронтендов сервисов
        </script>
    </body>
    </html>
    """)


@router.get("/api/v1/health", tags=["Health"])
def get_health():
    """Проверить живость процесса ядра."""
    return {"status": "ok"}

