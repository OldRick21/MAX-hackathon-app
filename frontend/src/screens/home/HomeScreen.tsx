import { Link } from 'react-router-dom';
import { serviceEntries } from '../../components/layout/navigation';
import type { ScheduleEvent } from '../../api/types';
import { Avatar, Button, Skeleton, toast } from '../../components/ui';
import { useAsync } from '../../hooks/useAsync';
import { useAvatar } from '../../hooks/useAvatar';
import { useProfilesApi, useScheduleApi } from '../../hooks/useServices';
import { PROFILE_LABEL, useInstitution } from '../../state/institution';
import { useSession } from '../../state/session';
import { addDays, dayKey, formatShort, formatTime, formatWeekday, now, startOfDay } from '../../utils/time';
import s from './home.module.css';


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
  const { maxUser, user } = useSession();
  const { institution, profile, catalog } = useInstitution();
  const profiles = useProfilesApi();
  const base = `/institution/${institution.id}`;

  const me = useAsync(async signal => (profiles ? (await profiles.api.getMe(signal)).data : null), [profiles]);
  const schedule = useHomeSchedule();
  const scheduleApi = useScheduleApi();
  const photo = useAvatar(user?.id);

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
      <Avatar name={displayName} src={photo} size="var(--home-avatar)" className={s.photo} />
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

  const services = catalogReady ? serviceEntries(base, catalog.value) : [];
  const day = schedule.data;
  const today = now();

  return (
    <div>
      <header className={s.head}>
        <h1 className={s.greeting}>Привет, {firstName}!</h1>
        <p className={s.subtitle}>{formatWeekday(today)}, {formatShort(today)}</p>
      </header>

      <div className={s.grid}>
        {profiles
          ? <Link to={`${base}/users/me`} className={s.profileCard} aria-label={`Мой профиль: ${displayName}`}>{cardInner}</Link>
          : <div className={s.profileCard}>{cardInner}</div>}

        {scheduleApi && (
          <section className={s.dayCard} aria-label="Занятия">
            <div className={s.dayHead}>
              <h2 className={s.dayTitle}>{!day?.day ? 'Занятия' : day.isToday ? 'Сегодня' : `${formatWeekday(day.day)}, ${formatShort(day.day)}`}</h2>
              <Link to={`${base}/schedule`} className={s.more}>Всё расписание</Link>
            </div>
            {schedule.status === 'loading' ? (
              <div className={s.lessons}>{[0, 1, 2].map(i => <Skeleton key={i} height="2rem" />)}</div>
            ) : schedule.status === 'error' ? (
              <p className={s.dayCaption}>Не удалось загрузить расписание.</p>
            ) : !day?.events.length ? (
              <p className={s.dayCaption}>На ближайшую неделю занятий нет.</p>
            ) : (
              <ul className={s.lessons}>
                {day.events.map(e => {
                  const t = today.getTime();
                  const current = e.status !== 'cancelled' && Date.parse(e.starts_at) <= t && t < Date.parse(e.ends_at);
                  return (
                    <li key={e.id} className={[s.lesson, e.status === 'cancelled' && s.cancelled, current && s.now].filter(Boolean).join(' ')}>
                      <span className={s.time}>{formatTime(e.starts_at)}–{formatTime(e.ends_at)}</span>
                      <span className={s.subject}>{e.title}</span>
                      <span className={s.room}>{e.location || 'онлайн'}</span>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        )}
      </div>

      {services.length > 0 && (
        <section className={s.services} aria-label="Сервисы">
          <h2 className={s.sectionTitle}>Сервисы</h2>
          <ul className={s.tiles}>
            {services.map(({ key, to, name, desc, Icon }) => (
              <li key={key}>
                <Link to={to} className={s.tile}>
                  <span className={s.tileIcon}><Icon /></span>
                  <span className={s.tileText}><span className={s.tileName}>{name}</span><span className={s.tileDesc}>{desc}</span></span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Без сервиса «Люди» профиля нет — ID для администратора показываем здесь. */}
      {catalogReady && !profiles && user && (
        <p className={s.uni} style={{ marginTop: '1.2rem', wordBreak: 'break-all' }}>
          Ваш ID: <code>{user.id}</code>{' '}
          <Button variant="ghost" size="small" onClick={() => navigator.clipboard?.writeText(user.id)
            .then(() => toast('ID скопирован'), () => toast('Не удалось скопировать', true))}>Скопировать</Button>
        </p>
      )}
    </div>
  );
}
