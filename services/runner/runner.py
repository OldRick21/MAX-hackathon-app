"""Раннер облачного сервиса: один контейнер на тип, внутри — отдельный процесс на каждый вуз.

Раннер спрашивает у ядра облачные экземпляры своего типа (токен раннера видит только свой тип),
держит для каждого экземпляра процесс сервиса — тот же код, что работает у вуза локально
(`uvicorn app.main:app`, ключ и адреса из окружения, свой файл БД), — и проксирует запросы
`/<service_id>/…` в процесс этого вуза через unix-сокет.

- Новый экземпляр запускается сразу: сервис публикует меню и роли.
- Остальные запускаются лениво, по первому запросу, и останавливаются после простоя (IDLE_SECONDS).
- Упавший процесс поднимается следующим запросом; смена ключа (revision) перезапускает процесс.
- Удалённый экземпляр останавливается; его данные остаются в /data/<service_id>.
- Экземпляры удалённого вуза (список purge от ядра) останавливаются, их данные стираются.

Переменные: CORE_INTERNAL_URL, PROVISIONING_TOKEN, SHELL_ORIGIN, DATA_DIR=/data, DB_ENV/DB_FILE
(куда процессу класть свою БД), APP_MODULE=app.main:app, IDLE_SECONDS=900, POLL_SECONDS=15,
PASS_ENV (через пробел — какие переменные раннера передать процессам, например FRAME_ANCESTORS).
"""
import asyncio
import hashlib
import logging
import os
import re
import shutil
import signal
import sys
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

log = logging.getLogger("runner")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

CORE = os.environ.get("CORE_INTERNAL_URL", "http://backend:8000").rstrip("/")
TOKEN = os.environ.get("PROVISIONING_TOKEN", "")
SHELL_ORIGIN = os.environ.get("SHELL_ORIGIN", "").rstrip("/")
DATA = Path(os.environ.get("DATA_DIR", "/data"))
RUN = Path(os.environ.get("RUN_DIR", "/tmp/runner"))
DB_ENV = os.environ.get("DB_ENV", "")
DB_FILE = os.environ.get("DB_FILE", "service.db")
APP_MODULE = os.environ.get("APP_MODULE", "app.main:app")
IDLE_SECONDS = float(os.environ.get("IDLE_SECONDS", "900"))
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "15"))
START_TIMEOUT = float(os.environ.get("START_TIMEOUT", "30"))
PASS_ENV = os.environ.get("PASS_ENV", "").split()
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length"}


@dataclass
class Instance:
    service_id: str
    binding: dict
    proc: Optional[asyncio.subprocess.Process] = None
    last_used: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def sock(self) -> Path:
        path = RUN / f"{self.service_id}.sock"
        # Путь unix-сокета ограничен ~108 байтами: при длинном RUN_DIR — короткое имя в /tmp.
        if len(str(path).encode()) > 100:
            path = Path("/tmp") / f"r-{hashlib.sha1(self.service_id.encode()).hexdigest()[:16]}.sock"
        return path

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def env(self) -> dict:
        b = self.binding
        data_dir = DATA / self.service_id
        env = {k: v for k, v in os.environ.items()
               if k in ("PATH", "HOME", "LANG", "LC_ALL", "PYTHONPATH", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE")}
        env.update({k: os.environ[k] for k in PASS_ENV if k in os.environ})
        env.update({"CORE_URL": CORE, "SHELL_ORIGIN": SHELL_ORIGIN, "SERVICE_CLIENT_ID": b["client_id"],
                    "SERVICE_CLIENT_SECRET": b["client_secret"], "SERVICE_API_BASE_URL": b["api_base_url"],
                    "SERVICE_CLIENT_BASE_URL": b["client_base_url"], "SERVICE_DATA": str(data_dir)})
        if DB_ENV:
            env[DB_ENV] = str(data_dir / DB_FILE)
        return env


instances: Dict[str, Instance] = {}
# Смена ключа или адресов требует перезапуска процесса вуза; включение/выключение — нет (его проверяет ядро).
RESTART_ON = ("revision", "client_id", "client_secret", "api_base_url", "client_base_url")


async def start(inst: Instance) -> None:
    """Запускает процесс вуза (если не запущен) и ждёт, пока он ответит на /api/v1/health."""
    async with inst.lock:
        if inst.running:
            return
        (DATA / inst.service_id).mkdir(parents=True, exist_ok=True)
        with suppress(FileNotFoundError):
            inst.sock.unlink()
        inst.proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "uvicorn", APP_MODULE, "--uds", str(inst.sock), "--workers", "1",
            "--no-access-log", "--proxy-headers", env=inst.env())
        log.info("started %s (pid %s)", inst.service_id, inst.proc.pid)
        deadline = time.monotonic() + START_TIMEOUT
        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=str(inst.sock)), timeout=2) as client:
            while time.monotonic() < deadline:
                if inst.proc.returncode is not None:
                    raise RuntimeError(f"process of {inst.service_id} exited with {inst.proc.returncode}")
                with suppress(httpx.HTTPError):
                    if (await client.get("http://svc/api/v1/health")).status_code == 200:
                        return
                await asyncio.sleep(0.2)
        await stop(inst, locked=True)
        raise RuntimeError(f"process of {inst.service_id} did not start in {START_TIMEOUT}s")


