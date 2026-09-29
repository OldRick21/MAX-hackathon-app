"""Раннер против заглушки ядра и тестового процесса: запуск на вуз, маршрутизация, простой, удаление.

    cd services/runner && python -m unittest tests.test_runner -v
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_tmp = tempfile.TemporaryDirectory()
TOKEN = 't' * 40
A, B = '11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'
INSTANCES = {}
PURGE = []


class Core(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        ok = self.path == '/api/v1/internal/provisioning/instances' and self.headers.get('Authorization') == f'Bearer {TOKEN}'
        body = json.dumps({'items': list(INSTANCES.values()), 'purge': PURGE} if ok else {}).encode()
        self.send_response(200 if ok else 404)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def binding(sid, secret='s1', revision=1):
    return {'service_id': sid, 'institution_id': 'inst-' + sid[:4], 'service_type': 'schedule', 'enabled': True,
            'api_base_url': f'https://shell.test/schedule/{sid}/api/v1', 'client_base_url': 'https://shell.test',
            'client_id': 'cid-' + sid[:4], 'client_secret': secret, 'revision': revision}


server = ThreadingHTTPServer(('127.0.0.1', 0), Core)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ.update(CORE_INTERNAL_URL=f'http://127.0.0.1:{server.server_address[1]}', PROVISIONING_TOKEN=TOKEN,
                  SHELL_ORIGIN='https://shell.test', DATA_DIR=f'{_tmp.name}/data', RUN_DIR=f'{_tmp.name}/run',
                  DB_ENV='TEST_DB', DB_FILE='t.db', APP_MODULE='tests.fake_app:app', IDLE_SECONDS='2',
                  POLL_SECONDS='0.3', PYTHONPATH=str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from starlette.testclient import TestClient  # noqa: E402

import runner  # noqa: E402


def wait(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


class Runner(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        server.shutdown()
        _tmp.cleanup()

    def test_process_per_institution(self):
        INSTANCES.clear()
        INSTANCES[A] = binding(A)
        INSTANCES[B] = binding(B)
        with TestClient(runner.app) as c:
            # Новые экземпляры запускаются сразу (сервис публикует меню).
            self.assertTrue(wait(lambda: sum(i.running for i in runner.instances.values()) == 2), 'processes did not start')
            a = c.post(f'/{A}/echo/x/y?q=1', content=b'hello').json()
            b = c.get(f'/{B}/echo/').json()
            # У каждого вуза свой процесс, свой ключ, свой файл БД; префикс /<service_id> снимается.
            self.assertNotEqual(a['pid'], b['pid'])
            self.assertEqual((a['client_id'], b['client_id']), ('cid-1111', 'cid-2222'))
            self.assertEqual(a['db'], f'{_tmp.name}/data/{A}/t.db')
            self.assertEqual((a['path'], a['query'], a['body']), ('x/y', {'q': '1'}, 'hello'))
            self.assertEqual(c.get('/33333333-3333-4333-8333-333333333333/echo/').status_code, 404)
            self.assertEqual(c.get('/health').json()['instances'], 2)
            # Состояние экземпляра для пульта: без запуска процесса; неизвестный — 404.
            self.assertEqual(c.get(f'/health?service_id={A}').json(), {'status': 'ok', 'running': True})
            self.assertEqual(c.get('/health?service_id=33333333-3333-4333-8333-333333333333').status_code, 404)

            # Простой: процесс останавливается и поднимается снова первым запросом.
            self.assertTrue(wait(lambda: not runner.instances[A].running, 10), 'idle process not stopped')
            self.assertEqual(c.get(f'/health?service_id={A}').json(), {'status': 'ok', 'running': False})
            self.assertFalse(runner.instances[A].running)  # проверка не будит процесс
            again = c.get(f'/{A}/echo/').json()
            self.assertNotEqual(again['pid'], a['pid'])

            # Смена ключа перезапускает процесс; удалённый экземпляр останавливается.
            INSTANCES[A] = binding(A, secret='s2', revision=2)
            self.assertTrue(wait(lambda: runner.instances[A].binding['revision'] == 2))
            del INSTANCES[B]
            self.assertTrue(wait(lambda: B not in runner.instances))
            self.assertEqual(c.get(f'/{B}/echo/').status_code, 404)
            self.assertTrue(Path(f'{_tmp.name}/data/{B}').is_dir())  # данные удалённого экземпляра остаются

            # Вуз удалён: экземпляр в списке purge — данные стираются.
            PURGE.append(B)
            self.assertTrue(wait(lambda: not Path(f'{_tmp.name}/data/{B}').exists()), 'data not purged')
            self.assertTrue(Path(f'{_tmp.name}/data/{A}').is_dir())


if __name__ == '__main__':
    unittest.main()
