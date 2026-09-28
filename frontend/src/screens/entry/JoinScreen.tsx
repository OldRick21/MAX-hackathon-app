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

type Choice = { profile: JoinProfile; group_id: string };

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
  const [chosen, setChosen] = useState<Record<string, Choice>>({});
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

  const toggle = (o: JoinOption) => setChosen(c => {
    const next = { ...c };
    if (next[o.id]) delete next[o.id];
    else next[o.id] = { profile: free(o)[0], group_id: '' };
    return next;
  });
  const update = (id: string, patch: Partial<Choice>) => setChosen(c => ({ ...c, [id]: { ...c[id], ...patch } }));

  const submit = async () => {
    setError(null);
    const ids = Object.keys(chosen);
    if (!name.trim()) { setError('Укажите имя и фамилию.'); return; }
    if (!ids.length) { setError('Выберите хотя бы один вуз.'); return; }
    const missing = ids.find(id => chosen[id].profile === 'student' && !chosen[id].group_id);
    if (missing) { setError(`Выберите группу в вузе «${options?.find(o => o.id === missing)?.display_name}».`); return; }
    setBusy(true);
    try {
      await backend.submitJoinRequests(name.trim(), ids.map(id => ({
        institution_id: id, profile: chosen[id].profile,
        ...(chosen[id].profile === 'student' ? { group_id: chosen[id].group_id } : {}),
      })));
      toast('Заявка отправлена администраторам вузов');
      setChosen({});
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
      <p className={s.text}>Укажите имя, выберите один или несколько вузов и группу. Заявку рассмотрит администратор вуза.</p>
      <Input label="Имя и фамилия" value={name} maxLength={200} autoComplete="name" onChange={e => setName(e.target.value)} />
      {available.length === 0
        ? <p className={s.text}>Сейчас нет вузов, в которые можно подать заявку.</p>
        : (
          <ul className={s.list}>
            {available.map(o => {
              const pick = chosen[o.id];
              const pending = pendingIn.has(o.id);
              const profiles = free(o);
              return (
                <li key={o.id} className={s.request} data-selected={!!pick || undefined}>
                  <label className={s.check}>
                    <input type="checkbox" checked={!!pick} disabled={pending} onChange={() => toggle(o)} />
                    <span>
                      <span className={s.uniName}>{o.display_name}</span>
                      {pending && <span className={s.uniMeta} style={{ display: 'block' }}>Заявка уже на рассмотрении</span>}
                      {o.profiles.length > 0 && !pending && (
                        <span className={s.uniMeta} style={{ display: 'block' }}>Вы уже: {o.profiles.map(p => PROFILE_LABEL[p]).join(', ')}</span>
                      )}
                    </span>
                  </label>
                  {pick && (
                    <div className={s.choice}>
                      <Select label="Кто вы" value={pick.profile} disabled={profiles.length < 2}
                        onChange={e => update(o.id, { profile: e.target.value as JoinProfile, group_id: '' })}>
                        {profiles.map(p => <option key={p} value={p}>{PROFILE_LABEL[p]}</option>)}
                      </Select>
                      {pick.profile === 'student' && (o.groups.length
                        ? (
                          <Select label="Группа" value={pick.group_id} onChange={e => update(o.id, { group_id: e.target.value })}>
                            <option value="" disabled>Выберите из списка</option>
                            {o.groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
                          </Select>
                        )
                        : <p className={s.uniMeta}>В этом вузе пока нет групп — студенческую заявку подать нельзя.</p>)}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
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
