"""Explicit consent fixture for pre-existing API tests (never imported by the app).

Privacy tests use the original TestClient to verify missing/replayed consent.
"""
from fastapi.testclient import TestClient as BaseClient

class TestClient(BaseClient):
    def post(self, url, **kwargs):
        body = kwargs.get('json')
        if str(url) == '/api/v1/auth/token' and isinstance(body, dict) and 'consent_version' not in body:
            challenge = super().post('/api/v1/privacy/challenge').json()
            kwargs['json'] = {**body, 'consent_version': challenge['version'],
                              'consent_challenge': challenge['challenge']}
        return super().post(url, **kwargs)
