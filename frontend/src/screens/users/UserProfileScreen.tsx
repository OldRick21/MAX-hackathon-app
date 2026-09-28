import { useMemo, useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import type { ProfilesApi } from '../../api/backend';
import { ApiError, humanMessage, isUUID } from '../../api/http';
import type { ProfileCard, ServiceView, Versioned } from '../../api/types';
import { SectionGate } from '../../components/SectionGate';
import { IconArrowLeft, IconUserOff } from '../../components/icons/ui';
import { Avatar, Button, EmptyState, ErrorState, Input, LoadingState, Modal, TextArea, toast } from '../../components/ui';
import { useAsync } from '../../hooks/useAsync';
import { useScheduleApi } from '../../hooks/useServices';
import { PROFILE_LABEL, useInstitution } from '../../state/institution';
import { useBackend, useSession } from '../../state/session';
import p from '../pages.module.css';
import s from './users.module.css';

export function UserProfileScreen() {
  return <SectionGate section="users">{service => <UserProfile service={service} />}</SectionGate>;
}

function UserProfile({ service }: { service: ServiceView }) {
  const { userId = '' } = useParams();
  const backend = useBackend();
  const { user, maxUser } = useSession();
  const { institution, profile } = useInstitution();
  const api = useMemo(() => backend.profiles(service), [backend, service]);
  const isMe = userId === 'me' || userId === user?.id;
  const [editSelf, setEditSelf] = useState(false);
  const [editAcademic, setEditAcademic] = useState(false);

  const card = useAsync<Versioned<ProfileCard>>(signal => {
    if (isMe) return api.getMe(signal);
    if (!isUUID(userId)) return Promise.reject(new ApiError('Не удалось найти данные. Возможно, их удалили.', 404));
    return api.getUser(userId, signal);
  }, [api, userId, isMe]);

  const canManage = profile === 'admin' && service.permissions.includes('profiles.manage');
  const back = isMe
    ? <Link to={`/institution/${institution.id}/users`} className={p.back}><IconArrowLeft />Люди</Link>
    : <Link to={`/institution/${institution.id}/users`} className={p.back}><IconArrowLeft />Все пользователи</Link>;

  if (card.status === 'loading' && !card.data) return <>{back}<LoadingState /></>;
  if (card.status === 'error') {
    const notFound = card.error instanceof ApiError && card.error.status === 404;
    return <>{back}{notFound
      ? <EmptyState icon={<IconUserOff />} title="Пользователь не найден" text="Возможно, он больше не состоит в этом вузе." />
      : <ErrorState title="Не удалось загрузить профиль" text={humanMessage(card.error)} onRetry={card.reload} />}</>;
  }
  const c = card.data!.data;
  const name = c.display_name || (isMe ? [maxUser?.first_name, maxUser?.last_name].filter(Boolean).join(' ') : '') || 'Имя не указано';

  return (
    <div>
      {back}
      <div className={s.profile}>
        <section className={s.head}>
          <Avatar name={name} src={isMe ? maxUser?.photo_url : undefined} size="var(--avatar)" className={s.photo} />
          <div className={s.headText}>
            <h1 className={s.name}>{name}</h1>
            <p className={s.meta}>{isMe ? PROFILE_LABEL[profile] : [c.position, c.academic_degree].filter(Boolean).join(' · ') || 'Участник вуза'}</p>
            <p className={s.uni}>{institution.display_name}</p>
          </div>
        </section>

        <section className={s.details} aria-label="Данные профиля">
          <dl className={s.dl}>
            <dt>Должность</dt><dd>{c.position || '—'}</dd>
            <dt>Учёная степень</dt><dd>{c.academic_degree || '—'}</dd>
            <dt>О себе</dt><dd>{c.about || (isMe ? 'Расскажите о себе — это увидят другие участники вуза.' : '—')}</dd>
          </dl>
          {(isMe || canManage) && (
            <div className={s.actions}>
              {isMe && <Button onClick={() => setEditSelf(true)}>Редактировать профиль</Button>}
              {canManage && <Button variant="secondary" onClick={() => setEditAcademic(true)}>Изменить должность и степень</Button>}
            </div>
          )}
        </section>

        {isMe && <AboutMe userId={user?.id ?? ''} />}
      </div>

      {editSelf && <EditSelf api={api} card={card.data!} onClose={() => setEditSelf(false)} onSaved={v => { card.mutate(() => v); setEditSelf(false); }} />}
      {editAcademic && (
        <EditAcademic api={api} userId={c.user_id} isMe={isMe} card={card.data!}
          onClose={() => setEditAcademic(false)} onSaved={v => { card.mutate(() => v); setEditAcademic(false); }} />
      )}
    </div>
  );
}

const ROLE_LABEL: Record<string, string> = {
  owner: 'Владелец вуза', technical_admin: 'Технический администратор', membership_admin: 'Администратор участников',
  schedule_editor: 'Редактор расписания', profile_editor: 'Редактор анкет', coursework_manager: 'Менеджер курсовых',
};

/** Всё о себе в вузе: профили, роли в сервисах, учебная группа и ID для администратора. */
function AboutMe({ userId }: { userId: string }) {
  const { institution, profiles, profile, catalog } = useInstitution();
  const schedule = useScheduleApi();
  // Своя группа видна только студенту: преподавателю сервис отдаёт все группы вуза.
  const group = useAsync(async signal => (schedule && profile === 'student'
    ? (await schedule.api.listGroups(signal))[0]?.name ?? null : undefined), [schedule, profile]);
  const roles = (catalog.status === 'ready' ? catalog.value : [])
    .flatMap(svc => svc.roles.map(r => `${ROLE_LABEL[r] ?? r} (${svc.display_name})`));
  const copy = async () => {
    try { await navigator.clipboard.writeText(userId); toast('ID скопирован'); }
    catch { toast('Не удалось скопировать. Выделите ID вручную.', true); }
  };
  return (
    <section className={s.details} aria-label="Участие в вузе">
      <dl className={s.dl}>
        <dt>Вуз</dt><dd>{institution.display_name}</dd>
        <dt>Профили</dt><dd>{profiles.map(x => PROFILE_LABEL[x]).join(', ')} · сейчас: {PROFILE_LABEL[profile]}</dd>
        {profile === 'student' && schedule && (
          <><dt>Учебная группа</dt><dd>{group.status === 'loading' ? '…' : group.data ?? 'Пока не назначена — обратитесь к куратору'}</dd></>
        )}
        <dt>Роли</dt><dd>{roles.length ? roles.join(', ') : 'Нет дополнительных ролей в текущем профиле'}</dd>
        <dt>ID пользователя</dt><dd><code style={{ wordBreak: 'break-all' }}>{userId}</code></dd>
      </dl>
      <div className={s.actions}>
        <Button variant="secondary" onClick={copy}>Скопировать ID</Button>
      </div>
      <p className={p.muted}>ID нужен администратору, чтобы добавить вас в вуз или в учебную группу.</p>
    </section>
  );
}

function EditSelf({ api, card, onClose, onSaved }: { api: ProfilesApi; card: Versioned<ProfileCard>; onClose: () => void; onSaved: (v: Versioned<ProfileCard>) => void }) {
  const [name, setName] = useState(card.data.display_name ?? '');
  const [about, setAbout] = useState(card.data.about);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return setError('Укажите имя — так вас увидят другие участники.');
    setBusy(true); setError(null);
    try {
      onSaved(await api.patchMe({ display_name: name.trim(), about: about.trim() }, card.etag));
      toast('Профиль сохранён');
    } catch (err) { setError(humanMessage(err)); setBusy(false); }
  };
  return (
    <Modal title="Мой профиль" onClose={onClose} busy={busy} actions={<>
      <Button variant="secondary" onClick={onClose} disabled={busy}>Отмена</Button>
      <Button type="submit" form="self-form" loading={busy}>Сохранить</Button>
    </>}>
      <form id="self-form" className={p.formGrid} onSubmit={submit} noValidate>
        <Input label="Имя и фамилия" value={name} onChange={e => setName(e.target.value)} maxLength={200} error={error && !name.trim() ? error : null} />
        <TextArea label="О себе" value={about} onChange={e => setAbout(e.target.value)} maxLength={2000} hint="До 2000 символов" />
        {error && name.trim() && <p role="alert" className={p.formError}>{error}</p>}
      </form>
    </Modal>
  );
}

