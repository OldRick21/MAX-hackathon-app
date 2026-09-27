import uuid
import jwt
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from database.create_tables import create_tables
from routes.auth import router_auth


@asynccontextmanager
async def lifespan(app: FastAPI):
    from settings.config import settings
    if not settings.ALLOW_DEV_LOGIN:
        if not settings.MAX_BOT_TOKEN.strip() or len(settings.JWT_SECRET_KEY) < 32:
            raise RuntimeError("Set MAX_BOT_TOKEN and a random JWT_SECRET_KEY of at least 32 characters")
    create_tables()
    yield

app = FastAPI(lifespan=lifespan)

# --- Middleware сквозного X-Request-ID и Cache-Control ---
class RequestIdAndCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
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

# --- Обработчики ошибок по схеме ErrorResponse ---
STATUS_TO_CODE = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "RESOURCE_NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR"
}

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
        headers={"X-Request-ID": req_id, "Cache-Control": "no-store"},
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)