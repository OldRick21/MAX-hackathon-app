import { useEffect, useState, type CSSProperties } from 'react';
import type { WidgetView } from '../../api/types';
import { serviceEntries } from '../../components/layout/navigation';
import { Button, Skeleton, toast } from '../../components/ui';
import { useAsync } from '../../hooks/useAsync';
import { useInstitution } from '../../state/institution';
import { useBackend, useSession } from '../../state/session';
import { formatShort, formatWeekday, now } from '../../utils/time';
import s from './home.module.css';
import { WidgetCard } from './widgets';
import { PrivacySettings } from '../entry/Privacy';

/** Колонки сетки главной: 4 на широком экране, 2 на среднем, 1 на телефоне (WIDGETS_SPEC.md §8). */
const COLUMN_QUERIES: [string, number][] = [['(min-width: 1100px)', 4], ['(min-width: 768px)', 2]];
function useColumns() {
  const read = () => COLUMN_QUERIES.find(([q]) => window.matchMedia(q).matches)?.[1] ?? 1;
  const [columns, setColumns] = useState(read);
  useEffect(() => {
    const lists = COLUMN_QUERIES.map(([q]) => window.matchMedia(q));
    const update = () => setColumns(read());
    lists.forEach(l => l.addEventListener('change', update));
    return () => lists.forEach(l => l.removeEventListener('change', update));
  }, []);
  return columns;
}

/** Ширина виджета в колонках: wide — половина строки (на двух колонках — вся), small — одна колонка. */
const spanOf = (w: WidgetView, columns: number) => (w.size === 'wide' ? (columns >= 4 ? columns / 2 : columns) : 1);

export function HomeScreen() {
  const backend = useBackend();
  const { maxUser, user } = useSession();
  const { institution, profile, catalog } = useInstitution();
  const base = `/institution/${institution.id}`;

  // Обновление при открытии главной и возврате в приложение; периодического опроса нет.
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const refresh = () => { if (document.visibilityState === 'visible') setTick(t => t + 1); };
    document.addEventListener('visibilitychange', refresh);
    window.addEventListener('focus', refresh);
    return () => { document.removeEventListener('visibilitychange', refresh); window.removeEventListener('focus', refresh); };
  }, []);

  const widgets = useAsync(signal => backend.listWidgets(institution.id, profile, signal), [backend, institution.id, profile, tick]);
  const services = catalog.status === 'ready' ? catalog.value : [];
  const entries = serviceEntries(base, services);
  const routeOf = (w: WidgetView) => {
    if (!w.open_menu) return null;
    const entry = entries.find(e => e.key === `${w.service_id}:${w.open_menu}`);
    if (!entry) return null;
    // Меню «Главная» сервиса «Люди» — своя анкета: встроенный экран открывает её по /users/me
    // (в боковом меню этот сервис ведёт к списку участников).
    if (w.service_type === 'user-profile' && w.open_menu === 'home') return `${base}/users/me`;
    return entry.to;
  };

  const list = widgets.data ?? [];
  // Профиль — всегда первым (§8).
  const ordered = [...list.filter(w => w.kind === 'profile').slice(0, 1), ...list.filter(w => w.kind !== 'profile')];
  const columns = useColumns();

  const firstName = maxUser?.first_name || 'Пользователь';
  const today = now();

  return (
    <div>
      <header className={s.head}>
        <h1 className={s.greeting}>Привет, {firstName}!</h1>
        <p className={s.subtitle}>{formatWeekday(today)}, {formatShort(today)} · {institution.display_name}</p>
      </header>

      {widgets.status === 'loading' && !widgets.data ? (
        <div className={s.widgets} style={{ '--columns': columns } as CSSProperties}>
          <div className={s.widget} style={{ gridColumn: `span ${Math.min(2, columns)}` }}><Skeleton width="40%" height="2.4rem" /><Skeleton width="70%" height="2rem" /></div>
          <div className={s.widget}><Skeleton width="60%" height="2.4rem" /></div>
          <div className={s.widget}><Skeleton width="60%" height="2.4rem" /></div>
        </div>
      ) : widgets.status === 'error' && !widgets.data ? (
        <p className={s.caption}>Не удалось загрузить главную. <Button variant="ghost" size="small" onClick={widgets.reload}>Повторить</Button></p>
      ) : ordered.length ? (
        <div className={s.widgets} style={{ '--columns': columns } as CSSProperties}>
          {ordered.map((w, i) => (
            // Профиль — отдельной строкой сверху (не шире половины), остальные виджеты — со следующей строки.
            <WidgetCard key={`${w.service_id}:${w.widget_id}`} widget={w} span={spanOf(w, columns)} to={routeOf(w)} tick={tick}
              newRow={i === 1 && ordered[0].kind === 'profile'} />
          ))}
        </div>
      ) : (
        <div className={s.empty}>
          <p className={s.caption}>Виджетов на главной пока нет. Разделы вуза — на вкладке «Сервисы».</p>
          {user && (
            <p className={s.caption}>
              Ваш ID: <code>{user.id}</code>{' '}
              <Button variant="ghost" size="small" onClick={() => navigator.clipboard?.writeText(user.id)
                .then(() => toast('ID скопирован'), () => toast('Не удалось скопировать', true))}>Скопировать</Button>
            </p>
          )}
        </div>
      )}

      <PrivacySettings />
    </div>
  );
}
