// Первый вход и вступление в другие вузы: имя, вузы, профиль и группа — только из списков,
// зарегистрированных в системе. Заявку одобряет или отклоняет администратор вуза.
import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { humanMessage } from '../../api/http';
import type { JoinOption, JoinProfile, JoinRequest } from '../../api/types';
import { Badge, Button, Input, LoadingState, Select, toast, type BadgeTone } from '../../components/ui';
import { PROFILE_LABEL } from '../../state/institution';
import { useBackend, useSession } from '../../state/session';
import { Frame } from './EntryScreens';
import s from './entry.module.css';

const STATUS: Record<JoinRequest['status'], [string, BadgeTone]> = {
  pending: ['На рассмотрении', 'warning'],
  approved: ['Одобрена', 'success'],
  rejected: ['Отклонена', 'error'],
  withdrawn: ['Отозвана', 'neutral'],
};

/** Строка заявки: вуз, профиль и (для студента) группа — всё выбирается из выпадающих списков. */
type Choice = { key: string; institution_id: string; profile: JoinProfile; group_id: string };
const emptyChoice = (): Choice => ({ key: crypto.randomUUID(), institution_id: '', profile: 'student', group_id: '' });

function RequestList({ requests, onWithdraw }: { requests: JoinRequest[]; onWithdraw: (r: JoinRequest) => void }) {
  return (
    <ul className={s.list}>
      {requests.map(r => (
        <li key={r.id} className={s.request}>
          <div className={s.requestHead}>
            <span className={s.uniName}>{r.institution_name}</span>
            <Badge tone={STATUS[r.status][1]}>{STATUS[r.status][0]}</Badge>
          </div>
          <span className={s.uniMeta}>
            {r.full_name} · {PROFILE_LABEL[r.profile]}{r.group_name ? ` · группа ${r.group_name}` : ''}
          </span>
          {r.decision_reason && <span className={s.uniMeta}>Причина: {r.decision_reason}</span>}
          {r.status === 'pending' && (
            <div><Button variant="ghost" size="small" onClick={() => onWithdraw(r)}>Отозвать</Button></div>
          )}
        </li>
      ))}
    </ul>
  );
}

