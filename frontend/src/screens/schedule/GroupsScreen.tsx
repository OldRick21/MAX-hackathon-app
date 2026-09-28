// Учебные группы: создание, переименование, удаление и состав (admin с правом groups.manage).
// Состав — UUID студентов; сервер сам проверяет, что это студенты вуза и что студент в одной группе.
import { useMemo, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import type { ProfilesApi, ScheduleApi } from '../../api/backend';
import { humanMessage, isUUID } from '../../api/http';
import type { Group, ProfileCard, ServiceView } from '../../api/types';
import { SectionGate } from '../../components/SectionGate';
import { IconUsers } from '../../components/icons/figma';
import { IconArrowLeft, IconLock, IconPlus } from '../../components/icons/ui';
import { Button, EmptyState, ErrorState, Input, LoadingState, Modal, toast } from '../../components/ui';
import { useAsync } from '../../hooks/useAsync';
import { useProfilesApi } from '../../hooks/useServices';
import { useUserNames } from '../../hooks/useUserNames';
import { useInstitution } from '../../state/institution';
import { useBackend } from '../../state/session';
import p from '../pages.module.css';
import s from './schedule.module.css';

export function GroupsScreen() {
  return <SectionGate section="schedule">{service => <Groups service={service} />}</SectionGate>;
}

function Groups({ service }: { service: ServiceView }) {
  const backend = useBackend();
  const { institution } = useInstitution();
  const api: ScheduleApi = useMemo(() => backend.schedule(service), [backend, service]);
  const profiles = useProfilesApi();
  const groups = useAsync(signal => api.listGroups(signal), [api]);
  const [name, setName] = useState('');
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState<Group | null>(null);
  const [composing, setComposing] = useState<Group | null>(null);

  const back = <Link to={`/institution/${institution.id}/schedule`} className={p.back}><IconArrowLeft />Расписание</Link>;
  if (service.profile !== 'admin' || !service.permissions.includes('groups.manage')) {
    return <>{back}<EmptyState icon={<IconLock />} title="Нет доступа"
      text="Группы ведёт администратор с ролью «Редактор расписания». Роль назначает владелец вуза в администрировании." /></>;
  }

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    try {
      await api.createGroup(name.trim());
      setName('');
      toast('Группа создана');
      groups.reload();
    } catch (err) { toast(humanMessage(err), true); } finally { setCreating(false); }
  };

  const remove = async (g: Group) => {
    try {
      const { etag } = await api.getGroup(g.id);
      await api.deleteGroup(g.id, etag);
      toast('Группа удалена');
      groups.reload();
    } catch (err) { toast(humanMessage(err), true); }
  };

  return (
    <div>
      {back}
      <div className={p.pageHead}>
        <div>
          <h1 className={p.title}>Учебные группы</h1>
          <p className={p.subtitle}>Студент видит занятия своей группы. Один студент — одна группа.</p>
        </div>
      </div>

      <form className={`${p.card} ${p.cardPad} ${s.groupForm}`} onSubmit={create}>
        <Input label="Новая группа" placeholder="Например, ИВТ-21" value={name} maxLength={100}
          onChange={e => setName(e.target.value)} />
        <Button type="submit" icon={<IconPlus width="1.2em" height="1.2em" />} loading={creating} disabled={!name.trim()}>Создать</Button>
      </form>

      {groups.status === 'error' ? (
        <ErrorState title="Не удалось загрузить группы" text={humanMessage(groups.error)} onRetry={groups.reload} />
      ) : groups.status === 'loading' && !groups.data ? <LoadingState /> : !groups.data?.length ? (
        <EmptyState icon={<IconUsers />} title="Групп пока нет" text="Создайте первую группу и добавьте в неё студентов." />
      ) : (
        <ul className={s.groupList}>
          {groups.data.map(g => (
            <li key={g.id} className={`${p.card} ${s.groupRow}`}>
              <span className={s.groupName}>{g.name}</span>
              <span className={s.spacer} />
              <Button size="small" onClick={() => setComposing(g)}>Состав</Button>
              <Button size="small" variant="secondary" onClick={() => setRenaming(g)}>Переименовать</Button>
              <Button size="small" variant="ghost" onClick={() => void remove(g)}>Удалить</Button>
            </li>
          ))}
        </ul>
      )}

      {renaming && <RenameGroup api={api} group={renaming} onClose={() => setRenaming(null)}
        onDone={() => { setRenaming(null); groups.reload(); }} />}
      {composing && <GroupStudents api={api} profiles={profiles?.api ?? null} scope={profiles?.service.id ?? ''}
        group={composing} onClose={() => setComposing(null)} />}
    </div>
  );
}

