'''
Роутер для ядра клиента.
Здесь оно может взаимодействовать с методами, связанными с ВУЗами и сервисами
'''

from fastapi import APIRouter
from pydantic import BaseModel

institution_router = APIRouter(prefix="/institution", tags=["institution"])

'''
Возвращает список институтов, 

TODO: определить формат, в котором возвращается этот список.
'''
@institution_router.get("")
async def get_institutions():
	pass

'''
Возвращает список сервисов, с id и отображаемым именем каждого

TODO: что делать со сменой языка?
TODO: формат?
'''
@institution_router.get("{institution_id}/service")
async def get_services():
	pass

'''
Возвращает данные о сервисе.

TODO: а, собственно, какие?

среди данных - URL сервиса, необходимый для того, чтобы скачать напрямую клиент сервиса
TODO: формат данных?
'''
@institution_router.get("{institution_id}/service/{service_id}")
async def get_service_data():
	pass