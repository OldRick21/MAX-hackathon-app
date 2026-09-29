// Core-сессия ядра: вход по initData MAX, ротация токенов, пагинация.
// Перенесено из MAX-hackathon-app/frontend/static/js/core/api.js с сохранением поведения.

import { ApiError, isUUID, request, type RequestOptions } from '../http';
import type { CoreTokenPair, Page } from '../types';

type Pair = Omit<CoreTokenPair, 'refresh_token'> & { expiresAt: number; refresh_token: string | null };

export function tokenPair<T extends CoreTokenPair>(data: T): T & { expiresAt: number } {
  if (!data || data.token_type !== 'Bearer' || !isUUID(data.session_id) ||
      typeof data.access_token !== 'string' || !data.access_token ||
      typeof data.refresh_token !== 'string' || !data.refresh_token ||
      !Number.isFinite(data.expires_in) || data.expires_in <= 0) {
    console.warn('[core] некорректная пара токенов');
    throw new ApiError('Не удалось войти. Откройте приложение заново.');
  }
  return { ...data, expiresAt: Date.now() + data.expires_in * 1000 };
}

const cancelled = () => new DOMException('Cancelled', 'AbortError');

export class CoreSession {
  pair: Pair | null = null;
  private refreshing: Promise<void> | null = null;
  private generation = 0;

  constructor(private onExpired: () => void = () => {}) {}

  clear(notify = false) {
    this.generation += 1;
    this.pair = null;
    this.refreshing = null;
    if (notify) this.onExpired();
  }

  async login(initData: string, consent?: { consent_version: string; consent_challenge: string }) {
    this.clear();
    const generation = this.generation;
    const { data } = await request<CoreTokenPair>('/api/v1/auth/token', { method: 'POST', body: { initData, ...consent } });
    const pair = tokenPair(data);
    if (generation !== this.generation) throw cancelled();
    this.pair = pair;
  }

  async refresh(): Promise<void> {
    if (this.refreshing) return this.refreshing;
    if (!this.pair?.refresh_token) throw new ApiError('Откройте приложение заново через MAX.', 401);
    const generation = this.generation;
    const old = this.pair;
    // Refresh-токен одноразовый: даже при потере ответа повторно его не отправляем.
    const refreshToken = old.refresh_token;
    old.refresh_token = null;
    this.refreshing = (async () => {
      try {
        const { data } = await request<CoreTokenPair>('/api/v1/auth/refresh', { method: 'POST', body: { refresh_token: refreshToken } });
        const pair = tokenPair(data);
        if (pair.session_id !== old.session_id) throw new ApiError('Сессия изменилась. Откройте приложение заново.');
        if (generation !== this.generation) throw cancelled();
        this.pair = pair;
      } catch (e) {
        if (generation === this.generation) this.clear(true);
        throw e;
      } finally {
        if (generation === this.generation) this.refreshing = null;
      }
    })();
    return this.refreshing;
  }

  async call<T>(path: string, options: RequestOptions = {}): Promise<T> {
    return (await this.callMeta<T>(path, options)).data;
  }

  async callMeta<T>(path: string, options: RequestOptions = {}) {
    if (!path.startsWith('/api/v1/')) throw new Error('Недопустимый путь API');
    const generation = this.generation;
    if (!this.pair) throw new ApiError('Откройте приложение через MAX.', 401);
    if (this.refreshing || this.pair.expiresAt - Date.now() < 30000) await this.refresh();
    if (generation !== this.generation || !this.pair) throw cancelled();
    const token = this.pair.access_token;
    try {
      const res = await request<T>(path, { ...options, token });
      if (generation !== this.generation) throw cancelled();
      return res;
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 401 || generation !== this.generation) throw e;
      // Изменяющие запросы после 401 автоматически не повторяются.
      if ((options.method || 'GET') !== 'GET') { this.clear(true); throw e; }
      if (this.pair?.access_token === token) await this.refresh();
      if (generation !== this.generation || !this.pair) throw cancelled();
      try {
        return await request<T>(path, { ...options, token: this.pair.access_token });
      } catch (second) {
        if (second instanceof ApiError && second.status === 401 && generation === this.generation) this.clear(true);
        throw second;
      }
    }
  }

  async logout() {
    if (this.refreshing) await this.refreshing.catch(() => {});
    const refreshToken = this.pair?.refresh_token;
    this.clear();
    if (refreshToken) await request('/api/v1/auth/logout', { method: 'POST', body: { refresh_token: refreshToken } });
  }
}

/** Получить все страницы списка по cursor. */
export async function listAll<T>(
  fetchPage: (cursor: string | null) => Promise<Page<T>>,
  key: (item: T) => string,
): Promise<T[]> {
  const items = new Map<string, T>();
  const seen = new Set<string>();
  let cursor: string | null = null;
  do {
    const page: Page<T> = await fetchPage(cursor);
    if (!Array.isArray(page.items) || !(page.next_cursor === null || typeof page.next_cursor === 'string')) {
      console.warn('[api] некорректная страница списка');
      throw new ApiError('Сервер ответил неожиданно. Попробуйте позже.');
    }
    for (const item of page.items) items.set(key(item), item);
    cursor = page.next_cursor;
    if (cursor && seen.has(cursor)) throw new ApiError('Сервер ответил неожиданно. Попробуйте позже.');
    if (cursor) seen.add(cursor);
  } while (cursor);
  return [...items.values()];
}
