// Учебные группы — сущность ядра. Все группы и их состав видит любой участник вуза.
// Править (создать, переименовать, удалить, состав) может любой администратор вуза без ролей;
// преподаватели и студенты только смотрят. Групп и студентов может быть много: список с поиском,
// состав раскрывается по нажатию и прокручивается внутри карточки.
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { humanMessage } from '../../api/http';
import type { GroupDirectory, GroupEntry } from '../../api/types';
import { IconUsers } from '../../components/icons/figma';
import { IconArrowLeft } from '../../components/icons/ui';
import { Badge, Button, EmptyState, ErrorState, Input, LoadingState, Modal, SearchInput, toast } from '../../components/ui';
import { useAsync } from '../../hooks/useAsync';
import { useProfilesApi } from '../../hooks/useServices';
import { useUserNames } from '../../hooks/useUserNames';
import { useInstitution } from '../../state/institution';
import { useBackend } from '../../state/session';
import p from '../pages.module.css';
import s from './schedule.module.css';

const plural = (n: number) => {
  const d = n % 10, h = n % 100;
  return d === 1 && h !== 11 ? 'студент' : d >= 2 && d <= 4 && (h < 12 || h > 14) ? 'студента' : 'студентов';
};

type Dialog = { kind: 'create' } | { kind: 'rename'; group: GroupEntry } | { kind: 'members'; group: GroupEntry } | { kind: 'delete'; group: GroupEntry };

