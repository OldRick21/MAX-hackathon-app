// Создание и изменение занятия — администратор или преподаватель с включённым редактированием (schedule.write).
import { useState, type FormEvent } from 'react';
import type { ProfilesApi, ScheduleApi } from '../../api/backend';
import { humanMessage, isUUID } from '../../api/http';
import type { EventInput, Group, ProfileCard, ScheduleEvent } from '../../api/types';
import { Button, Input, LoadingState, Modal, SearchInput, Select, TextArea, toast } from '../../components/ui';
import { useAsync } from '../../hooks/useAsync';
import { LESSON_TYPES, type LessonType } from '../../utils/lessonTypes';
import { dayKey, formatTime, mskToIso } from '../../utils/time';
import p from '../pages.module.css';
import s from './schedule.module.css';

export function EventEditor({ api, profiles, groups, initial, etag, lockedTeacher, defaultDate, onClose, onSaved }: {
  api: ScheduleApi;
  profiles: ProfilesApi | null;
  groups: Group[];
  initial: ScheduleEvent | null;
  etag: string | null;
  /** Преподаватель, который задаёт своё занятие: он всегда остаётся в списке преподавателей. */
  lockedTeacher: string | null;
  defaultDate: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? '');
  const [groupQuery, setGroupQuery] = useState('');
  const [date, setDate] = useState(initial ? dayKey(initial.starts_at) : defaultDate);
  const [start, setStart] = useState(initial ? formatTime(initial.starts_at) : '09:00');
  const [end, setEnd] = useState(initial ? formatTime(initial.ends_at) : '10:30');
  const [location, setLocation] = useState(initial?.location ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [cancelled, setCancelled] = useState(initial?.status === 'cancelled');
  const [lessonType, setLessonType] = useState<LessonType | ''>(initial?.lesson_type ?? '');
  const [groupIds, setGroupIds] = useState<string[]>(initial?.group_ids ?? []);
  const [teacherIds, setTeacherIds] = useState<string[]>(
    initial?.teacher_ids ?? (lockedTeacher ? [lockedTeacher] : []));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [manualTeacher, setManualTeacher] = useState('');
  // Без сервиса «Люди» список преподавателей недоступен — добавляем по ID, сервер проверит профиль.
  const addTeacher = () => {
    const id = manualTeacher.trim().toLowerCase();
    if (!isUUID(id)) return setError('Введите ID преподавателя: он показан у него в профиле или на главном экране.');
    setError(null);
    setTeacherIds(l => (l.includes(id) ? l : [...l, id]));
    setManualTeacher('');
  };

  // Преподаватели — все страницы «Людей» (в вузе бывает больше 50 человек), только с профилем «Преподаватель»:
  // студента сервер не примет преподавателем занятия (INVALID_REFERENCE).
  const people = useAsync<ProfileCard[]>(async signal => {
    if (!profiles) return [];
    const all: ProfileCard[] = [];
    let cursor: string | null = null;
    for (let page = 0; page < 40; page++) {
      const res: { items: ProfileCard[]; next_cursor: string | null } = await profiles.listUsers('', cursor, signal);
      all.push(...res.items);
      if (!res.next_cursor) break;
      cursor = res.next_cursor;
    }
    return all.filter(c => !c.profiles || c.profiles.includes('teacher'));
  }, [profiles]);

  const toggle = (list: string[], id: string) => (list.includes(id) ? list.filter(x => x !== id) : [...list, id]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!title.trim()) return setError('Укажите название занятия.');
    if (!groupIds.length) return setError('Выберите хотя бы одну группу.');
    if (!teacherIds.length) return setError('Выберите хотя бы одного преподавателя.');
    if (lockedTeacher && !teacherIds.includes(lockedTeacher)) return setError('Вы должны остаться преподавателем занятия.');
    if (!(start < end)) return setError('Время окончания должно быть позже времени начала.');
    const input: EventInput = {
      title: title.trim(), starts_at: mskToIso(date, start), ends_at: mskToIso(date, end),
      group_ids: groupIds, teacher_ids: teacherIds, location: location.trim(), description: description.trim(),
      status: cancelled ? 'cancelled' : 'scheduled', lesson_type: lessonType || null,
    };
    setBusy(true);
    try {
      if (initial && etag) await api.updateEvent(initial.id, input, etag);
      else await api.createEvent(input);
      toast(initial ? 'Занятие сохранено' : 'Занятие добавлено');
      onSaved();
    } catch (err) {
      setError(humanMessage(err));
      setBusy(false);
    }
  };

  const knownTeachers = people.data ?? [];
  // Групп может быть много: выбранные — сверху, остальные фильтруются поиском.
  const needle = groupQuery.trim().toLowerCase();
  const shownGroups = groups
    .filter(g => groupIds.includes(g.id) || !needle || g.name.toLowerCase().includes(needle))
    .sort((a, b) => Number(groupIds.includes(b.id)) - Number(groupIds.includes(a.id)));
  const extraTeachers = teacherIds.filter(id => !knownTeachers.some(c => c.user_id === id));

  return (
    <Modal
      title={initial ? 'Изменить занятие' : 'Новое занятие'}
      onClose={onClose}
      busy={busy}
      actions={<>
        <Button variant="secondary" onClick={onClose} disabled={busy}>Отмена</Button>
        <Button type="submit" form="event-form" loading={busy}>{initial ? 'Сохранить' : 'Добавить'}</Button>
      </>}
    >
      <form id="event-form" className={p.formGrid} onSubmit={submit} noValidate>
        <Input label="Название" value={title} onChange={e => setTitle(e.target.value)} maxLength={200} required />
        <div className={p.formRow}>
          <Input label="Дата" type="date" value={date} onChange={e => setDate(e.target.value)} required />
          <Input label="Начало" type="time" value={start} onChange={e => setStart(e.target.value)} required />
          <Input label="Окончание" type="time" value={end} onChange={e => setEnd(e.target.value)} required />
        </div>
        <Select label="Тип занятия" hint="Цвет пометки: лекция — зелёный, семинар — красный, лабораторная — синий"
          value={lessonType} onChange={e => setLessonType(e.target.value as LessonType | '')}>
          <option value="">Не указан</option>
          {(Object.keys(LESSON_TYPES) as LessonType[]).map(t => <option key={t} value={t}>{LESSON_TYPES[t]}</option>)}
        </Select>
        <Input label="Аудитория" hint="Оставьте пустым для онлайн-занятия" value={location} onChange={e => setLocation(e.target.value)} maxLength={200} />
        <TextArea label="Описание" hint="Ссылку на онлайн-занятие добавьте сюда — у студентов появится кнопка «Подключиться»"
          value={description} onChange={e => setDescription(e.target.value)} maxLength={2000} />
        <fieldset className={p.fieldset}>
          <legend className={p.legend}>Группы{groupIds.length ? ` · выбрано ${groupIds.length}` : ''}</legend>
          {groups.length > 8 && <SearchInput placeholder="Найти группу" value={groupQuery} onChange={e => setGroupQuery(e.target.value)} />}
          <div className={`${p.checks} ${s.scrollChecks}`}>
            {!groups.length && <span className={p.muted}>Групп пока нет: их создаёт администратор в разделе «Группы».</span>}
            {shownGroups.map(g => (
              <label key={g.id} className={p.check}>
                <input type="checkbox" checked={groupIds.includes(g.id)} onChange={() => setGroupIds(l => toggle(l, g.id))} />{g.name}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className={p.fieldset}>
          <legend className={p.legend}>Преподаватели</legend>
          {people.status === 'loading' ? <LoadingState text="Загружаем список…" /> : (
            <div className={`${p.checks} ${s.scrollChecks}`}>
              {knownTeachers.map(c => (
                <label key={c.user_id} className={p.check}>
                  <input type="checkbox" checked={teacherIds.includes(c.user_id)} disabled={c.user_id === lockedTeacher}
                    onChange={() => setTeacherIds(l => toggle(l, c.user_id))} />
                  {c.user_id === lockedTeacher ? `${c.display_name} (вы)` : c.display_name}
                </label>
              ))}
              {extraTeachers.map(id => (
                <label key={id} className={p.check}>
                  <input type="checkbox" checked disabled={id === lockedTeacher} onChange={() => setTeacherIds(l => toggle(l, id))} />
                  {id === lockedTeacher ? 'Вы' : 'Преподаватель без анкеты'}
                </label>
              ))}
              {!knownTeachers.length && !extraTeachers.length && <span className={p.muted}>{profiles ? 'Преподавателей в вузе пока нет.' : 'Сервис «Люди» выключен.'} Добавьте преподавателя по ID ниже.</span>}
            </div>
          )}
        </fieldset>
        <div className={p.formRow}>
          <Input label="Добавить преподавателя по ID" value={manualTeacher} onChange={e => setManualTeacher(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addTeacher(); } }} />
          <div style={{ alignSelf: 'end' }}><Button variant="secondary" onClick={addTeacher} disabled={!manualTeacher.trim()}>Добавить</Button></div>
        </div>
        <label className={p.check}>
          <input type="checkbox" checked={cancelled} onChange={e => setCancelled(e.target.checked)} />Занятие отменено
        </label>
        {error && <p role="alert" className={p.formError}>{error}</p>}
      </form>
    </Modal>
  );
}
