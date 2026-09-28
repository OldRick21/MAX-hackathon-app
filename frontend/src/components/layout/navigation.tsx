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
  // Экран профиля закрыт тем же гейтом, что и список, поэтому условие здесь — тоже 'users'.
  if (findService(services, 'users')) items.push({ key: 'profile', to: `${base}/users/me`, label: 'Мой профиль', short: 'Профиль', Icon: IconUsers });
  if (findService(services, 'users')) items.push({ key: 'users', to: `${base}/users`, label: 'Пользователи', short: 'Люди', Icon: IconUsers, end: true });
  if (findService(services, 'coursework')) items.push({ key: 'coursework', to: `${base}/coursework`, label: 'Курсовые работы', short: 'Работы', Icon: IconCoursework });
  const admin = frameMenus(services).find(x => x.service.service_type === 'administration');
  if (admin) {
    items.push({
      key: 'administration', to: `${base}/service/${admin.service.id}/${admin.menu.id}`,
      label: 'Администрирование', short: 'Админ', Icon: IconBuilding, small: true,
    });
  }
  items.push({ key: 'services', to: `${base}/services`, label: 'Все сервисы', short: 'Ещё', Icon: IconServices });
  return items;
}

/** Нижняя навигация: Главная | Расписание | Пользователи | Работы | Ещё (handoff, раздел 8). */
export function bottomNav(items: NavItem[]): NavItem[] {
  return items.filter(i => i.key !== 'administration');
}
