import { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import type { ScheduleApi } from '../../api/backend';
import { humanMessage } from '../../api/http';
import type { ScheduleEvent, ServiceView } from '../../api/types';
import { SectionGate } from '../../components/SectionGate';
import { IconArrowLeft, IconArrowRight, IconCalendarEmpty, IconPlus } from '../../components/icons/ui';
import { Badge, Button, EmptyState, ErrorState, IconButton, Modal, Select, Skeleton, toast, type DropdownItem } from '../../components/ui';
import { useAsync } from '../../hooks/useAsync';
import { useProfilesApi } from '../../hooks/useServices';
import { useUserNames } from '../../hooks/useUserNames';
import { useInstitution } from '../../state/institution';
import { useBackend } from '../../state/session';
import { addDays, dayKey, formatShort, formatWeekday, now, startOfWeek } from '../../utils/time';
import p from '../pages.module.css';
import { EventEditor } from './EventEditor';
import { ScheduleCard } from './ScheduleCard';
import s from './schedule.module.css';

export function ScheduleScreen() {
  return <SectionGate section="schedule">{service => <Schedule service={service} />}</SectionGate>;
}

type Editing = { event: ScheduleEvent | null; etag: string | null } | null;

function Schedule({ service }: { service: ServiceView }) {
  const backend = useBackend();
  const { institution } = useInstitution();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const api: ScheduleApi = useMemo(() => backend.schedule(service), [backend, service]);
  const profiles = useProfilesApi();
  const [weekOffset, setWeekOffset] = useState(0);
  // Выбранная группа живёт в адресе (?group=): на неё ведёт «Расписание группы» со страницы групп.
  const groupId = params.get('group') ?? '';
  const setGroupId = (id: string) => setParams(id ? { group: id } : {}, { replace: true });
  const [editing, setEditing] = useState<Editing>(null);
  const [deleting, setDeleting] = useState<ScheduleEvent | null>(null);
  const [opening, setOpening] = useState(false);

  // Смотреть может любой участник; менять — только «Редактор расписания» (админ или преподаватель).
  const canWrite = service.permissions.includes('schedule.write');
  const isTeacher = service.profile === 'teacher';
  const weekStart = addDays(startOfWeek(now()), weekOffset * 7);
  const weekEnd = addDays(weekStart, 7);

  const events = useAsync(signal => api.listEvents({
    from: weekStart.toISOString(), to: weekEnd.toISOString(), group_id: groupId || undefined,
  }, signal), [api, weekStart.getTime(), groupId]);
  // Группы — из справочника ядра: там же своя группа студента.
  const directory = useAsync(signal => backend.listGroups(institution.id, service.profile, signal), [backend, institution.id, service.profile]);
  const groups = { status: directory.status, data: directory.data?.items };
  const myGroups = (directory.data?.items ?? []).filter(g => directory.data?.my_group_ids.includes(g.id));
  const mineLabel = service.profile === 'student' ? 'Моя группа' : isTeacher ? 'Мои занятия' : 'Все группы';

  const list = events.data ?? [];
  const teacherNames = useUserNames(profiles?.api ?? null, profiles?.service.id ?? '', list.flatMap(e => e.teacher_ids));
  const groupName = useMemo(() => new Map((groups.data ?? []).map(g => [g.id, g.name])), [groups.data]);

  const byDay = useMemo(() => {
    const map = new Map<string, ScheduleEvent[]>();
    for (const e of list) {
      const k = dayKey(e.starts_at);
      map.set(k, [...(map.get(k) ?? []), e]);
    }
    return [...map.entries()];
  }, [list]);

  const openEditor = async (event: ScheduleEvent | null) => {
    if (!event) return setEditing({ event: null, etag: null });
    setOpening(true);
    try {
      const fresh = await api.getEvent(event.id);
      setEditing({ event: fresh.data, etag: fresh.etag });
    } catch (e) {
      toast(humanMessage(e), true);
    } finally { setOpening(false); }
  };

  const actionsFor = (_e: ScheduleEvent): DropdownItem[] | undefined => canWrite ? [
    { key: 'edit', label: 'Изменить', onSelect: () => void openEditor(_e) },
    { key: 'delete', label: 'Удалить', danger: true, onSelect: () => setDeleting(_e) },
  ] : undefined;

  const todayKey = dayKey(now());
  const last = addDays(weekEnd, -1);
  const [d1, m1] = formatShort(weekStart).split(' ');
  const [d2, m2] = formatShort(last).split(' ');
  const weekLabel = m1 === m2 ? `${d1}–${d2} ${m2}` : `${d1} ${m1} – ${d2} ${m2}`;

  return (
    <div>
      <div className={p.pageHead}>
        <div>
          <h1 className={p.title}>Расписание</h1>
          <p className={p.subtitle}>{weekOffset === 0 ? 'Текущая неделя' : weekOffset === 1 ? 'Следующая неделя' : weekOffset === -1 ? 'Прошлая неделя' : weekLabel}</p>
          {service.profile === 'student' && directory.status === 'ready' && (
            <p className={s.myGroup}>{myGroups[0] ? <>Моя группа: <strong>{myGroups[0].name}</strong></> : 'Группа пока не назначена — обратитесь к куратору'}</p>
          )}
        </div>
        <div className={p.headActions}>
          <Button variant="secondary" onClick={() => navigate('groups')}>Группы</Button>
          {canWrite && <Button icon={<IconPlus width="1.2em" height="1.2em" />} onClick={() => void openEditor(null)} loading={opening}>Добавить занятие</Button>}
        </div>
      </div>

      <div className={s.toolbar}>
        <div className={s.weekNav}>
          <IconButton label="Предыдущая неделя" className={s.navBtn} onClick={() => setWeekOffset(w => w - 1)}><IconArrowLeft /></IconButton>
          <span className={s.weekLabel} aria-live="polite">{weekLabel}</span>
          <IconButton label="Следующая неделя" className={s.navBtn} onClick={() => setWeekOffset(w => w + 1)}><IconArrowRight /></IconButton>
        </div>
        {weekOffset !== 0 && <Button variant="ghost" size="small" onClick={() => setWeekOffset(0)}>К текущей неделе</Button>}
        <span className={s.spacer} />
        {(groups.data?.length ?? 0) > 0 && (
          <Select aria-label="Группа" className={s.groupFilter} value={groupId} onChange={e => setGroupId(e.target.value)}>
            <option value="">{mineLabel}</option>
            {groups.data!.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
          </Select>
        )}
      </div>

      {events.status === 'error' ? (
        <ErrorState title="Не удалось загрузить расписание" text={humanMessage(events.error)} onRetry={events.reload} />
      ) : events.status === 'loading' ? (
        <div className={s.days} aria-busy="true" aria-label="Загрузка расписания">
          {[0, 1].map(d => (
            <div key={d} className={s.day}>
              <Skeleton width="32rem" height="2.6rem" />
              <Skeleton className={s.skeletonCard} />
              <Skeleton className={s.skeletonCard} />
            </div>
          ))}
        </div>
      ) : byDay.length === 0 ? (
        <EmptyState icon={<IconCalendarEmpty />} title={weekOffset === 0 ? 'На этой неделе занятий нет' : 'На эту неделю занятий нет'}
          text={service.profile === 'student' ? 'Если занятия должны быть, проверьте у куратора, что вас добавили в группу.'
            : canWrite ? 'Добавьте занятие кнопкой «Добавить занятие» — его увидят студенты выбранных групп.' : undefined} />
      ) : (
        <div className={s.days}>
          {byDay.map(([key, dayEvents]) => {
            const date = new Date(dayEvents[0].starts_at);
            return (
              <section key={key} className={s.day} aria-label={formatWeekday(date)}>
                <h2 className={s.dayHead}>
                  {formatWeekday(date)}, {formatShort(date)}
                  {key === todayKey && <Badge tone="accent">Сегодня</Badge>}
                </h2>
                <ul className={s.list}>
                  {dayEvents.map(e => (
                    <li key={e.id}>
                      <ScheduleCard
                        event={e}
                        teacherNames={e.teacher_ids.some(id => teacherNames[id] === undefined) ? null : e.teacher_ids.map(id => teacherNames[id]).filter((n): n is string => !!n)}
                        groupNames={e.group_ids.map(id => groupName.get(id) ?? '').filter(Boolean)}
                        showGroups={service.profile !== 'student' || !!groupId}
                        onJoin={backend.openLink}
                        actions={actionsFor(e)}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      )}

      {editing && (
        <EventEditor
          api={api}
          profiles={profiles?.api ?? null}
          groups={groups.data ?? []}
          initial={editing.event}
          etag={editing.etag}
          lockedTeacher={null}
          defaultDate={dayKey(weekOffset === 0 ? now() : weekStart)}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); events.reload(); }}
        />
      )}
      {deleting && <DeleteEvent api={api} event={deleting} onClose={() => setDeleting(null)} onDone={() => { setDeleting(null); events.reload(); }} />}
    </div>
  );
}

function DeleteEvent({ api, event, onClose, onDone }: { api: ScheduleApi; event: ScheduleEvent; onClose: () => void; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const confirm = async () => {
    setBusy(true);
    try {
      const { etag } = await api.getEvent(event.id);
      await api.deleteEvent(event.id, etag);
      toast('Занятие удалено');
      onDone();
    } catch (e) {
      toast(humanMessage(e), true);
      setBusy(false);
    }
  };
  return (
    <Modal title="Удалить занятие?" onClose={onClose} busy={busy} actions={<>
      <Button variant="secondary" onClick={onClose} disabled={busy}>Отмена</Button>
      <Button variant="danger" onClick={confirm} loading={busy}>Удалить</Button>
    </>}>
      <p className={p.muted} style={{ fontSize: 'inherit' }}>
        «{event.title}» пропадёт из расписания у всех. Чтобы сообщить студентам об отмене, лучше отметьте занятие как отменённое.
      </p>
    </Modal>
  );
}
