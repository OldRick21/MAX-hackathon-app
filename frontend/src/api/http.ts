// HTTP-слой. Пользователю показываются только понятные сообщения;
// технические подробности (код, request_id) уходят в console.

export class ApiError extends Error {
  constructor(
    message: string,
    public status = 0,
    public code = '',
    public requestId = '',
  ) {
    super(message);
  }
}

const STATUS_MESSAGES: Record<number, string> = {
  401: 'Сессия истекла. Откройте приложение заново через MAX.',
  403: 'Нет доступа к этому действию.',
  404: 'Не удалось найти данные. Возможно, их удалили.',
  409: 'Действие сейчас недоступно. Обновите страницу и попробуйте снова.',
  412: 'Данные успели измениться. Обновите страницу и повторите действие.',
  413: 'Файл слишком большой. Максимальный размер — 20 МБ.',
  415: 'Можно загрузить только PDF-файл.',
  422: 'Проверьте заполненные поля.',
  428: 'Данные успели измениться. Обновите страницу и повторите действие.',
  429: 'Слишком много запросов. Подождите немного и повторите.',
  503: 'Сервис временно недоступен. Попробуйте позже.',
};

const CODE_MESSAGES: Record<string, string> = {
  INVALID_STATE: 'Это действие недоступно для работы в текущем статусе.',
  INVALID_TIME_RANGE: 'Время окончания должно быть позже времени начала.',
  INVALID_REFERENCE: 'Выбранная группа или преподаватель больше не доступны.',
  FILE_STORAGE_UNAVAILABLE: 'Хранилище файлов временно недоступно.',
  RATE_LIMITED: 'Слишком много загрузок подряд. Подождите минуту.',
};

export const isUUID = (v: unknown): v is string =>
  typeof v === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v);

export interface RequestOptions {
  method?: string;
  body?: unknown;
  form?: FormData;
  token?: string;
  signal?: AbortSignal;
  headers?: Record<string, string>;
  /** Вернуть Response целиком (для файлов). */
  raw?: boolean;
}

export interface ResponseMeta<T> {
  data: T;
  etag: string | null;
}

export function toApiError(status: number, payload: unknown, url: string): ApiError {
  const err = (payload as { error?: { code?: string; request_id?: string } } | null)?.error;
  const code = err?.code ?? '';
  const message = CODE_MESSAGES[code] || STATUS_MESSAGES[status] || 'Не удалось выполнить запрос. Попробуйте ещё раз.';
  console.warn('[api]', status, code || '(без кода)', url, err?.request_id ?? '');
  return new ApiError(message, status, code, err?.request_id ?? '');
}

export async function request<T>(url: string, opts: RequestOptions = {}): Promise<ResponseMeta<T>> {
  const { method = 'GET', body, form, token, signal, headers = {} } = opts;
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) abort();
  const timer = setTimeout(abort, 20000);
  try {
    const res = await fetch(url, {
      method,
      signal: controller.signal,
      credentials: 'omit',
      cache: 'no-store',
      redirect: 'error',
      headers: {
        Accept: opts.raw ? '*/*' : 'application/json',
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...headers,
      },
      body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
    });
    const etag = res.headers.get('ETag');
    if (opts.raw && res.ok) return { data: res as unknown as T, etag };
    if (res.status === 204) return { data: null as T, etag };
    const payload = await res.json().catch(() => null);
    if (!res.ok) throw toApiError(res.status, payload, url);
    if (payload === null || typeof payload !== 'object') {
      console.warn('[api] ответ не по контракту', url);
      throw new ApiError('Сервер ответил неожиданно. Попробуйте позже.');
    }
    return { data: payload as T, etag };
  } catch (e) {
    if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
    if (e instanceof ApiError) throw e;
    console.warn('[api] сеть', url, e);
    throw new ApiError('Нет связи с сервером. Проверьте интернет и повторите.');
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
  }
}

export const isAbort = (e: unknown) => e instanceof DOMException && e.name === 'AbortError';

/** Сообщение для человека из любой ошибки. */
export function humanMessage(e: unknown, fallback = 'Что-то пошло не так. Попробуйте ещё раз.'): string {
  if (e instanceof ApiError) return e.message;
  console.error(e);
  return fallback;
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}
