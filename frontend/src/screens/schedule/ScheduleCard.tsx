import type { ScheduleEvent } from '../../api/types';
import { IconDots, IconVideo } from '../../components/icons/ui';
import { Badge, Button, Dropdown, IconButton, type DropdownItem } from '../../components/ui';
import { findMeetingLink } from '../../utils/links';
import { formatTime, now } from '../../utils/time';
import s from './schedule.module.css';

const cx = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(' ');

export interface ScheduleCardProps {
  event: ScheduleEvent;
  /** null — имена ещё загружаются */
  teacherNames: string[] | null;
  groupNames: string[];
  showGroups: boolean;
  onJoin: (url: string) => void;
  actions?: DropdownItem[];
}

export function ScheduleCard({ event: e, teacherNames, groupNames, showGroups, onJoin, actions }: ScheduleCardProps) {
  const t = now().getTime();
  const cancelled = e.status === 'cancelled';
  const current = !cancelled && Date.parse(e.starts_at) <= t && t < Date.parse(e.ends_at);
  const link = findMeetingLink(e.description, e.location);
  const mentionsOnline = /онлайн|online/i.test(`${e.location} ${e.description}`);
  const online = !!link || mentionsOnline;
  const description = link ? e.description.replace(link, '').replace(/(Ссылка|Подключиться)\s*:?\s*$/i, '').trim() : e.description.trim();

  return (
    <article className={cx(s.card, current && s.current, cancelled && s.cancelled)} aria-label={`${formatTime(e.starts_at)} ${e.title}`}>
      <div className={s.times}>
        <span className={s.start}>{formatTime(e.starts_at)}</span>
        <span className={s.end}>до {formatTime(e.ends_at)}</span>
      </div>
      <div className={s.body}>
        <h3 className={s.title}>{e.title}</h3>
        <div className={s.meta}>
          <span className={s.metaItem}>{!teacherNames ? '…' : teacherNames.length ? teacherNames.join(', ') : 'Преподаватель не указан'}</span>
          <span className={s.metaItem}>
            {e.location ? <strong>{e.location.startsWith('Ауд') ? e.location : `Ауд. ${e.location}`}</strong>
              : online ? <strong>Онлайн</strong> : 'Аудитория не указана'}
          </span>
          {showGroups && groupNames.length > 0 && <span className={s.metaItem}>{groupNames.join(', ')}</span>}
        </div>
        {description && <p className={s.description}>{description}</p>}
        {(current || cancelled || online) && (
          <div className={s.badges}>
            {current && <Badge tone="accent" dot>Идёт сейчас</Badge>}
            {cancelled && <Badge tone="error">Занятие отменено</Badge>}
            {online && !cancelled && <Badge tone="info">Онлайн</Badge>}
          </div>
        )}
        {online && !cancelled && (
          link
            ? <div><Button size="small" className={s.join} icon={<IconVideo width="1.2em" height="1.2em" />} onClick={() => onJoin(link)}>Подключиться</Button></div>
            : <p className={s.noLink}>Ссылка на занятие пока не добавлена</p>
        )}
      </div>
      {actions && actions.length > 0 && (
        <div className={s.actions}>
          <Dropdown
            label="Действия с занятием"
            align="end"
            items={actions}
            trigger={({ toggle, ref, ...aria }) => (
              <IconButton ref={ref} label="Действия с занятием" onClick={toggle} {...aria}><IconDots /></IconButton>
            )}
          />
        </div>
      )}
    </article>
  );
}
