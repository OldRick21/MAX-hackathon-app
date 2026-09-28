import type { ComponentType, SVGProps } from 'react';
import { Link } from 'react-router-dom';
import type { ServiceView } from '../../api/types';
import { IconCoursework, IconSchedule, IconServices, IconUsers } from '../../components/icons/figma';
import { IconBuilding, IconGrid } from '../../components/icons/ui';
import { EmptyState, ErrorState, LoadingState } from '../../components/ui';
import { NATIVE_MENUS, useInstitution } from '../../state/institution';
import p from '../pages.module.css';
import s from './services.module.css';

interface Entry {
  key: string;
  to: string;
  name: string;
  desc: string;
  Icon: ComponentType<SVGProps<SVGSVGElement>>;
}

const NATIVE: Record<string, { section: string; name: string; desc: string; Icon: Entry['Icon'] }> = {
  // Один сервис — одна карточка: оба меню сервиса «Люди» ведут в «Люди», профиль открывается внутри.
  home: { section: 'users', name: 'Люди', desc: 'Ваш профиль и участники вуза', Icon: IconUsers },
  users: { section: 'users', name: 'Люди', desc: 'Ваш профиль и участники вуза', Icon: IconUsers },
  schedule: { section: 'schedule', name: 'Расписание', desc: 'Занятия на неделю', Icon: IconSchedule },
  schedule_admin: { section: 'schedule', name: 'Расписание', desc: 'Все группы и редактирование занятий', Icon: IconSchedule },
  coursework: { section: 'coursework', name: 'Курсовые работы', desc: 'Загрузка и проверка работ', Icon: IconCoursework },
  coursework_admin: { section: 'coursework', name: 'Курсовые работы', desc: 'Все работы вуза', Icon: IconCoursework },
};

function entries(base: string, services: ServiceView[]): Entry[] {
  const seen = new Set<string>();
  return services.flatMap(svc => svc.menus.flatMap(menu => {
    const native = Object.values(NATIVE_MENUS).some(d => d.type === svc.service_type && (d.menus as readonly string[]).includes(menu.id)) ? NATIVE[menu.id] : undefined;
    if (native) {
      if (seen.has(native.section)) return [];
      seen.add(native.section);
      return [{ key: `${svc.id}:${menu.id}`, to: native.section ? `${base}/${native.section}` : base, name: native.name, desc: native.desc, Icon: native.Icon }];
    }
    return [{
      key: `${svc.id}:${menu.id}`,
      to: `${base}/service/${svc.id}/${menu.id}`,
      name: menu.display_name,
      desc: svc.deployment === 'local' ? 'Сервис вуза' : svc.display_name !== menu.display_name ? svc.display_name : 'Сервис платформы',
      Icon: svc.service_type === 'administration' ? IconBuilding : IconGrid,
    }];
  }));
}

export function AllServicesScreen() {
  const { institution, catalog, reloadCatalog } = useInstitution();
  const base = `/institution/${institution.id}`;
  return (
    <div>
      <div className={p.pageHead}>
        <div>
          <h1 className={p.title}>Все сервисы</h1>
          <p className={p.subtitle}>Доступные вам сервисы вуза</p>
        </div>
      </div>
      {catalog.status === 'loading' ? <LoadingState /> : catalog.status === 'error' ? (
        <ErrorState title="Не удалось загрузить сервисы" text={catalog.message} onRetry={reloadCatalog} />
      ) : (() => {
        const list = entries(base, catalog.value);
        if (!list.length) {
          return <EmptyState icon={<IconServices />} title="Для текущего профиля сервисы недоступны"
            text={institution.status === 'active' ? 'Попробуйте сменить профиль или обратитесь к администратору вуза.' : 'Вуз временно отключён от платформы.'} />;
        }
        return (
          <ul className={s.grid} style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {list.map(({ key, ...e }) => (
              <li key={key}>
                <ServiceCard {...e} />
              </li>
            ))}
          </ul>
        );
      })()}
    </div>
  );
}

export function ServiceCard({ to, name, desc, Icon }: Omit<Entry, 'key'>) {
  return (
    <Link to={to} className={s.card}>
      <span className={s.icon}><Icon strokeWidth={2.4} /></span>
      <span className={s.text}>
        <span className={s.name} style={{ display: 'block' }}>{name}</span>
        <span className={s.desc} style={{ display: 'block' }}>{desc}</span>
      </span>
    </Link>
  );
}
