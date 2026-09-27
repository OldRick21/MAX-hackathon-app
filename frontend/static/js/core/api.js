export class ApiError extends Error {
  constructor(message, status = 0, requestId = '') {
    super(message);
    this.status = status;
    this.requestId = requestId;
  }
}

export const isUUID = (value) => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
export const profiles = ['student', 'teacher', 'admin'];
export const isProfile = (value) => profiles.includes(value);

export async function request(path, { method = 'GET', body, token, signal } = {}) {
  if (!path.startsWith('/api/v1/')) throw new Error('Недопустимый путь API');
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) abort();
  const timeout = setTimeout(abort, 15000);
  try {
    const response = await fetch(path, {
      method, signal: controller.signal, credentials: 'omit', cache: 'no-store', redirect: 'error',
      headers: { Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (response.status === 204) return null;
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      // Не показываем произвольное тело ответа: оно может содержать входные credentials.
      const messages = { 401: 'Сессия истекла. Откройте приложение заново через MAX.',
        403: 'Нет доступа к выбранному профилю или сервису.', 404: 'Ресурс не найден или API ещё не подключён.',
        409: 'Вуз или сервис сейчас недоступен.', 422: 'Сервер отклонил параметры запроса.',
        429: 'Слишком много запросов. Подождите и повторите действие.', 503: 'Сервис временно недоступен.' };
      throw new ApiError(messages[response.status] || 'Не удалось выполнить запрос к серверу.', response.status,
        data?.error?.request_id || response.headers.get('X-Request-ID') || '');
    }
    if (!data || typeof data !== 'object') throw new ApiError('Сервер вернул ответ не по контракту.');
    return data;
  } catch (error) {
    if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
    if (error instanceof ApiError) throw error;
    throw new ApiError('Нет ответа от сервера. Проверьте соединение.');
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', abort);
  }
}

export function tokenPair(data) {
  if (!data || data.token_type !== 'Bearer' || !isUUID(data.session_id) ||
      typeof data.access_token !== 'string' || !data.access_token ||
      typeof data.refresh_token !== 'string' || !data.refresh_token ||
      !Number.isFinite(data.expires_in) || data.expires_in <= 0 ||
      !Number.isFinite(data.refresh_expires_in) || data.refresh_expires_in <= 0) {
    throw new ApiError('Некорректный ответ сервера при создании сессии.');
  }
  return { ...data, expiresAt: Date.now() + data.expires_in * 1000 };
}

export class CoreSession {
  pair = null;
  refreshing = null;
  generation = 0;
  constructor(onExpired = () => {}) { this.onExpired = onExpired; }

  clear(notify = false) {
    this.generation += 1;
    this.pair = null;
    this.refreshing = null;
    if (notify) this.onExpired();
  }

  async login(initData) {
    this.clear();
    const generation = this.generation;
    const pair = tokenPair(await request('/api/v1/auth/token', { method: 'POST', body: { initData } }));
    if (generation !== this.generation) throw new DOMException('Cancelled', 'AbortError');
    this.pair = pair;
  }

  async refresh() {
    if (this.refreshing) return this.refreshing;
    if (!this.pair) throw new ApiError('Откройте приложение через MAX.', 401);
    const generation = this.generation;
    const old = this.pair;
    // Старый refresh потребляется только один раз, даже при потере ответа.
    const refreshToken = old.refresh_token;
    old.refresh_token = null;
    this.refreshing = (async () => {
      try {
        const pair = tokenPair(await request('/api/v1/auth/refresh', { method: 'POST', body: { refresh_token: refreshToken } }));
        if (pair.session_id !== old.session_id) throw new ApiError('Сервер изменил контекст сессии.');
        if (generation !== this.generation) throw new DOMException('Cancelled', 'AbortError');
        this.pair = pair;
      } catch (error) {
        if (generation === this.generation) this.clear(true);
        throw error;
      } finally {
        if (generation === this.generation) this.refreshing = null;
      }
    })();
    return this.refreshing;
  }

  async call(path, options = {}) {
    const generation = this.generation;
    if (!this.pair) throw new ApiError('Откройте приложение через MAX.', 401);
    if (this.refreshing || this.pair.expiresAt - Date.now() < 30000) await this.refresh();
    if (generation !== this.generation || !this.pair) throw new DOMException('Cancelled', 'AbortError');
    const token = this.pair.access_token;
    try {
      const data = await request(path, { ...options, token });
      if (generation !== this.generation) throw new DOMException('Cancelled', 'AbortError');
      return data;
    } catch (error) {
      if (error.status !== 401 || generation !== this.generation) throw error;
      if ((options.method || 'GET') !== 'GET') { this.clear(true); throw error; }
      if (this.pair?.access_token === token) await this.refresh();
      if (generation !== this.generation || !this.pair) throw new DOMException('Cancelled', 'AbortError');
      try {
        const data = await request(path, { ...options, token: this.pair.access_token });
        if (generation !== this.generation) throw new DOMException('Cancelled', 'AbortError');
        return data;
      } catch (secondError) {
        if (secondError.status === 401 && generation === this.generation) this.clear(true);
        throw secondError;
      }
    }
  }

  async logout() {
    // Дождаться начатой ротации, чтобы не послать уже использованный refresh.
    if (this.refreshing) await this.refreshing.catch(() => {});
    const refreshToken = this.pair?.refresh_token;
    this.clear();
    if (refreshToken) await request('/api/v1/auth/logout', { method: 'POST', body: { refresh_token: refreshToken } });
  }
}

export async function listAll(core, path, query = {}, signal) {
  const items = new Map();
  const cursors = new Set();
  let cursor;
  do {
    const params = new URLSearchParams({ ...query, limit: '100', ...(cursor ? { cursor } : {}) });
    const page = await core.call(`${path}?${params}`, { signal });
    if (!Array.isArray(page.items) || !(page.next_cursor === null || typeof page.next_cursor === 'string')) {
      throw new ApiError('Некорректный ответ каталога.');
    }
    for (const item of page.items) {
      if (!isUUID(item.id)) throw new ApiError('Некорректный идентификатор в каталоге.');
      items.set(item.id, item);
    }
    cursor = page.next_cursor;
    if (cursor && cursors.has(cursor)) throw new ApiError('Сервер повторил страницу каталога.');
    if (cursor) cursors.add(cursor);
  } while (cursor);
  return [...items.values()];
}
