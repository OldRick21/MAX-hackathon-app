// Пункты навигации строятся из каталога сервисов: недоступное текущему профилю не показывается.
import type { ComponentType, SVGProps } from 'react';
import type { ServiceView } from '../../api/types';
import { findService, frameMenus } from '../../state/institution';
import { IconCoursework, IconHome, IconSchedule, IconServices, IconUsers } from '../icons/figma';
import { IconBuilding } from '../icons/ui';

export interface NavItem {
  key: string;
  to: string;
  label: string;
  short: string;
  Icon: ComponentType<SVGProps<SVGSVGElement>>;
  small?: boolean;
  end?: boolean;
}

export function buildNav(base: string, services: ServiceView[] | undefined): NavItem[] {
  const items: NavItem[] = [{ key: 'home', to: base, label: 'Главная', short: 'Главная', Icon: IconHome, end: true }];
  if (findService(services, 'schedule')) items.push({ key: 'schedule', to: `${base}/schedule`, label: 'Расписание', short: 'Расписание', Icon: IconSchedule });
  // Один сервис — один пункт: «Мой профиль» открывается внутри «Люди».
  if (findService(services, 'users')) items.push({ key: 'users', to: `${base}/users`, label: 'Люди', short: 'Люди', Icon: IconUsers });
  if (findService(services, 'coursework')) items.push({ key: 'coursework', to: `${base}/coursework`, label: 'Курсовые работы', short: 'Работы', Icon: IconCoursework });
  const admin = frameMenus(services).find(x => x.service.service_type === 'administration');
  if (admin) {
    items.push({
      key: 'administration', to: `${base}/service/${admin.service.id}/${admin.menu.id}`,
      label: 'Администрирование', short: 'Админ', Icon: IconBuilding, small: true,
    });
  }
  items.push({ key: 'services', to: `${base}/services`, label: 'Все сервисы', short: 'Сервисы', Icon: IconServices });
  return items;
}

/** Нижняя навигация: только Главная и Сервисы. Разделы сервисов открываются из «Все сервисы». */
export function bottomNav(items: NavItem[]): NavItem[] {
  return items.filter(i => i.key === 'home' || i.key === 'services');
}