function RenameGroup({ api, group, onClose, onDone }: { api: ScheduleApi; group: Group; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState(group.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return setError('Укажите название группы.');
    setBusy(true);
    try {
      const { etag } = await api.getGroup(group.id);
      await api.renameGroup(group.id, name.trim(), etag);
      toast('Группа переименована');
      onDone();
    } catch (err) { setError(humanMessage(err)); setBusy(false); }
  };
  return (
    <Modal title="Переименовать группу" onClose={onClose} busy={busy} actions={<>
      <Button variant="secondary" onClick={onClose} disabled={busy}>Отмена</Button>
      <Button type="submit" form="rename-group" loading={busy}>Сохранить</Button>
    </>}>
      <form id="rename-group" className={p.formGrid} onSubmit={save} noValidate>
        <Input label="Название" value={name} maxLength={100} onChange={e => setName(e.target.value)} required />
        {error && <p role="alert" className={p.formError}>{error}</p>}
      </form>
    </Modal>
  );
}

function GroupStudents({ api, profiles, scope, group, onClose }: {
  api: ScheduleApi; profiles: ProfilesApi | null; scope: string; group: Group; onClose: () => void;
}) {
  const current = useAsync(signal => api.getStudents(group.id, signal), [api, group.id]);
  // Анкеты не различают студентов и преподавателей — сервер отклонит не-студентов.
  const people = useAsync<ProfileCard[]>(async signal => (profiles ? (await profiles.listUsers('', null, signal)).items : []), [profiles]);
  const [selected, setSelected] = useState<string[] | null>(null);
  const [manual, setManual] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ids = selected ?? current.data?.data ?? [];
  const names = useUserNames(profiles, scope, ids);
  const known = people.data ?? [];
  const extra = ids.filter(id => !known.some(c => c.user_id === id));
  const toggle = (id: string) => setSelected(ids.includes(id) ? ids.filter(x => x !== id) : [...ids, id]);

  const addManual = () => {
    const id = manual.trim().toLowerCase();
    if (!isUUID(id)) return setError('Введите UUID пользователя: он показан у студента в приложении.');
    setError(null);
    if (!ids.includes(id)) setSelected([...ids, id]);
    setManual('');
  };

  const save = async () => {
    if (!current.data) return;
    setBusy(true);
    setError(null);
    try {
      await api.setStudents(group.id, ids, current.data.etag);
      toast('Состав группы сохранён');
      onClose();
    } catch (err) { setError(humanMessage(err)); setBusy(false); }
  };

  return (
    <Modal title={`Состав группы «${group.name}»`} onClose={onClose} busy={busy} actions={<>
      <Button variant="secondary" onClick={onClose} disabled={busy}>Отмена</Button>
      <Button onClick={save} loading={busy} disabled={!current.data}>Сохранить</Button>
    </>}>
      {current.status === 'error' ? <ErrorState text={humanMessage(current.error)} onRetry={current.reload} />
        : !current.data || people.status === 'loading' ? <LoadingState /> : (
          <div className={p.formGrid}>
            <fieldset className={p.fieldset}>
              <legend className={p.legend}>Студенты ({ids.length})</legend>
              <div className={p.checks}>
                {known.map(c => (
                  <label key={c.user_id} className={p.check}>
                    <input type="checkbox" checked={ids.includes(c.user_id)} onChange={() => toggle(c.user_id)} />{c.display_name}
                  </label>
                ))}
                {extra.map(id => (
                  <label key={id} className={p.check}>
                    <input type="checkbox" checked onChange={() => toggle(id)} />{names[id] || `Без анкеты · ${id.slice(0, 8)}`}
                  </label>
                ))}
                {!known.length && !extra.length && <span className={p.muted}>Пока никого. Добавьте студентов по UUID ниже.</span>}
              </div>
            </fieldset>
            <div className={s.groupForm}>
              <Input label="Добавить по UUID" hint="Для студентов без анкеты: UUID показан у них в приложении"
                value={manual} onChange={e => setManual(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addManual(); } }} />
              <Button variant="secondary" onClick={addManual} disabled={!manual.trim()}>Добавить</Button>
            </div>
            <p className={p.muted}>В списке — все, кто заполнил анкету. Сохранить получится только студентов вуза, которые не состоят в другой группе.</p>
            {error && <p role="alert" className={p.formError}>{error}</p>}
          </div>
        )}
    </Modal>
  );
}
