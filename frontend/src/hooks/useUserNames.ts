// Имена по UUID. Расписание и курсовые хранят только UUID; имена берём из анкет (user-profile),
// если этот сервис подключён. Без него показываем нейтральную подпись.
import { useEffect, useMemo, useState } from 'react';
import type { ProfilesApi } from '../api/backend';
import type { ProfileCard } from '../api/types';

const cache = new Map<string, Promise<ProfileCard | null>>();

export function loadCard(api: ProfilesApi, scope: string, id: string) {
  const key = `${scope}:${id}`;
  let p = cache.get(key);
  if (!p) {
    p = api.getUser(id).then(r => r.data, () => null);
    cache.set(key, p);
  }
  return p;
}

/**
 * Имя по UUID: строка — имя, '' — имени нет, undefined — ещё загружается
 * (чтобы до ответа не показывать «Имя не указано»).
 */
export function useUserNames(api: ProfilesApi | null, scope: string, ids: string[]): Record<string, string | undefined> {
  const [names, setNames] = useState<Record<string, string>>({});
  const key = [...new Set(ids)].sort().join(',');
  useEffect(() => {
    if (!api || !key) return;
    let alive = true;
    Promise.all(key.split(',').map(id => loadCard(api, scope, id).then(c => [id, c?.display_name ?? ''] as const)))
      .then(pairs => { if (alive) setNames(prev => ({ ...prev, ...Object.fromEntries(pairs) })); });
    return () => { alive = false; };
  }, [api, scope, key]);
  // Без сервиса «Люди» имён не будет — сразу считаем их неизвестными.
  return useMemo(() => api ? names : Object.fromEntries(key.split(',').map(id => [id, ''])), [api, names, key]);
}