async def stop(inst: Instance, locked: bool = False) -> None:
    async def _stop():
        proc, inst.proc = inst.proc, None
        if proc and proc.returncode is None:
            proc.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            log.info("stopped %s", inst.service_id)
        with suppress(FileNotFoundError):
            inst.sock.unlink()
    if locked:
        await _stop()
    else:
        async with inst.lock:
            await _stop()


async def sync_instances(client: httpx.AsyncClient) -> None:
    """Сверяет процессы со списком ядра: новые — запустить, сменившие ключ — перезапустить, удалённые — остановить."""
    response = await client.get(f"{CORE}/api/v1/internal/provisioning/instances",
                                headers={"Authorization": f"Bearer {TOKEN}"})
    response.raise_for_status()
    data = response.json()
    seen = set()
    for b in data["items"]:
        sid = b["service_id"]
        seen.add(sid)
        inst = instances.get(sid)
        if inst is None:
            inst = instances[sid] = Instance(sid, b)
            log.info("new instance %s (institution %s)", sid, b["institution_id"])
            asyncio.create_task(safe_start(inst))  # сразу: сервис публикует меню и роли
        else:
            changed = any(inst.binding.get(k) != b.get(k) for k in RESTART_ON)
            inst.binding = b
            if changed and inst.running:
                await stop(inst)
                asyncio.create_task(safe_start(inst))
    for sid in list(instances):
        if sid not in seen:
            await stop(instances.pop(sid))
            log.info("instance %s removed", sid)
    for sid in data.get("purge") or []:
        if isinstance(sid, str) and UUID_RE.match(sid) and sid not in seen:
            purge(sid)


def purge(service_id: str) -> None:
    """Стирает данные экземпляра удалённого вуза. Повтор безопасен: каталога уже нет."""
    path = DATA / service_id
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        log.info("instance %s purged", service_id)


async def safe_start(inst: Instance) -> None:
    try:
        await start(inst)
    except Exception as error:  # noqa: BLE001 — лог и повтор при следующем запросе
        log.warning("start %s failed: %s", inst.service_id, error)


async def poll_loop() -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            try:
                await sync_instances(client)
            except Exception as error:  # noqa: BLE001 — ядро недоступно: повторим позже
                log.warning("sync failed: %s", error)
            await asyncio.sleep(POLL_SECONDS)


async def reap_loop() -> None:
    """Останавливает процессы вузов, к которым долго не обращались."""
    while True:
        await asyncio.sleep(min(30.0, IDLE_SECONDS))
        now = time.monotonic()
        for inst in list(instances.values()):
            if inst.running and now - inst.last_used > IDLE_SECONDS:
                await stop(inst)


async def proxy(request: Request) -> Response:
    sid = request.path_params["service_id"]
    inst = instances.get(sid) if UUID_RE.match(sid) else None
    if inst is None:
        return JSONResponse({"error": {"code": "RESOURCE_NOT_FOUND", "message": "Сервис не найден", "request_id": ""}},
                            status_code=404)
    inst.last_used = time.monotonic()
    try:
        await start(inst)
    except Exception as error:  # noqa: BLE001
        log.warning("start %s failed: %s", sid, error)
        return JSONResponse({"error": {"code": "SERVICE_UNAVAILABLE", "message": "Сервис временно недоступен",
                                       "request_id": ""}}, status_code=503)
    path = "/" + request.path_params.get("rest", "")
    headers = [(k, v) for k, v in request.headers.items() if k.lower() not in HOP]
    headers.append(("x-forwarded-proto", request.headers.get("x-forwarded-proto", request.url.scheme)))
    body = await request.body()
    async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=str(inst.sock)), timeout=60) as client:
        try:
            upstream = await client.request(request.method, f"http://svc{path}", params=request.query_params,
                                            headers=headers, content=body)
        except httpx.HTTPError as error:
            log.warning("proxy %s %s failed: %s", sid, path, error)
            return JSONResponse({"error": {"code": "SERVICE_UNAVAILABLE", "message": "Сервис временно недоступен",
                                           "request_id": ""}}, status_code=503)
    inst.last_used = time.monotonic()
    out = [(k, v) for k, v in upstream.headers.multi_items() if k.lower() not in HOP and k.lower() != "content-encoding"]
    response = Response(upstream.content, status_code=upstream.status_code)
    response.raw_headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in out] + \
        [(b"content-length", str(len(upstream.content)).encode())]
    return response


async def health(request: Request) -> JSONResponse:
    """Раннер жив. С ?service_id= — состояние экземпляра вуза без запуска процесса: 404, если раннер
    его не знает; running=false — процесс остановлен по простою и поднимется первым запросом."""
    sid = request.query_params.get("service_id")
    if sid is not None:
        inst = instances.get(sid)
        if inst is None:
            return JSONResponse({"status": "unknown"}, status_code=404)
        return JSONResponse({"status": "ok", "running": inst.running})
    return JSONResponse({"status": "ok", "instances": len(instances),
                         "running": sum(1 for i in instances.values() if i.running)})


@asynccontextmanager
async def lifespan(app):
    if len(TOKEN) < 32:
        raise RuntimeError("Set PROVISIONING_TOKEN (32+ characters)")
    RUN.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    tasks = [asyncio.create_task(poll_loop()), asyncio.create_task(reap_loop())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*(stop(i) for i in instances.values()), return_exceptions=True)


METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
app = Starlette(lifespan=lifespan, routes=[
    Route("/health", health),
    Route("/{service_id}", proxy, methods=METHODS),
    Route("/{service_id}/{rest:path}", proxy, methods=METHODS),
])
