import { Link } from 'react-router-dom';
import { IconServices } from '../../components/icons/figma';
import { serviceEntries, type ServiceEntry } from '../../components/layout/navigation';
import { EmptyState, ErrorState, LoadingState } from '../../components/ui';
import { useInstitution } from '../../state/institution';
import p from '../pages.module.css';
import s from './services.module.css';

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
        const list = serviceEntries(base, catalog.value);
        if (!list.length) {
          return <EmptyState icon={<IconServices />} title="Для текущего профиля сервисы недоступны"
            text={institution.status === 'active' ? 'Попробуйте сменить профиль или обратитесь к администратору вуза.' : 'Вуз временно отключён от платформы.'} />;
        }
        return (
          <ul className={s.grid} style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {list.map(({ key, to, name, desc, Icon }) => (
              <li key={key}>
                <ServiceCard to={to} name={name} desc={desc} Icon={Icon} />
              </li>
            ))}
          </ul>
        );
      })()}
    </div>
  );
}

export function ServiceCard({ to, name, desc, Icon }: Pick<ServiceEntry, 'to' | 'name' | 'desc' | 'Icon'>) {
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
