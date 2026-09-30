// Иконки сервисов — готовый набор (ключи совпадают с catalog.SERVICE_ICONS ядра). Сервис объявляет
// свою в manifest.icon, администратор вуза может заменить её в администрировании; ядро отдаёт итоговую
// в ServiceView.icon. Те же контуры рисует администрирование (services/administration/client/assets/admin.js).
import type { ComponentType, SVGProps } from 'react';
import { base } from './ui';

type P = SVGProps<SVGSVGElement>;

export const SERVICE_ICON_PATHS: Record<string, string> = {
  calendar: '<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4M7.5 14h3M13.5 14h3M7.5 17.5h3"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.4-3.6 3-6 6.5-6s6.1 2.4 6.5 6M15.5 4.8a3.5 3.5 0 0 1 0 6.4M17.5 14.4c2.3.7 3.7 2.8 4 5.6"/>',
  document: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 12.5h6M9 16.5h6"/>',
  building: '<path d="M3 21h18M5 21V10l7-5 7 5v11M9.5 21v-5h5v5M9 12.5h.01M15 12.5h.01"/>',
  box: '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/>',
  book: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5M8 7.5h8"/>',
  chat: '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 9h8M8 12.5h5"/>',
  graduation: '<path d="M2 9.5 12 5l10 4.5-10 4.5z"/><path d="M6 11.3V16c1.7 1.6 3.7 2.4 6 2.4s4.3-.8 6-2.4v-4.7M22 9.5V15"/>',
  clipboard: '<rect x="5" y="4.5" width="14" height="17" rx="2.5"/><path d="M9 4.5V3h6v1.5M9 10.5l1.8 1.8L15 8.5M9 16h6"/>',
  chart: '<path d="M4 20V4M4 20h16M8.5 16v-4M13 16V8M17.5 16v-6"/>',
  bell: '<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2H4.5z"/><path d="M10 21h4"/>',
  star: '<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
  folder: '<path d="M3.5 6.5A2 2 0 0 1 5.5 4.5H10l2 2.5h6.5a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
  video: '<rect x="3" y="6" width="13" height="12" rx="2.5"/><path d="m16 10.5 5-3v9l-5-3"/>',
  flask: '<path d="M9.5 3h5M10 3v6.5L4.8 18.3A1.8 1.8 0 0 0 6.4 21h11.2a1.8 1.8 0 0 0 1.6-2.7L14 9.5V3M7.5 14.5h9"/>',
  code: '<path d="m8.5 7.5-5 4.5 5 4.5M15.5 7.5l5 4.5-5 4.5M13.5 4.5l-3 15"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.5 3.7 5.5 3.7 9s-1.2 6.5-3.7 9c-2.5-2.5-3.7-5.5-3.7-9S9.5 5.5 12 3z"/>',
  heart: '<path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7a4.3 4.3 0 0 1 7.5 2.8C19.5 15.4 12 20 12 20z"/>',
  trophy: '<path d="M8 4h8v5a4 4 0 0 1-8 0zM8 6H4.5v1.5A3.5 3.5 0 0 0 8 11M16 6h3.5v1.5A3.5 3.5 0 0 1 16 11M12 13v4M8.5 20.5h7M9.5 17h5v3.5h-5z"/>',
  map: '<path d="M9 4.5 3.5 6.5v13L9 17.5l6 2 5.5-2v-13L15 6.5z"/><path d="M9 4.5v13M15 6.5v13"/>',
  briefcase: '<rect x="3" y="7" width="18" height="13" rx="2.5"/><path d="M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7M3 12.5h18"/>',
  wallet: '<path d="M4 7.5A2.5 2.5 0 0 1 6.5 5H18v3M4 7.5v10A2.5 2.5 0 0 0 6.5 20H20v-4.5"/><path d="M4 7.5A2.5 2.5 0 0 0 6.5 10H20v5.5h-4a2.8 2.8 0 0 1 0-5.5"/>',
};

const cache = new Map<string, ComponentType<P>>();

/** Компонент иконки по ключу набора; неизвестный ключ — «Сервис» (box). */
export function serviceIcon(name: string | undefined | null): ComponentType<P> {
  const key = name && name in SERVICE_ICON_PATHS ? name : 'box';
  let Icon = cache.get(key);
  if (!Icon) {
    const markup = SERVICE_ICON_PATHS[key];
    Icon = (p: P) => <svg {...base(p)} dangerouslySetInnerHTML={{ __html: markup }} />;
    Icon.displayName = `ServiceIcon(${key})`;
    cache.set(key, Icon);
  }
  return Icon;
}
