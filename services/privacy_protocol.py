"""Privacy protocol v1 SDK. Vendored verbatim into standalone service packages.

Pure ASGI lock spans the entire response, including streaming and writes. Erasure
cannot race a local request. Every authenticated API request synchronizes first;
background polling handles idle instances. Transport failures deny API access.
Run one worker per instance, as in the supplied Dockerfiles.
"""
import asyncio
import contextlib
import logging

log = logging.getLogger('privacy')

class PrivacyGuard:
    def __init__(self, app, erase, client):
        self.app, self.erase = app, erase
        self.client = client
        self.lock = asyncio.Lock()
        self.worker = None
        self.service_id = None
        self.institution_id = None

    def request(self, method, path):
        client = self.client()
        binding = client.binding()
        self.service_id, self.institution_id = binding.service_id, binding.institution_id
        response = client.machine(method, path) if hasattr(client, 'machine') else client._machine_request(binding, method, path)
        if response.status_code != 200:
            raise RuntimeError('Privacy protocol unavailable')
        return response.json()

    def synchronize(self):
        tasks = self.request('GET', '/api/v1/internal/privacy/tasks')['items']
        if not isinstance(tasks, list):
            raise ValueError('Invalid privacy task list')
        for task in tasks:
            from uuid import UUID
            UUID(task['subject_id'])
            UUID(task['task_id'])
            if task['institution_id'] != self.institution_id or task['action'] != 'restrict_and_erase':
                raise ValueError('Unexpected privacy task scope')
            self.erase(task['subject_id'], self.institution_id, self.service_id)
            # Ack only after durable local deletion. Lost acknowledgments retry safely.
            self.request('POST', '/api/v1/internal/privacy/tasks/' + task['task_id'] + '/complete')

    async def poll(self):
        while True:
            try:
                async with self.lock:
                    await self.synchronize_async()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log.warning('Privacy synchronization deferred: %s', type(error).__name__)
            # Раз в минуту: при опросе раз в 5 с десятки процессов вузов перегружали ядро на слабом сервере.
            await asyncio.sleep(60)

    async def synchronize_async(self):
        task = asyncio.create_task(asyncio.to_thread(self.synchronize))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Do not release the lock while a background thread is still deleting.
            with contextlib.suppress(Exception):
                await task
            raise

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            async def lifecycle_send(message):
                if message['type'] == 'lifespan.startup.complete':
                    self.worker = asyncio.create_task(self.poll())
                if message['type'] in ('lifespan.shutdown.complete', 'lifespan.shutdown.failed') and self.worker:
                    self.worker.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self.worker
                await send(message)
            return await self.app(scope, receive, lifecycle_send)
        if (scope['type'] != 'http' or not scope['path'].startswith('/api/v1/')
                or scope['path'].endswith('/health') or scope['method'] == 'OPTIONS'):
            return await self.app(scope, receive, send)
        async with self.lock:
            try:
                await self.synchronize_async()
            except Exception:
                from starlette.responses import JSONResponse
                return await JSONResponse({'error': {'code': 'PRIVACY_SYNC_UNAVAILABLE',
                    'message': 'Проверка удаления данных временно недоступна. Повторите позже.'}},
                    status_code=503, headers={'Cache-Control': 'no-store'})(scope, receive, send)
            return await self.app(scope, receive, send)