function EditAcademic({ api, userId, isMe, card, onClose, onSaved }: {
  api: ProfilesApi; userId: string; isMe: boolean; card: Versioned<ProfileCard>; onClose: () => void; onSaved: (v: Versioned<ProfileCard>) => void;
}) {
  const [position, setPosition] = useState(card.data.position ?? '');
  const [degree, setDegree] = useState(card.data.academic_degree ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      // Пустое поле очищает значение (null по контракту).
      const saved = await api.patchUser(userId, { position: position.trim() || null, academic_degree: degree.trim() || null }, card.etag);
      onSaved(isMe ? await api.getMe() : saved);
      toast('Данные сохранены');
    } catch (err) { setError(humanMessage(err)); setBusy(false); }
  };
  return (
    <Modal title="Должность и степень" onClose={onClose} busy={busy} actions={<>
      <Button variant="secondary" onClick={onClose} disabled={busy}>Отмена</Button>
      <Button type="submit" form="academic-form" loading={busy}>Сохранить</Button>
    </>}>
      <form id="academic-form" className={p.formGrid} onSubmit={submit} noValidate>
        <Input label="Должность" value={position} onChange={e => setPosition(e.target.value)} maxLength={200} placeholder="Например, доцент кафедры 42" />
        <Input label="Учёная степень" value={degree} onChange={e => setDegree(e.target.value)} maxLength={200} placeholder="Например, кандидат технических наук" />
        {error && <p role="alert" className={p.formError}>{error}</p>}
      </form>
    </Modal>
  );
}
