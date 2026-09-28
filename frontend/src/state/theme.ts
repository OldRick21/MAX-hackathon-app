// Оформление: как в системе (по умолчанию), светлое или тёмное. Выбор — удобство этого устройства,
// поэтому живёт в localStorage; без него (приватный режим, запрет) работает «как в системе».
import { useSyncExternalStore } from 'react';

export type ThemeChoice = 'system' | 'light' | 'dark';

const KEY = 'vuzy:theme';
const ORDER: ThemeChoice[] = ['system', 'light', 'dark'];
export const THEME_LABEL: Record<ThemeChoice, string> = { system: 'как в системе', light: 'светлое', dark: 'тёмное' };

const listeners = new Set<() => void>();
const dark = window.matchMedia?.('(prefers-color-scheme: dark)');

function read(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    return v === 'light' || v === 'dark' ? v : 'system';
  } catch {
    return 'system';
  }
}

let current: ThemeChoice = read();

function apply() {
  const root = document.documentElement;
  if (current === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', current);
  // Цвет системной панели браузера/MAX — под фон страницы.
  const isDark = current === 'dark' || (current === 'system' && !!dark?.matches);
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', isDark ? '#0E1016' : '#F6F7FB');
}

export function setTheme(next: ThemeChoice) {
  current = next;
  try {
    if (next === 'system') localStorage.removeItem(KEY); else localStorage.setItem(KEY, next);
  } catch { /* выбор просто не запомнится */ }
  apply();
  listeners.forEach(l => l());
}

export const nextTheme = (t: ThemeChoice) => ORDER[(ORDER.indexOf(t) + 1) % ORDER.length];

export function useTheme(): ThemeChoice {
  return useSyncExternalStore(cb => { listeners.add(cb); return () => listeners.delete(cb); }, () => current);
}

dark?.addEventListener?.('change', () => { if (current === 'system') apply(); });
apply();
