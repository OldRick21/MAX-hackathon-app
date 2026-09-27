import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { CoreSession, listAll, tokenPair } from '../static/js/core/api.js';
import { clientAddress } from '../static/js/core/service-frame.js';
const nativeFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = nativeFetch; });
const id = '11111111-1111-4111-8111-111111111111';
const pair = (suffix = 'a') => ({ token_type: 'Bearer', session_id: id, access_token: `access-${suffix}`, refresh_token: `refresh-${suffix}`, expires_in: 600, refresh_expires_in: 6000 });
const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
const coreWithPair = () => { const core = new CoreSession(); core.pair = tokenPair(pair()); return core; };

test('MAX login sends only original initData, with no cookies', async () => {
  globalThis.fetch = async (url, opts) => {
    assert.equal(url, '/api/v1/auth/token');
    assert.deepEqual(JSON.parse(opts.body), { initData: 'signed%20original' });
    assert.equal(opts.credentials, 'omit');
    assert.equal(opts.redirect, 'error');
    return json(pair());
  };
  const core = new CoreSession(); await core.login('signed%20original'); assert.equal(core.pair.session_id, id);
});

test('parallel expired requests perform one core refresh', async () => {
  const core = coreWithPair(); core.pair.expiresAt = 0;
  let refreshes = 0;
  globalThis.fetch = async (url, opts) => {
    if (url.endsWith('/refresh')) { refreshes++; await new Promise(r => setTimeout(r, 10)); return json(pair('b')); }
    assert.equal(opts.headers.Authorization, 'Bearer access-b'); return json({ id });
  };
  await Promise.all([core.call('/api/v1/auth/me'), core.call('/api/v1/institution')]);
  assert.equal(refreshes, 1);
});

test('concurrent 401 responses do not rotate an already renewed pair twice', async () => {
  const core = coreWithPair(); let refreshes = 0;
  globalThis.fetch = async (url, opts) => {
    if (url.endsWith('/refresh')) { refreshes++; await new Promise(r => setTimeout(r, 5)); return json(pair('b')); }
    if (opts.headers.Authorization === 'Bearer access-a') { await new Promise(r => setTimeout(r, url.endsWith('/me') ? 1 : 25)); return json({}, 401); }
    return json({ id });
  };
  await Promise.all([core.call('/api/v1/auth/me'), core.call('/api/v1/institution')]);
  assert.equal(refreshes, 1);
});

test('lost refresh response clears session and never reuses refresh', async () => {
  let expired = 0, calls = 0;
  const core = new CoreSession(() => expired++); core.pair = tokenPair(pair());
  globalThis.fetch = async () => { calls++; throw new TypeError('network'); };
  await assert.rejects(core.refresh()); await assert.rejects(core.refresh());
  assert.equal(calls, 1); assert.equal(expired, 1); assert.equal(core.pair, null);
});

test('late refresh cannot resurrect a cleared session', async () => {
  const core = coreWithPair(); let release;
  globalThis.fetch = () => new Promise(r => { release = r; });
  const refresh = core.refresh(); core.clear(); release(json(pair('b')));
  await assert.rejects(refresh); assert.equal(core.pair, null);
});

test('mutation is not automatically retried on 401', async () => {
  const core = coreWithPair(); let calls = 0;
  globalThis.fetch = async () => { calls++; return json({}, 401); };
  await assert.rejects(core.call('/api/v1/service/session', { method: 'POST', body: { profile: 'student' } }));
  assert.equal(calls, 1);
});

test('pagination follows cursor even when the first page is empty', async () => {
  const seen = [];
  const core = { call: async (url) => { seen.push(url); return url.includes('cursor=next') ? { items: [{ id }], next_cursor: null } : { items: [], next_cursor: 'next' }; } };
  assert.equal((await listAll(core, '/api/v1/institution', { locale: 'en' })).length, 1);
  assert.ok(seen.every(url => url.includes('locale=en')));
});

test('repeating cursor fails instead of an infinite loop', async () => {
  const core = { call: async () => ({ items: [], next_cursor: 'same' }) };
  await assert.rejects(listAll(core, '/api/v1/institution'));
});

test('client entrypoint is constrained to separate HTTPS origin', () => {
  const service = { client_base_url: 'https://service.test', api_base_url: 'https://service.test/api/v1' };
  assert.equal(clientAddress(service, { entrypoint_path: '/schedule' }, 'https://core.test').href, 'https://service.test/schedule');
  for (const path of ['//evil.test', '/../../x', '/%2e%2e/x', '/a\\b', '/home?token=x', '/home#token', 'javascript:alert(1)']) {
    assert.throws(() => clientAddress(service, { entrypoint_path: path }, 'https://core.test'));
  }
  assert.throws(() => clientAddress({ ...service, client_base_url: 'https://core.test' }, { entrypoint_path: '/' }, 'https://core.test'));
  assert.throws(() => clientAddress({ ...service, client_base_url: 'http://service.test' }, { entrypoint_path: '/' }, 'https://core.test'));
});
