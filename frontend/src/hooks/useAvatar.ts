// Аватары пользователей: один запрос на человека за сессию, общий кеш для всех экранов.
import { useEffect, useState } from 'react';
import type { Backend } from '../api/backend';
import { useBackend } from '../state/session';

const cache = new Map<string, Promise<string | null>>();
window.addEventListener('vuzy:privacy-withdrawn', () => {
  for (const value of cache.values()) value.then(url => { if (url) URL.revokeObjectURL(url); });
  cache.clear();
});
const EVENT = 'vuzy:avatar-changed';

function load(backend: Backend, userId: string) {
  let entry = cache.get(userId);
  if (!entry) {
    entry = backend.getAvatar(userId).then(b => (b ? URL.createObjectURL(b) : null)).catch(() => null);
    cache.set(userId, entry);
  }
  return entry;
}

/** Сбросить кеш после загрузки или удаления — все экраны перечитают картинку. */
export function invalidateAvatar(userId: string) {
  const old = cache.get(userId);
  cache.delete(userId);
  old?.then(url => { if (url) URL.revokeObjectURL(url); });
  window.dispatchEvent(new CustomEvent(EVENT, { detail: userId }));
}

/** URL картинки для <img> или null (тогда Avatar рисует инициалы). */
export function useAvatar(userId: string | null | undefined): string | null {
  const backend = useBackend();
  const [src, setSrc] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    if (!userId) return;
    const onChange = (e: Event) => { if ((e as CustomEvent).detail === userId) setVersion(v => v + 1); };
    window.addEventListener(EVENT, onChange);
    return () => window.removeEventListener(EVENT, onChange);
  }, [userId]);
  useEffect(() => {
    let alive = true;
    setSrc(null);
    if (userId) load(backend, userId).then(url => { if (alive) setSrc(url); });
    return () => { alive = false; };
  }, [backend, userId, version]);
  return src;
}
