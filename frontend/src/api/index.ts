// Выбор реализации данных. mock — тестовые данные, real — настоящее API.
// Режим задаётся VITE_API_MODE (см. .env.example); по умолчанию mock в разработке и real в сборке.

import type { Backend } from './backend';

export const API_MODE: 'mock' | 'real' =
  (import.meta.env.VITE_API_MODE as 'mock' | 'real' | undefined) ?? (import.meta.env.DEV ? 'mock' : 'real');

export async function loadBackend(): Promise<Backend> {
  if (API_MODE === 'mock') return (await import('./mock')).createMockBackend();
  return (await import('./real')).createRealBackend();
}

export type { Backend } from './backend';
