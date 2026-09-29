import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { WidgetView } from '../../api/types';
import { serviceEntries, type ServiceEntry } from '../../components/layout/navigation';
import { Button, Skeleton, toast } from '../../components/ui';
import { useAsync } from '../../hooks/useAsync';
import { useInstitution } from '../../state/institution';
import { useBackend, useSession } from '../../state/session';
import { formatShort, formatWeekday, now } from '../../utils/time';
import s from './home.module.css';
import { WidgetCard } from './widgets';

type Cell = { kind: 'widget'; widget: WidgetView } | { kind: 'filler'; entry: ServiceEntry };

/**
 * Раскладка главной (WIDGETS_SPEC.md §8): на широком экране две колонки, wide — во всю строку.
 * Если small остаётся в строке один, свободную половину занимает ярлык облачного сервиса
 * (только на десктопе; на телефоне одна колонка и ярлыков нет).
 */
function layout(widgets: WidgetView[], fillers: ServiceEntry[]): Cell[] {
  const cells: Cell[] = [];
  const spare = [...fillers];
  let open = false;
  const hole = () => { const entry = spare.shift(); if (entry) cells.push({ kind: 'filler', entry }); open = false; };
  for (const widget of widgets) {
    if (widget.size === 'wide') {
      if (open) hole();
      cells.push({ kind: 'widget', widget });
    } else {
      cells.push({ kind: 'widget', widget });
      open = !open;
    }
  }
  if (open) hole();
  return cells;
}

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
  const routeOf = (w: WidgetView) => (w.open_menu ? entries.find(e => e.key === `${w.service_id}:${w.open_menu}`)?.to ?? null : null);

  const list = widgets.data ?? [];
  // Профиль — всегда первым (§8).
  const ordered = [...list.filter(w => w.kind === 'profile').slice(0, 1), ...list.filter(w => w.kind !== 'profile')];
  const onHome = new Set(ordered.map(w => w.service_id));
  const cloud = new Set(services.filter(x => x.deployment === 'cloud').map(x => x.id));
  const fillers = entries.filter(e => cloud.has(e.key.split(':')[0]) && !onHome.has(e.key.split(':')[0]));

  const firstName = maxUser?.first_name || 'Пользователь';
  const today = now();

  return (
    <div>
      <header className={s.head}>
        <h1 className={s.greeting}>Привет, {firstName}!</h1>
        <p className={s.subtitle}>{formatWeekday(today)}, {formatShort(today)} · {institution.display_name}</p>
      </header>

      {widgets.status === 'loading' && !widgets.data ? (
        <div className={s.widgets}>
          <div className={`${s.widget} ${s.wide}`}><Skeleton width="40%" height="2.4rem" /><Skeleton width="70%" height="2rem" /></div>
          <div className={s.widget}><Skeleton width="60%" height="2.4rem" /></div>
          <div className={s.widget}><Skeleton width="60%" height="2.4rem" /></div>
        </div>
      ) : widgets.status === 'error' && !widgets.data ? (
        <p className={s.caption}>Не удалось загрузить главную. <Button variant="ghost" size="small" onClick={widgets.reload}>Повторить</Button></p>
      ) : ordered.length ? (
        <div className={s.widgets}>
          {layout(ordered, fillers).map(cell => cell.kind === 'widget'
            ? <WidgetCard key={`${cell.widget.service_id}:${cell.widget.widget_id}`} widget={cell.widget} to={routeOf(cell.widget)} tick={tick} />
            : (
              <Link key={cell.entry.key} to={cell.entry.to} className={s.filler}>
                <span className={s.tileIcon}><cell.entry.Icon /></span>
                <span className={s.tileText}><span className={s.tileName}>{cell.entry.name}</span><span className={s.tileDesc}>{cell.entry.desc}</span></span>
              </Link>
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
    </div>
  );
}
