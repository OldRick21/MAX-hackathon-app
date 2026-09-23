'''
Роутер для бекенда сервисов.
Здесь сервис может получить публичный токен для проверки JWT,
вносить информацию о профилях и ролях и получать эту информацию.
'''

from fastapi import APIRouter
from pydantic import BaseModel

internal_router = APIRouter(prefix="/internal", tags=["internal"])

'''
Возвращает публичный ключ для проверки бекендом сервиса подписей.
'''
@internal_router.get("/auth/public-key")
def get_public_key():
	pass

