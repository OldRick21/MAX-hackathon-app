from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from database.create_tables import get_db
from database.tables import ServiceInstance
from settings.config import settings

router = APIRouter()

def _origin(url: str) -> str:
    parts = urlsplit(url or "")
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme == "https" and parts.netloc else ""


def shell_csp(db: Session) -> str:
    """CSP HTML-оболочки: iframe только с зарегистрированных client origins включённых сервисов
    и origin облачного администрирования (CORE_API_SPEC.md §6)."""
    frames = {_origin(settings.ADMINISTRATION_PUBLIC_ORIGIN)}
    for (url,) in db.query(ServiceInstance.client_base_url).filter(ServiceInstance.enabled == True,  # noqa: E712
                                                                    ServiceInstance.deleted_at.is_(None)):
        frames.add(_origin(url))
    frames.discard("")
    frame_src = " ".join(["'self'", *sorted(frames)])
    return ("default-src 'self'; script-src 'self' https://st.max.ru; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https:; font-src 'self' data:; connect-src 'self'; "
            f"frame-src {frame_src}; object-src 'none'; base-uri 'self'; form-action 'self'")


@router.get("/api/v1/internal/shell-csp", include_in_schema=False)
def get_shell_csp(db: Session = Depends(get_db)):
    """Заголовок CSP для оболочки: nginx берёт его подзапросом (auth_request) при отдаче index.html.
    Снаружи /api/v1/internal/* закрыт, поэтому endpoint доступен только nginx."""
    return Response(status_code=204, headers={"Content-Security-Policy": shell_csp(db)})


@router.get("/api/v1/health", tags=["Health"])
def get_health():
    """Проверить живость процесса ядра."""
    return {"status": "ok"}