export function GroupsScreen() {
  const backend = useBackend();
  const { institution, profile } = useInstitution();
  const dir = useAsync(signal => backend.listGroups(institution.id, profile, signal), [backend, institution.id, profile]);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const people = useProfilesApi();
  const base = `/institution/${institution.id}`;

  // Имена грузим только для раскрытых групп: студентов может быть несколько сотен.
  const openIds = (dir.data?.items ?? []).filter(g => open.has(g.id)).flatMap(g => g.user_ids);
  const names = useUserNames(people?.api ?? null, people?.service.id ?? '', openIds);
  const nameOf = (id: string) => names[id] || (names[id] === undefined ? '…' : `Без анкеты · ${id.slice(0, 8)}`);

  const back = <Link to={`${base}/schedule`} className={p.back}><IconArrowLeft />Расписание</Link>;
  if (dir.status === 'error') return <>{back}<ErrorState text={humanMessage(dir.error)} onRetry={dir.reload} /></>;
  if (dir.status === 'loading' || !dir.data) return <>{back}<LoadingState /></>;
  const { items, can_manage, my_group_ids } = dir.data;
  const needle = query.trim().toLowerCase();
  const shown = needle ? items.filter(g => g.name.toLowerCase().includes(needle)) : items;
  const toggle = (id: string) => setOpen(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  return (
    <div>
      {back}
      <div className={p.pageHead}>
        <div>
          <h1 className={p.title}>Учебные группы</h1>
          <p className={p.subtitle}>{items.length ? `${items.length} в вузе` : 'Групп пока нет'}</p>
        </div>
        {can_manage && <Button onClick={() => setDialog({ kind: 'create' })}>Новая группа</Button>}
      </div>
      {items.length > 5 && (
        <SearchInput className={s.groupSearch} placeholder="Найти группу" value={query} onChange={e => setQuery(e.target.value)} />
      )}
      {!items.length ? (
        <EmptyState icon={<IconUsers />} title="Групп пока нет"
          text={can_manage ? 'Создайте первую группу кнопкой «Новая группа».' : 'Группы появятся, когда их создаст администратор вуза.'} />
      ) : !shown.length ? (
        <EmptyState icon={<IconUsers />} title="Ничего не найдено" text="Проверьте название группы." />
      ) : (
        <ul className={s.groupList}>
          {shown.map(g => {
            const expanded = open.has(g.id);
            return (
              <li key={g.id} className={`${p.card} ${s.groupCard}`}>
                <button type="button" className={s.groupHead} aria-expanded={expanded} onClick={() => toggle(g.id)}>
                  <span className={s.groupName}>{g.name}</span>
                  {my_group_ids.includes(g.id) && <Badge tone="accent">Моя группа</Badge>}
                  <span className={`${p.muted} ${s.groupCount}`}>{g.user_ids.length} {plural(g.user_ids.length)}</span>
                  <span className={s.chevron} aria-hidden="true" data-open={expanded} />
                </button>
                <div className={s.groupBody} data-open={expanded}>
                  <div className={s.groupBodyInner}>
                    {expanded && (g.user_ids.length ? (
                      <ul className={s.memberList}>
                        {g.user_ids.map(id => <li key={id}>{nameOf(id)}</li>)}
                      </ul>
                    ) : <p className={p.muted}>В группе пока нет студентов.</p>)}
                    <div className={s.groupActions}>
                      <Link to={`${base}/schedule?group=${g.id}`} className={s.linkBtn}>Расписание группы</Link>
                      {can_manage && <>
                        <Button size="small" variant="secondary" onClick={() => setDialog({ kind: 'members', group: g })}>Состав</Button>
                        <Button size="small" variant="ghost" onClick={() => setDialog({ kind: 'rename', group: g })}>Переименовать</Button>
                        <Button size="small" variant="ghost" onClick={() => setDialog({ kind: 'delete', group: g })}>Удалить</Button>
                      </>}
                    </div>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {dialog && <GroupDialog dialog={dialog} dir={dir.data} onClose={() => setDialog(null)}
        onDone={msg => { setDialog(null); toast(msg); dir.reload(); }} />}
    </div>
  );
}

function GroupDialog({ dialog, dir, onClose, onDone }:
  { dialog: Dialog; dir: GroupDirectory; onClose: () => void; onDone: (msg: string) => void }) {
  const backend = useBackend();
  const { institution, profile } = useInstitution();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [name, setName] = useState(dialog.kind === 'rename' ? dialog.group.name : '');
  const run = async (fn: () => Promise<unknown>, msg: string) => {
    setBusy(true); setError('');
    try { await fn(); onDone(msg); } catch (e) { setError(humanMessage(e)); setBusy(false); }
  };
  const id = institution.id;

  if (dialog.kind === 'members') {
    return <MembersDialog group={dialog.group} dir={dir} busy={busy} error={error} onClose={onClose}
      onSave={ids => run(() => backend.setGroupMembers(id, profile, dialog.group, ids), 'Состав группы сохранён')} />;
  }
  if (dialog.kind === 'delete') {
    return (
      <Modal title={`Удалить группу ${dialog.group.name}?`} onClose={onClose} busy={busy} actions={<>
        <Button variant="secondary" onClick={onClose} disabled={busy}>Отмена</Button>
        <Button variant="danger" loading={busy} onClick={() => run(() => backend.deleteGroup(id, profile, dialog.group), 'Группа удалена')}>Удалить</Button>
      </>}>
        <p>Удалить можно только пустую группу, на которую не ссылаются занятия.</p>
        {error && <p className={s.formError}>{error}</p>}
      </Modal>
    );
  }
  const create = dialog.kind === 'create';
  const submit = () => {
    if (!name.trim()) { setError('Введите название группы.'); return; }
    run(() => create ? backend.createGroup(id, profile, name.trim())
      : backend.renameGroup(id, profile, dialog.group, name.trim()), create ? 'Группа создана' : 'Группа переименована');
  };
  return (
    <Modal title={create ? 'Новая группа' : 'Переименовать группу'} onClose={onClose} busy={busy} actions={<>
      <Button variant="secondary" onClick={onClose} disabled={busy}>Отмена</Button>
      <Button loading={busy} onClick={submit}>{create ? 'Создать' : 'Сохранить'}</Button>
    </>}>
      <form onSubmit={e => { e.preventDefault(); submit(); }}>
        <Input label="Название" value={name} maxLength={100} onChange={e => setName(e.target.value)} error={error || undefined} />
      </form>
    </Modal>
  );
}

function MembersDialog({ group, dir, busy, error, onClose, onSave }: {
  group: GroupEntry; dir: GroupDirectory; busy: boolean; error: string; onClose: () => void; onSave: (ids: string[]) => void;
}) {
  const people = useProfilesApi();
  const [selected, setSelected] = useState(() => new Set(group.user_ids));
  const [query, setQuery] = useState('');
  const students = dir.students ?? [];
  const names = useUserNames(people?.api ?? null, people?.service.id ?? '', students);
  // Студент состоит максимум в одной группе: занятых другими группами показываем, но не даём выбрать.
  const elsewhere = useMemo(() => {
    const m = new Map<string, string>();
    for (const g of dir.items) if (g.id !== group.id) for (const u of g.user_ids) m.set(u, g.name);
    return m;
  }, [dir.items, group.id]);
  const label = (id: string) => names[id] || `Без анкеты · ${id.slice(0, 8)}`;
  const needle = query.trim().toLowerCase();
  const list = students
    .filter(id => !needle || label(id).toLowerCase().includes(needle) || id.startsWith(needle))
    .sort((a, b) => Number(selected.has(b)) - Number(selected.has(a)) || label(a).localeCompare(label(b), 'ru'));
  const flip = (id: string) => setSelected(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  return (
    <Modal title={`Состав группы ${group.name}`} onClose={onClose} busy={busy} actions={<>
      <Button variant="secondary" onClick={onClose} disabled={busy}>Отмена</Button>
      <Button loading={busy} onClick={() => onSave([...selected])}>Сохранить · {selected.size}</Button>
    </>}>
      <SearchInput placeholder="Найти студента" value={query} onChange={e => setQuery(e.target.value)} />
      {!students.length ? <p className={p.muted}>В вузе пока нет студентов.</p> : (
        <ul className={s.pickList} aria-label="Студенты">
          {list.map(id => {
            const other = elsewhere.get(id);
            return (
              <li key={id}>
                <label className={s.pickRow} data-disabled={!!other}>
                  <input type="checkbox" checked={selected.has(id)} disabled={!!other} onChange={() => flip(id)} />
                  <span className={s.pickName}>{label(id)}</span>
                  {other && <span className={p.muted}>в группе {other}</span>}
                </label>
              </li>
            );
          })}
          {!list.length && <li className={p.muted}>Никого не найдено.</li>}
        </ul>
      )}
      {error && <p className={s.formError}>{error}</p>}
    </Modal>
  );
}
