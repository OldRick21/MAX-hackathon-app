// Карточки виджетов главной: оболочка рисует их сама по шаблону, данные даёт сервис
// (docs/services/sdk/WIDGETS_SPEC.md §7–§8). HTML сервиса на главную не попадает.
import { Link } from 'react-router-dom';
import { ApiError } from '../../api/http';
import type { Tone, WidgetData, WidgetView } from '../../api/types';
import { Avatar, Skeleton } from '../../components/ui';
import { useAsync } from '../../hooks/useAsync';
import { useAvatar } from '../../hooks/useAvatar';
import { useInstitution } from '../../state/institution';
import { useBackend } from '../../state/session';
import { dayKey, formatShort, formatTime, formatWeekday, now } from '../../utils/time';
import s from './home.module.css';

const toneClass = (tone?: Tone) => (tone && tone !== 'normal' ? s[`tone_${tone}`] : undefined);
const cx = (...names: (string | false | undefined)[]) => names.filter(Boolean).join(' ');

/** 403/404/204 — карточку скрыть (виджет недоступен этому пользователю); остальное — «Не удалось загрузить». */
const hidden = (e: unknown) => e instanceof ApiError && (e.status === 403 || e.status === 404);

export function WidgetCard({ widget, to, tick, span }: { widget: WidgetView; to: string | null; tick: number; span: number }) {
  const backend = useBackend();
  const { institution, profile } = useInstitution();
  const state = useAsync(signal => backend.widgetData(institution.id, profile, widget, signal),
    [backend, institution.id, profile, widget.service_id, widget.widget_id, widget.data_url, tick]);

  if (state.status === 'ready' && state.data === null) return null;
  if (state.status === 'error' && hidden(state.error)) return null;

  const data = state.data ?? null;
  const body = !data
    ? state.status === 'error'
      ? <p className={s.caption}>Не удалось загрузить</p>
      : <div className={s.skeleton}><Skeleton width="60%" height="2.4rem" /><Skeleton width="85%" height="2rem" /><Skeleton width="40%" height="2rem" /></div>
    : <WidgetBody data={data} title={widget.display_name} />;
  const className = cx(s.widget, data?.kind === 'profile' && s.profileWidget);
  const style = { gridColumn: `span ${span}` };
  const content = (
    <>
      {data?.kind !== 'profile' && <h2 className={s.widgetTitle}>{data?.kind === 'events' ? eventsTitle(data.day) : widget.display_name}</h2>}
      {body}
    </>
  );
  return to
    ? <Link to={to} className={cx(className, s.clickable)} style={style} aria-label={widget.display_name}>{content}</Link>
    : <section className={className} style={style} aria-label={widget.display_name}>{content}</section>;
}

function eventsTitle(day: string | null) {
  if (!day) return 'Занятия';
  if (day === dayKey(now())) return 'Сегодня';
  const d = new Date(`${day}T12:00:00`);
  return `${formatWeekday(d)}, ${formatShort(d)}`;
}

function WidgetBody({ data, title }: { data: WidgetData; title: string }) {
  switch (data.kind) {
    case 'profile': return <ProfileBody data={data} />;
    case 'events': return <EventsBody data={data} />;
    case 'list':
      return data.items.length ? (
        <>
          <ul className={s.list}>
            {data.items.map((item, i) => (
              <li key={i} className={s.listItem}>
                <span className={s.listText}><span className={s.listTitle}>{item.title}</span>
                  {item.subtitle && <span className={s.listSub}>{item.subtitle}</span>}</span>
                {item.badge && <span className={cx(s.badge, toneClass(item.tone))}>{item.badge}</span>}
              </li>
            ))}
          </ul>
          {data.total !== undefined && data.total > data.items.length && <p className={s.caption}>Ещё {data.total - data.items.length}</p>}
        </>
      ) : <p className={s.caption}>{data.empty_text || 'Пока пусто'}</p>;
    case 'stat':
      return (
        <div className={s.stat}>
          <span className={cx(s.statValue, toneClass(data.tone))}>{data.value}</span>
          <span className={s.statText}>{data.unit && <span className={s.statUnit}>{data.unit}</span>}
            {data.caption && <span className={s.caption}>{data.caption}</span>}</span>
        </div>
      );
    case 'progress':
      return <Progress value={data.value} caption={data.caption} hint={data.hint} />;
    case 'notice':
      return (
        <div className={cx(s.notice, toneClass(data.tone))}>
          <p className={s.noticeTitle}>{data.title || title}</p>
          {data.text && <p className={s.caption}>{data.text}</p>}
        </div>
      );
  }
}

function Progress({ value, caption, hint }: { value: number; caption?: string; hint?: string }) {
  const v = Math.max(0, Math.min(100, Math.round(value)));
  return (
    <div className={s.progress}>
      <div className={s.progressHead}>{caption && <span>{caption}</span>}<span className={s.progressValue}>{v}%</span></div>
      <div className={s.bar} role="progressbar" aria-valuenow={v} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${v}%` }} /></div>
      {hint && <p className={s.caption}>{hint}</p>}
    </div>
  );
}

function ProfileBody({ data }: { data: Extract<WidgetData, { kind: 'profile' }> }) {
  const photo = useAvatar(data.user_id);
  const { institution } = useInstitution();
  return (
    <div className={s.profile}>
      <Avatar name={data.title} src={photo} size="var(--home-avatar)" className={s.photo} />
      <div className={s.profileText}>
        <p className={s.name}>{data.title}</p>
        {data.lines.slice(0, 3).map((line, i) => <p key={i} className={s.meta}>{line}</p>)}
        <p className={s.uni}>{institution.display_name}</p>
        {data.progress !== undefined && data.progress < 100 && <Progress value={data.progress} caption="Анкета заполнена" hint={data.hint} />}
      </div>
    </div>
  );
}

// Больше пяти занятий виджет не показывает: иначе он растягивает весь ряд главной. Полный день — по клику.
const EVENTS_SHOWN = 5;

function EventsBody({ data }: { data: Extract<WidgetData, { kind: 'events' }> }) {
  if (!data.items.length) return <p className={s.caption}>{data.empty_text || 'На ближайшую неделю занятий нет'}</p>;
  const t = now().getTime();
  const shown = data.items.slice(0, EVENTS_SHOWN);
  const more = (data.more ?? 0) + data.items.length - shown.length;
  return (
    <>
    <ul className={s.lessons}>
      {shown.map((e, i) => {
        const current = e.status !== 'cancelled' && Date.parse(e.starts_at) <= t && t < Date.parse(e.ends_at);
        return (
          <li key={i} className={cx(s.lesson, e.status === 'cancelled' && s.cancelled, current && s.now)}>
            <span className={s.time}>{formatTime(e.starts_at)}–{formatTime(e.ends_at)}</span>
            <span className={s.subject}>{e.title}</span>
            <span className={s.room}>{e.place || 'онлайн'}</span>
          </li>
        );
      })}
    </ul>
    {more ? <p className={s.caption}>Ещё {more}</p> : null}
    </>
  );
}
