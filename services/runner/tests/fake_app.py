"""Процесс «сервиса» для теста раннера: отдаёт своё окружение."""
import os

from fastapi import FastAPI, Request

app = FastAPI()


@app.get('/api/v1/health')
def health():
    return {'status': 'ok'}


@app.api_route('/echo/{rest:path}', methods=['GET', 'POST'])
async def echo(rest: str, request: Request):
    return {'client_id': os.environ['SERVICE_CLIENT_ID'], 'api': os.environ['SERVICE_API_BASE_URL'],
            'db': os.environ.get('TEST_DB'), 'path': rest, 'query': dict(request.query_params),
            'body': (await request.body()).decode(), 'pid': os.getpid()}
