// Создание и изменение занятия (только для профиля администратора с правом schedule.write).
import { useState, type FormEvent } from 'react';
import type { ProfilesApi, ScheduleApi } from '../../api/backend';
import { humanMessage } from '../../api/http';
import type { EventInput, Group, ProfileCard, ScheduleEvent } from '../../api/types';
import { Button, Input, LoadingState, Modal, TextArea, toast } from '../../components/ui';
import { useAsync } from '../../hooks/useAsync';
import { dayKey, formatTime, mskToIso } from '../../utils/time';
import p from '../pages.module.css';

export function EventEditor({ api, profiles, groups, initial, etag, defaultDate, onClose, onSaved }: {
  api: ScheduleApi;
  profiles: ProfilesApi | null;
  groups: Group[];
  initial: ScheduleEvent | null;
  etag: string | null;
  defaultDate: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? '');
  const [date, setDate] = useState(initial ? dayKey(initial.starts_at) : defaultDate);
  const [start, setStart] = useState(initial ? formatTime(initial.starts_at) : '09:00');
  const [end, setEnd] = useState(initial ? formatTime(initial.ends_at) : '10:30');
  const [location, setLocation] = useState(initial?.location ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [cancelled, setCancelled] = useState(initial?.status === 'cancelled');
  const [groupIds, setGroupIds] = useState<string[]>(initial?.group_ids ?? []);
  const [teacherIds, setTeacherIds] = useState<string[]>(initial?.teacher_ids ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Анкеты не различают студентов и преподавателей — сервер сам проверит, что выбран преподаватель.
  const people = useAsync<ProfileCard[]>(async signal => (profiles ? (await profiles.listUsers('', null, signal)).items : []), [profiles]);

  const toggle = (list: string[], id: string) => (list.includes(id) ? list.filter(x => x !== id) : [...list, id]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!title.trim()) return setError('Укажите название занятия.');
    if (!groupIds.length) return setError('Выберите хотя бы одну группу.');
    if (!teacherIds.length) return setError('Выберите хотя бы одного преподавателя.');
    if (!(start < end)) return setError('Время окончания должно быть позже времени начала.');
    const input: EventInput = {
      title: title.trim(), starts_at: mskToIso(date, start), ends_at: mskToIso(date, end),
      group_ids: groupIds, teacher_ids: teacherIds, location: location.trim(), description: description.trim(),
      status: cancelled ? 'cancelled' : 'scheduled',
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
        <Input label="Аудитория" hint="Оставьте пустым для онлайн-занятия" value={location} onChange={e => setLocation(e.target.value)} maxLength={200} />
        <TextArea label="Описание" hint="Ссылку на онлайн-занятие добавьте сюда — у студентов появится кнопка «Подключиться»"
          value={description} onChange={e => setDescription(e.target.value)} maxLength={2000} />
        <fieldset className={p.fieldset}>
          <legend className={p.legend}>Группы</legend>
          <div className={p.checks}>
            {groups.map(g => (
              <label key={g.id} className={p.check}>
                <input type="checkbox" checked={groupIds.includes(g.id)} onChange={() => setGroupIds(l => toggle(l, g.id))} />{g.name}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className={p.fieldset}>
          <legend className={p.legend}>Преподаватели</legend>
          {people.status === 'loading' ? <LoadingState text="Загружаем список…" /> : (
            <div className={p.checks}>
              {knownTeachers.map(c => (
                <label key={c.user_id} className={p.check}>
                  <input type="checkbox" checked={teacherIds.includes(c.user_id)} onChange={() => setTeacherIds(l => toggle(l, c.user_id))} />
                  {c.display_name}
                </label>
              ))}
              {extraTeachers.map(id => (
                <label key={id} className={p.check}>
                  <input type="checkbox" checked onChange={() => setTeacherIds(l => toggle(l, id))} />Преподаватель без анкеты
                </label>
              ))}
              {!knownTeachers.length && !extraTeachers.length && <span className={p.muted}>Список людей недоступен: сервис анкет не подключён.</span>}
            </div>
          )}
        </fieldset>
        <label className={p.check}>
          <input type="checkbox" checked={cancelled} onChange={e => setCancelled(e.target.checked)} />Занятие отменено
        </label>
        {error && <p role="alert" className={p.formError}>{error}</p>}
      </form>
    </Modal>
  );
}
