// Пункты навигации строятся из каталога сервисов: недоступное текущему профилю не показывается.
import type { ComponentType, SVGProps } from 'react';
import type { ServiceView } from '../../api/types';
import { NATIVE_MENUS } from '../../state/institution';
import { IconHome, IconServices } from '../icons/figma';
import { serviceIcon } from '../icons/services';

type Icon = ComponentType<SVGProps<SVGSVGElement>>;

export interface NavItem {
  key: string;
  to: string;
  label: string;
  short: string;
  Icon: Icon;
  end?: boolean;
}

export interface ServiceEntry {
  key: string;
  to: string;
  name: string;
  short: string;
  desc: string;
  Icon: Icon;
}

// icon — запасная иконка, если ядро её не прислало; обычно берётся ServiceView.icon (выбор сервиса или вуза).
const NATIVE: Record<string, { section: string; name: string; short: string; desc: string; icon: string }> = {
  // Один сервис — одна карточка: оба меню сервиса «Люди» ведут в «Люди», профиль открывается внутри.
  home: { section: 'users', name: 'Люди', short: 'Люди', desc: 'Ваш профиль и участники вуза', icon: 'users' },
  users: { section: 'users', name: 'Люди', short: 'Люди', desc: 'Ваш профиль и участники вуза', icon: 'users' },
  schedule: { section: 'schedule', name: 'Расписание', short: 'Расписание', desc: 'Занятия на неделю', icon: 'calendar' },
  schedule_admin: { section: 'schedule', name: 'Расписание', short: 'Расписание', desc: 'Все группы и редактирование занятий', icon: 'calendar' },
  coursework: { section: 'coursework', name: 'Курсовые работы', short: 'Работы', desc: 'Загрузка и проверка работ', icon: 'document' },
  coursework_admin: { section: 'coursework', name: 'Курсовые работы', short: 'Работы', desc: 'Все работы вуза', icon: 'document' },
};

/** Все сервисы каталога, по пункту на раздел: встроенные экраны и меню сервисов в iframe. */
export function serviceEntries(base: string, services: ServiceView[]): ServiceEntry[] {
  const seen = new Set<string>();
  return services.flatMap(svc => svc.menus.flatMap(menu => {
    const native = Object.values(NATIVE_MENUS).some(d => d.type === svc.service_type && (d.menus as readonly string[]).includes(menu.id)) ? NATIVE[menu.id] : undefined;
    if (native) {
      if (seen.has(native.section)) return [];
      seen.add(native.section);
      return [{ key: `${svc.id}:${menu.id}`, to: `${base}/${native.section}`, name: native.name, short: native.short, desc: native.desc,
        Icon: serviceIcon(svc.icon ?? native.icon) }];
    }
    const admin = svc.service_type === 'administration';
    return [{
      key: `${svc.id}:${menu.id}`,
      to: `${base}/service/${svc.id}/${menu.id}`,
      name: menu.display_name,
      short: admin ? 'Админ' : menu.display_name,
      desc: svc.deployment === 'local' ? 'Сервис вуза' : svc.display_name !== menu.display_name ? svc.display_name : 'Сервис платформы',
      Icon: serviceIcon(svc.icon ?? (admin ? 'building' : 'box')),
    }];
  }));
}

export function buildNav(base: string, services: ServiceView[] | undefined): NavItem[] {
  return [
    { key: 'home', to: base, label: 'Главная', short: 'Главная', Icon: IconHome, end: true },
    ...serviceEntries(base, services ?? []).map(e => ({ key: e.key, to: e.to, label: e.name, short: e.short, Icon: e.Icon })),
    { key: 'services', to: `${base}/services`, label: 'Все сервисы', short: 'Сервисы', Icon: IconServices },
  ];
}

/** Боковая колонка (широкий экран): Главная и все сервисы — отдельная кнопка «Все сервисы» не нужна. */
export function sideNav(items: NavItem[]): NavItem[] {
  return items.filter(i => i.key !== 'services');
}

/** Нижняя навигация (телефон): только Главная и Сервисы. Разделы сервисов открываются из «Все сервисы». */
export function bottomNav(items: NavItem[]): NavItem[] {
  return items.filter(i => i.key === 'home' || i.key === 'services');
}
