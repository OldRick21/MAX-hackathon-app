// Обёртка над MAX Bridge (window.WebApp из https://st.max.ru/js/max-web-app.js).

import type { MaxUserInfo } from './types';

interface MaxWebApp {
  initData?: string;
  initDataUnsafe?: { user?: MaxUserInfo };
  ready?: () => void;
  openLink?: (url: string) => void;
  openMaxLink?: (url: string) => void;
}

declare global {
  interface Window { WebApp?: MaxWebApp }
}

export const bridge = () => window.WebApp;

const BRIDGE_URL = 'https://st.max.ru/js/max-web-app.js';
let bridgeLoading: Promise<void> | null = null;

/** Подключить MAX Bridge (только в режиме real). Не блокирует отрисовку и не ждёт дольше 10 секунд. */
export function loadBridge(): Promise<void> {
  if (window.WebApp) return Promise.resolve();
  bridgeLoading ??= new Promise<void>(resolve => {
    const script = document.createElement('script');
    script.src = BRIDGE_URL;
    script.async = true;
    const done = () => { clearTimeout(timer); resolve(); };
    const timer = setTimeout(done, 10000);
    script.onload = done;
    script.onerror = done; // без Bridge покажем экран «Откройте приложение в MAX»
    document.head.appendChild(script);
  });
  return bridgeLoading;
}

export function initData(): string | null {
  const data = bridge()?.initData;
  return typeof data === 'string' && data.trim() ? data : null;
}

/** Имя и фото из initDataUnsafe: подпись не проверена — только для отображения. */
export function maxUserInfo(): MaxUserInfo | null {
  const u = bridge()?.initDataUnsafe?.user;
  if (!u) return null;
  const photo = typeof u.photo_url === 'string' && /^https:\/\//.test(u.photo_url) ? u.photo_url : undefined;
  return { first_name: u.first_name, last_name: u.last_name, photo_url: photo };
}

/** Ссылки max.ru открываем внутри MAX, остальные — во внешнем браузере. */
export function openExternal(url: string) {
  let parsed: URL;
  try { parsed = new URL(url); } catch { return; }
  if (parsed.protocol !== 'https:') return;
  const b = bridge();
  const isMax = /(^|\.)max\.ru$/.test(parsed.hostname);
  if (isMax && b?.openMaxLink) return b.openMaxLink(parsed.href);
  if (b?.openLink) return b.openLink(parsed.href);
  window.open(parsed.href, '_blank', 'noopener,noreferrer');
}
