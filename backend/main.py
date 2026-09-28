import uuid
import jwt
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from database.create_tables import create_tables
from auth.routes import router as router_auth
from routes.institutions import router as router_institutions
from routes.service_registry import router as router_service_registry
from routes.system import router as router_system
from routes.private_admin import router_private
from routes.platform import router_platform
from routes.avatars import router as router_avatars
from platform_core.errors import DomainError


@asynccontextmanager
async def lifespan(app: FastAPI):
    from settings.config import settings
    if not settings.ALLOW_DEV_LOGIN:
        if not settings.MAX_BOT_TOKEN.strip() or len(settings.CURSOR_SECRET_KEY) < 32:
            raise RuntimeError("Set MAX_BOT_TOKEN and a random CURSOR_SECRET_KEY of at least 32 characters")
    from auth.security import security
    security.keys.initialize()
    create_tables()
    yield

app = FastAPI(lifespan=lifespan)

# --- Middleware сквозного X-Request-ID и Cache-Control ---
class RequestIdAndCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Request ID генерирует сервер; присланный клиентом не переносится (CORE_API_SPEC.md §3).
        req_id = str(uuid.uuid4())
        request.state.request_id = req_id
        
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        if "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response

app.add_middleware(RequestIdAndCacheMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router_auth)
app.include_router(router_institutions)
app.include_router(router_service_registry)
app.include_router(router_system)
app.include_router(router_private)
app.include_router(router_platform)
app.include_router(router_avatars)

# --- Обработчики ошибок по схеме ErrorResponse ---
STATUS_TO_CODE = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "RESOURCE_NOT_FOUND",
    409: "CONFLICT",
    412: "PRECONDITION_FAILED",
    422: "VALIDATION_ERROR",
    428: "PRECONDITION_REQUIRED",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE"
}


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError):
    req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    error = {"code": exc.code, "message": exc.message[:500], "request_id": req_id}
    if exc.details:
        error["details"] = exc.details[:100]
    return JSONResponse(
        status_code=exc.status,
        headers={"X-Request-ID": req_id, "Cache-Control": "no-store", **(exc.headers or {})},
        content={"error": error},
    )

@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    code = STATUS_TO_CODE.get(exc.status_code, "BAD_REQUEST")
    
    msg = str(exc.detail)
    if ":" in msg:
        parts = msg.split(":", 1)
        code = parts[0].strip()
        msg = parts[1].strip()

    return JSONResponse(
        status_code=exc.status_code,
        headers={"X-Request-ID": req_id, "Cache-Control": "no-store", **(exc.headers or {})},
        content={
            "error": {
                "code": code,
                "message": msg,
                "request_id": req_id
            }
        }
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    if any(err.get("type") == "json_invalid" for err in exc.errors()):
        return JSONResponse(
            status_code=400,
            headers={"X-Request-ID": req_id, "Cache-Control": "no-store"},
            content={"error": {"code": "BAD_REQUEST", "message": "Некорректный JSON", "request_id": req_id}},
        )
    details = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", []))
        details.append({"path": loc, "message": err.get("msg", "Validation error")})

    return JSONResponse(
        status_code=422,
        headers={"X-Request-ID": req_id, "Cache-Control": "no-store"},
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Validation error",
                "request_id": req_id,
                "details": details
            }
        }
    )

@app.exception_handler(jwt.PyJWTError)
async def jwt_exception_handler(request: Request, exc: jwt.PyJWTError):
    req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=401,
        headers={"X-Request-ID": req_id, "Cache-Control": "no-store"},
        content={
            "error": {
                "code": "UNAUTHENTICATED",
                "message": "Invalid or expired token",
                "request_id": req_id
            }
        }
    )

from sqlalchemy.exc import SQLAlchemyError
from auth.keys import KeyStoreUnavailable
from filelock import Timeout as FileLockTimeout


@app.exception_handler(SQLAlchemyError)
@app.exception_handler(KeyStoreUnavailable)
@app.exception_handler(FileLockTimeout)
@app.exception_handler(OSError)
async def storage_unavailable(request: Request, exc):
    return await domain_error_handler(request, DomainError(503, 'SERVICE_UNAVAILABLE', 'Authentication storage unavailable'))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