export function JoinScreen() {
  const backend = useBackend();
  const { institutions, maxUser, reloadInstitutions } = useSession();
  const navigate = useNavigate();
  const [options, setOptions] = useState<JoinOption[] | null>(null);
  const [requests, setRequests] = useState<JoinRequest[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState(() => [maxUser?.first_name, maxUser?.last_name].filter(Boolean).join(' '));
  const [rows, setRows] = useState<Choice[]>(() => [emptyChoice()]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const [o, r] = await Promise.all([backend.listJoinOptions(), backend.listJoinRequests(), reloadInstitutions()]);
      setOptions(o);
      setRequests(r);
      setFormOpen(open => open || r.length === 0);
    } catch (e) { setLoadError(humanMessage(e)); }
  }, [backend, reloadInstitutions]);
  useEffect(() => { load(); }, [load]);

  const pendingIn = new Set(requests.filter(r => r.status === 'pending').map(r => r.institution_id));
  // Профили, которые ещё можно попросить в вузе: без уже имеющихся.
  const free = (o: JoinOption) => (['student', 'teacher'] as JoinProfile[]).filter(p => !o.profiles.includes(p));
  const available = (options ?? []).filter(o => free(o).length > 0);

  const optionOf = (id: string) => (options ?? []).find(o => o.id === id);
  const update = (key: string, patch: Partial<Choice>) => setRows(list => list.map(r => r.key === key ? { ...r, ...patch } : r));
  const pickInstitution = (key: string, id: string) => {
    const o = optionOf(id);
    update(key, { institution_id: id, profile: o ? free(o)[0] : 'student', group_id: '' });
  };

  const submit = async () => {
    setError(null);
    const picked = rows.filter(r => r.institution_id);
    if (!name.trim()) { setError('Укажите имя и фамилию.'); return; }
    if (!picked.length) { setError('Выберите вуз.'); return; }
    const missing = picked.find(r => r.profile === 'student' && !r.group_id);
    if (missing) { setError(`Выберите группу в вузе «${optionOf(missing.institution_id)?.display_name}».`); return; }
    setBusy(true);
    try {
      await backend.submitJoinRequests(name.trim(), picked.map(r => ({
        institution_id: r.institution_id, profile: r.profile,
        ...(r.profile === 'student' ? { group_id: r.group_id } : {}),
      })));
      toast('Заявка отправлена администраторам вузов');
      setRows([emptyChoice()]);
      setFormOpen(false);
      await load();
    } catch (e) { setError(humanMessage(e)); } finally { setBusy(false); }
  };

  const withdraw = async (r: JoinRequest) => {
    try { await backend.withdrawJoinRequest(r.id); toast('Заявка отозвана'); await load(); }
    catch (e) { toast(humanMessage(e), true); }
  };

  if (loadError) {
    return (
      <Frame>
        <h1 className={s.title}>Не удалось загрузить вузы</h1>
        <p className={s.text}>{loadError}</p>
        <div className={s.center}><Button onClick={load}>Повторить</Button></div>
      </Frame>
    );
  }
  if (!options) return <Frame><LoadingState text="Загружаем список вузов…" /></Frame>;

  const back = institutions.length > 0 && (
    <div className={s.center}><Link to="/">← Вернуться в приложение</Link></div>
  );

  if (!formOpen) {
    const approved = requests.some(r => r.status === 'approved') && institutions.length > 0;
    return (
      <Frame>
        <h1 className={s.title}>Ваши заявки</h1>
        <p className={s.text}>Администратор вуза проверит данные и одобрит заявку. После одобрения вуз появится в приложении.</p>
        <RequestList requests={requests} onWithdraw={withdraw} />
        <div className={s.center}>
          {approved
            ? <Button onClick={() => navigate('/')}>Перейти в приложение</Button>
            : <Button variant="secondary" onClick={load}>Проверить статус</Button>}
          {available.length > 0 && <Button variant="ghost" onClick={() => setFormOpen(true)}>Подать заявку в другой вуз</Button>}
        </div>
        {back}
      </Frame>
    );
  }

  return (
    <Frame>
      <h1 className={s.title}>{institutions.length ? 'Вступить в вуз' : 'Добро пожаловать!'}</h1>
      <p className={s.text}>Укажите имя, выберите вуз и группу. Заявку рассмотрит администратор вуза.</p>
      <Input label="Имя и фамилия" value={name} maxLength={200} autoComplete="name" onChange={e => setName(e.target.value)} />
      {available.length === 0
        ? <p className={s.text}>Сейчас нет вузов, в которые можно подать заявку.</p>
        : (
          <ul className={s.list}>
            {rows.map((row, i) => {
              const o = optionOf(row.institution_id);
              const profiles = o ? free(o) : [];
              // Вуз не повторяется в соседних строках; вуз с заявкой на рассмотрении выбрать нельзя.
              const taken = new Set(rows.filter(r => r.key !== row.key).map(r => r.institution_id));
              return (
                <li key={row.key} className={s.request} data-selected={!!o || undefined}>
                  <div className={s.choice}>
                    <Select label={rows.length > 1 ? `Вуз ${i + 1}` : 'Вуз'} value={row.institution_id}
                      onChange={e => pickInstitution(row.key, e.target.value)}>
                      <option value="" disabled>Выберите из списка</option>
                      {available.map(x => (
                        <option key={x.id} value={x.id} disabled={taken.has(x.id) || pendingIn.has(x.id)}>
                          {x.display_name}{pendingIn.has(x.id) ? ' — заявка на рассмотрении' : ''}
                        </option>
                      ))}
                    </Select>
                    {o && o.profiles.length > 0 && (
                      <p className={s.uniMeta}>Вы уже: {o.profiles.map(p => PROFILE_LABEL[p]).join(', ')}</p>
                    )}
                    {o && (
                      <Select label="Кто вы" value={row.profile} disabled={profiles.length < 2}
                        onChange={e => update(row.key, { profile: e.target.value as JoinProfile, group_id: '' })}>
                        {profiles.map(p => <option key={p} value={p}>{PROFILE_LABEL[p]}</option>)}
                      </Select>
                    )}
                    {o && row.profile === 'student' && (o.groups.length
                      ? (
                        <Select label="Группа" value={row.group_id} onChange={e => update(row.key, { group_id: e.target.value })}>
                          <option value="" disabled>Выберите из списка</option>
                          {o.groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
                        </Select>
                      )
                      : <p className={s.uniMeta}>В этом вузе пока нет групп — студенческую заявку подать нельзя.</p>)}
                    {rows.length > 1 && (
                      <Button variant="ghost" size="small" onClick={() => setRows(list => list.filter(r => r.key !== row.key))}>Убрать</Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      {rows.length < available.length && rows.every(r => r.institution_id) && (
        <div className={s.center}><Button variant="ghost" onClick={() => setRows(list => [...list, emptyChoice()])}>+ Добавить ещё вуз</Button></div>
      )}
      {error && <p className={s.formError} role="alert">{error}</p>}
      <div className={s.center}>
        <Button onClick={submit} loading={busy} disabled={available.length === 0}>Отправить заявку</Button>
        {requests.length > 0 && <Button variant="ghost" onClick={() => setFormOpen(false)}>Мои заявки</Button>}
      </div>
      {back}
      <ConnectInstitution />
    </Frame>
  );
}

/** Своего вуза нет в списке — заявка на подключение вуза платформе. */
function ConnectInstitution() {
  return (
    <p className={s.text}>
      Вашего вуза нет в списке? <Link to="/applications">Подать заявку на подключение вуза</Link>
    </p>
  );
}
