// Сервисная сессия для экранов, встроенных прямо в оболочку (расписание, анкеты, курсовые).
// Токен выдаёт ядро: POST /institution/{id}/service/{service_id}/session { profile }.
// Запросы идут на api_base_url сервиса. Сервис должен разрешать CORS для origin оболочки.

import { ApiError, request, type RequestOptions } from '../http';
import type { ServiceTokenPair, ServiceView } from '../types';
import { tokenPair, type CoreSession } from './core';

type Pair = Omit<ServiceTokenPair, 'refresh_token'> & { expiresAt: number; refresh_token: string | null };

export function sessionPath(s: ServiceView) {
  return `/api/v1/institution/${s.institution_id}/service/${s.id}/session`;
}

export function checkedPair(data: ServiceTokenPair, s: ServiceView, parent: string | undefined, previous?: Pair): Pair {
  const pair: Pair = tokenPair(data);
  if (pair.institution_id !== s.institution_id || pair.service_id !== s.id || pair.profile !== s.profile ||
      pair.parent_session_id !== parent || (previous && pair.session_id !== previous.session_id)) {
    console.warn('[service] сессия другого контекста', s.id);
    throw new ApiError('Не удалось открыть сервис. Попробуйте ещё раз.');
  }
  return pair;
}

export class ServiceSession {
  private pair: Pair | null = null;
  private pending: Promise<Pair> | null = null;
  private disposed = false;
  /** Может быть пустым, если сессию первым открыл виджет главной: экраны дописывают адрес (real/index.ts). */
  apiBase: string;

  constructor(private core: CoreSession, readonly service: ServiceView) {
    this.apiBase = service.api_base_url.replace(/\/+$/, '');
  }

  private async open(): Promise<Pair> {
    const parent = this.core.pair?.session_id;
    const data = await this.core.call<ServiceTokenPair>(sessionPath(this.service), {
      method: 'POST', body: { profile: this.service.profile },
    });
    return checkedPair(data, this.service, parent);
  }

  private async rotate(old: Pair): Promise<Pair> {
    const refreshToken = old.refresh_token;
    old.refresh_token = null;
    if (!refreshToken) return this.open();
    try {
      const { data } = await request<ServiceTokenPair>(`${sessionPath(this.service)}/refresh`, {
        method: 'POST', body: { refresh_token: refreshToken },
      });
      return checkedPair(data, this.service, old.parent_session_id, old);
    } catch {
      // Refresh не удался — открываем новую сессию вместо повтора старого токена.
      return this.open();
    }
  }

  private async token(force = false): Promise<string> {
    if (this.disposed) throw new DOMException('Cancelled', 'AbortError');
    if (!this.pending && (force || !this.pair || this.pair.expiresAt - Date.now() < 20000)) {
      const old = this.pair;
      this.pending = (old ? this.rotate(old) : this.open()).finally(() => { this.pending = null; });
    }
    if (this.pending) this.pair = await this.pending;
    return this.pair!.access_token;
  }

  /** Путь из OpenAPI (/api/v1/...) → адрес на api_base_url сервиса. */
  url(path: string) {
    return this.apiBase + path.replace(/^\/api\/v1/, '');
  }

  async call<T>(path: string, opts: RequestOptions = {}) {
    const token = await this.token();
    try {
      return await request<T>(this.url(path), { ...opts, token });
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 401 || (opts.method ?? 'GET') !== 'GET') throw e;
      return request<T>(this.url(path), { ...opts, token: await this.token(true) });
    }
  }

  /** GET по готовому адресу сервиса (данные виджета): один повтор с новой сессией на 401. */
  async getUrl<T>(url: string, signal?: AbortSignal) {
    try {
      return await request<T>(url, { token: await this.token(), signal });
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 401) throw e;
      return request<T>(url, { token: await this.token(true), signal });
    }
  }

  async accessToken() {
    return this.token();
  }

  dispose() {
    this.disposed = true;
    const pair = this.pair;
    this.pair = null;
    if (pair && this.core.pair && pair.parent_session_id === this.core.pair.session_id) {
      void this.core.call(`${sessionPath(this.service)}/${pair.session_id}`, { method: 'DELETE' }).catch(() => {});
    }
  }
}
