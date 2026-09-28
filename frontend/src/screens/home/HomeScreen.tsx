import { Link } from 'react-router-dom';
import type { ScheduleEvent } from '../../api/types';
import { Avatar, Badge, EmptyState, ErrorState, Skeleton } from '../../components/ui';
import { IconCalendarEmpty } from '../../components/icons/ui';
import { useAsync } from '../../hooks/useAsync';
import { useProfilesApi, useScheduleApi } from '../../hooks/useServices';
import { PROFILE_LABEL, useInstitution } from '../../state/institution';
import { useSession } from '../../state/session';
import { findMeetingLink } from '../../utils/links';
import { addDays, dayKey, formatDayTitle, formatRange, now, startOfDay } from '../../utils/time';
import { humanMessage } from '../../api/http';
import s from './home.module.css';

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(' ');

/** Группа студента: общая для всех его занятий (у студента максимум одна группа — spec расписания). */
function studentGroupId(events: ScheduleEvent[]): string | null {
  if (!events.length) return null;
  const common = events.slice(1).reduce((acc, e) => acc.filter(g => e.group_ids.includes(g)), events[0].group_ids);
  return common.length === 1 ? common[0] : null;
}

function useHomeSchedule() {
  const schedule = useScheduleApi();
  const { profile } = useInstitution();
  return useAsync(async signal => {
    if (!schedule) return null;
    const today = startOfDay(now());
    const events = await schedule.api.listEvents({ from: today.toISOString(), to: addDays(today, 14).toISOString() }, signal);
    // День для карточки: сегодня, иначе ближайший учебный день в пределах недели.
    const upcoming = events.filter(e => Date.parse(e.ends_at) > now().getTime() || dayKey(e.starts_at) === dayKey(today));
    const firstDay = upcoming.find(e => Date.parse(e.starts_at) < addDays(today, 7).getTime());
    const key = firstDay ? dayKey(firstDay.starts_at) : null;
    const dayEvents = key ? events.filter(e => dayKey(e.starts_at) === key) : [];
    let groupName: string | null = null;
    if (profile === 'student') {
      const gid = studentGroupId(events);
      if (gid) groupName = (await schedule.api.listGroups(signal).catch(() => [])).find(g => g.id === gid)?.name ?? null;
    }
    return { day: key ? new Date(dayEvents[0].starts_at) : null, isToday: key === dayKey(today), events: dayEvents, groupName };
  }, [schedule, profile]);
}

export function HomeScreen() {
  const { maxUser } = useSession();
  const { institution, profile, catalog } = useInstitution();
  const profiles = useProfilesApi();
  const scheduleApi = useScheduleApi();
  const base = `/institution/${institution.id}`;

  const me = useAsync(async signal => (profiles ? (await profiles.api.getMe(signal)).data : null), [profiles]);
  const schedule = useHomeSchedule();

  const displayName = me.data?.display_name
    || [maxUser?.first_name, maxUser?.last_name].filter(Boolean).join(' ')
    || 'Пользователь';
  const firstName = maxUser?.first_name || displayName.split(' ')[0];
  const catalogReady = catalog.status === 'ready';
  const loadingCard = !catalogReady || me.status === 'loading';
  const meta = [PROFILE_LABEL[profile], schedule.data?.groupName && `Группа ${schedule.data.groupName}`, me.data?.position]
    .filter(Boolean).join(' · ');

  const cardInner = (
    <>
      <Avatar name={displayName} src={maxUser?.photo_url} size="var(--home-avatar)" className={s.photo} />
      <div className={s.profileText}>
        {loadingCard ? (
          <>
            <Skeleton width="60%" height="2.8rem" />
            <Skeleton width="80%" height="2.6rem" className={s.meta} />
            <Skeleton width="70%" height="2rem" className={s.uni} />
          </>
        ) : (
          <>
            <p className={s.name}>{displayName}</p>
            <p className={s.meta}>{meta}</p>
            <p className={s.uni}>{institution.display_name}</p>
          </>
        )}
      </div>
    </>
  );

  return (
    <div>
      <h1 className={s.greeting}>Привет, {firstName}!</h1>
      <p className={s.subtitle}>Хорошего дня и продуктивной учебы!</p>

      {profiles
        ? <Link to={`${base}/users/me`} className={s.profileCard} aria-label={`Мой профиль: ${displayName}`}>{cardInner}</Link>
        : <div className={s.profileCard}>{cardInner}</div>}

      {(!catalogReady || scheduleApi) && (
        <section className={s.dayCard} aria-label="Расписание на день">
          <TodaySchedule state={schedule} base={base} />
        </section>
      )}
    </div>
  );
}

function TodaySchedule({ state, base }: { state: ReturnType<typeof useHomeSchedule>; base: string }) {
  if (state.status === 'error') {
    return <ErrorState title="Не удалось загрузить расписание" text={humanMessage(state.error)} onRetry={state.reload} />;
  }
  if (state.status === 'loading' || !state.data) {
    return (
      <div aria-busy="true" aria-label="Загрузка расписания">
        <Skeleton width="55%" height="2.8rem" />
        <div className={s.lessons}>
          {[0, 1, 2].map(i => (
            <div key={i} className={s.skeletonRow}><Skeleton height="2.4rem" /><Skeleton height="2.4rem" /><Skeleton height="2.4rem" /></div>
          ))}
        </div>
      </div>
    );
  }
  const { day, isToday, events } = state.data;
  if (!day) {
    return (
      <EmptyState icon={<IconCalendarEmpty />} title="Сегодня занятий нет" text="На ближайшую неделю занятия тоже не запланированы.">
        <Link to={`${base}/schedule`} className={s.more}>Открыть расписание</Link>
      </EmptyState>
    );
  }
  const t = now().getTime();
  return (
    <>
      <h2 className={s.dayTitle}>{formatDayTitle(day)}</h2>
      {!isToday && <p className={s.dayCaption}>Сегодня занятий нет — ближайший учебный день</p>}
      <ul className={s.lessons}>
        {events.map(e => {
          const link = findMeetingLink(e.description, e.location);
          const cancelled = e.status === 'cancelled';
          const current = !cancelled && Date.parse(e.starts_at) <= t && t < Date.parse(e.ends_at);
          return (
            <li key={e.id} className={cx(s.lesson, cancelled && s.cancelled, current && s.now)}>
              <span className={s.time}>{formatRange(e.starts_at, e.ends_at)}</span>
              <span className={s.subject}>
                {e.title}
                {cancelled && <span className={s.tag}><Badge tone="error">Отменено</Badge></span>}
                {current && <span className={s.tag}><Badge tone="accent" dot>Идёт сейчас</Badge></span>}
              </span>
              <span className={cx(s.room, !e.location && link && s.online)}>{e.location || (link ? 'Онлайн' : '—')}</span>
            </li>
          );
        })}
      </ul>
    </>
  );
}
