import logging
import redis
from settings.config import settings

logger = logging.getLogger("uvicorn")

try:
    # Пробуем подключиться к реальному Redis по адресу из .env
    real_redis = redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        decode_responses=True,
        socket_connect_timeout=1
    )
    real_redis.ping()
    revoked_tokens_redis = real_redis
    logger.info(f"Connected to REAL Redis at {settings.REDIS_HOST}:{settings.REDIS_PORT}")
except Exception:
    # Если локальный Redis не запущен, используем in-memory fakeredis
    import fakeredis
    fake_server = fakeredis.FakeServer()
    revoked_tokens_redis = fakeredis.FakeRedis(
        server=fake_server,
        decode_responses=True
    )
    logger.warning("Real Redis unavailable. Falling back to in-memory FakeRedis.")