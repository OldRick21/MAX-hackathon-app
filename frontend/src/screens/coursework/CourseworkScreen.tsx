import { useMemo, useRef, useState, type FormEvent } from 'react';
import type { CourseworkApi, ProfilesApi } from '../../api/backend';
import { humanMessage } from '../../api/http';
import type { ProfileCard, ServiceView, Submission, SubmissionStatus } from '../../api/types';
import { SectionGate } from '../../components/SectionGate';
import { IconDownload, IconFile, IconUpload } from '../../components/icons/ui';
import { Button, EmptyState, ErrorState, Input, Modal, Select, Skeleton, TextArea, toast } from '../../components/ui';
import { useAsync } from '../../hooks/useAsync';
import { useProfilesApi } from '../../hooks/useServices';
import { useUserNames } from '../../hooks/useUserNames';
import { useBackend, useSession } from '../../state/session';
import { formatBytes, saveBlob } from '../../utils/links';
import p from '../pages.module.css';
import { CourseworkCard, STATUS } from './CourseworkCard';
import { UploadArea, validatePdf } from './UploadArea';
import s from './coursework.module.css';

export function CourseworkScreen() {
  return <SectionGate section="coursework">{service => <Coursework service={service} />}</SectionGate>;
}

const FILTERS: { key: SubmissionStatus | 'all'; label: string }[] = [
  { key: 'all', label: 'Все' },
  { key: 'submitted', label: STATUS.submitted.label },
  { key: 'changes_requested', label: STATUS.changes_requested.label },
  { key: 'accepted', label: STATUS.accepted.label },
];

function Coursework({ service }: { service: ServiceView }) {
  const backend = useBackend();
  const { user } = useSession();
  const api = useMemo(() => backend.coursework(service), [backend, service]);
  const profiles = useProfilesApi();
  const [filter, setFilter] = useState<SubmissionStatus | 'all'>('all');
  const [reviewing, setReviewing] = useState<Submission | null>(null);
  const [deleting, setDeleting] = useState<Submission | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const replaceInput = useRef<HTMLInputElement>(null);
  const replaceTarget = useRef<Submission | null>(null);

  const role = service.profile;
  const canManage = role === 'admin' && service.permissions.includes('coursework.manage');
  const list = useAsync(signal => api.list(filter === 'all' ? undefined : filter, signal), [api, filter]);
  const items = list.data ?? [];
  const names = useUserNames(profiles?.api ?? null, profiles?.service.id ?? '',
    items.flatMap(i => [i.teacher_id, i.student_id]));

  const download = async (item: Submission) => {
    setBusyId(item.id);
    try { saveBlob(await api.download(item.id), item.file.original_name); }
    catch (e) { toast(humanMessage(e), true); }
    finally { setBusyId(null); }
  };

  const replace = async (file: File) => {
    const item = replaceTarget.current;
    if (!item) return;
    const problem = validatePdf(file);
    if (problem) return toast(problem, true);
    setBusyId(item.id);
    try {
      const { etag } = await api.get(item.id);
      await api.replaceFile(item.id, file, etag);
      toast('Новая версия загружена и отправлена на проверку');
      list.reload();
    } catch (e) { toast(humanMessage(e), true); }
    finally { setBusyId(null); }
  };

  const actionsFor = (item: Submission) => {
    const own = role === 'student' && item.student_id === user?.id;
    const reviewer = role === 'teacher' && item.teacher_id === user?.id && item.student_id !== user?.id;
    const busy = busyId === item.id;
    return (
      <>
        <Button variant="secondary" size="small" icon={<IconDownload width="1.2em" height="1.2em" />} onClick={() => download(item)} disabled={busy}>Скачать</Button>
        {reviewer && item.status === 'submitted' && <Button size="small" onClick={() => setReviewing(item)} disabled={busy}>Проверить</Button>}
        {own && item.status !== 'accepted' && (
          <Button variant="secondary" size="small" icon={<IconUpload width="1.2em" height="1.2em" />} loading={busy}
            onClick={() => { replaceTarget.current = item; replaceInput.current?.click(); }}>
            Загрузить новую версию
          </Button>
        )}
        {((own && item.status !== 'accepted') || canManage) && (
          <Button variant="ghost" size="small" onClick={() => setDeleting(item)} disabled={busy} style={{ color: 'var(--error)' }}>Удалить</Button>
        )}
      </>
    );
  };

  const empty = {
    student: { title: 'У вас пока нет загруженных работ', text: 'Загрузите PDF с курсовой работой — преподаватель получит её на проверку.' },
    teacher: { title: 'Работ на проверку пока нет', text: 'Здесь появятся работы студентов, которые выбрали вас проверяющим.' },
    admin: { title: 'Работ пока нет', text: 'Здесь появятся все работы, загруженные в вузе.' },
  }[role];

  return (
    <div>
      <div className={p.pageHead}>
        <div>
          <h1 className={p.title}>Курсовые работы</h1>
          <p className={p.subtitle}>
            {role === 'student' ? 'Загрузка и статус ваших работ' : role === 'teacher' ? 'Работы, назначенные вам на проверку' : 'Все работы вуза'}
          </p>
        </div>
      </div>

      <div className={s.layout}>
        {role === 'student' && <NewSubmission api={api} profiles={profiles?.api ?? null} meId={user?.id ?? ''} onCreated={() => { setFilter('all'); list.reload(); }} />}

        <section aria-label="Список работ">
          <div className={s.sectionHead}>
            <h2 className={p.sectionTitle}>{role === 'student' ? 'Мои работы' : 'Работы'}</h2>
            <div className={p.tabs} role="group" aria-label="Фильтр по статусу">
              {FILTERS.map(f => (
                <button key={f.key} type="button" className={p.tab} aria-pressed={filter === f.key} onClick={() => setFilter(f.key)}>{f.label}</button>
              ))}
            </div>
          </div>
          {list.status === 'error' ? (
            <ErrorState title="Не удалось загрузить работы" text={humanMessage(list.error)} onRetry={list.reload} />
          ) : list.status === 'loading' ? (
            <div className={s.list} aria-busy="true" aria-label="Загрузка работ"><Skeleton className={s.skeletonCard} /><Skeleton className={s.skeletonCard} /></div>
          ) : items.length === 0 ? (
            filter === 'all'
              ? <EmptyState icon={<IconFile />} title={empty.title} text={empty.text} />
              : <EmptyState icon={<IconFile />} title="В этом статусе работ нет">
                  <Button variant="secondary" onClick={() => setFilter('all')}>Показать все</Button>
                </EmptyState>
          ) : (
            <ul className={s.list}>
              {items.map(item => (
                <li key={item.id}>
                  <CourseworkCard
                    item={item}
                    personLabel={role === 'student' ? 'Преподаватель' : 'Студент'}
                    personName={personName(names[role === 'student' ? item.teacher_id : item.student_id])}
                    actions={actionsFor(item)}
                  />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <input ref={replaceInput} type="file" accept="application/pdf,.pdf" hidden
        onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) void replace(f); }} />
      {reviewing && <ReviewModal api={api} item={reviewing} studentName={names[reviewing.student_id]} onClose={() => setReviewing(null)} onDone={() => { setReviewing(null); list.reload(); }} />}
      {deleting && <DeleteModal api={api} item={deleting} onClose={() => setDeleting(null)} onDone={() => { setDeleting(null); list.reload(); }} />}
    </div>
  );
}

function NewSubmission({ api, profiles, meId, onCreated }: { api: CourseworkApi; profiles: ProfilesApi | null; meId: string; onCreated: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [teacher, setTeacher] = useState('');
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Проверяющий выбирается из анкет; тип профиля в анкете не хранится, поэтому сервер сам проверит, что это преподаватель.
  const people = useAsync<ProfileCard[]>(async signal => (profiles ? (await profiles.listUsers('', null, signal)).items : []), [profiles]);
  const candidates = (people.data ?? []).filter(c => c.user_id !== meId);

  const pick = (f: File) => {
    const problem = validatePdf(f);
    if (problem) { setError(problem); return; }
    setError(null);
    setFile(f);
    if (!title) setTitle(f.name.replace(/\.pdf$/i, '').replace(/[_]+/g, ' '));
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!file) return;
    if (!title.trim()) return setError('Укажите тему работы.');
    if (!teacher) return setError('Выберите преподавателя, который проверит работу.');
    setError(null);
    setProgress(0);
    try {
      await api.create({ title: title.trim(), teacher_id: teacher, file }, (l, t) => setProgress(Math.round((l / t) * 100)));
      toast('Работа загружена и отправлена на проверку');
      setFile(null); setTitle(''); setTeacher(''); setProgress(null);
      onCreated();
    } catch (err) {
      setError(humanMessage(err));
      setProgress(null);
    }
  };

  const uploading = progress !== null;
  if (!file) {
    return (
      <section aria-label="Загрузка работы">
        <UploadArea onFile={pick} />
        {error && <p role="alert" className={p.formError} style={{ marginTop: '1.2rem' }}>{error}</p>}
      </section>
    );
  }
  return (
    <form className={s.form} onSubmit={submit} noValidate aria-label="Новая работа">
      <div className={s.fileChip}>
        <IconFile />
        <span style={{ minWidth: 0, flex: 1 }}>
          <span className={s.fileName} style={{ display: 'block' }}>{file.name}</span>
          <span className={s.fileSize}>{formatBytes(file.size)}</span>
        </span>
        {!uploading && <Button variant="ghost" size="small" onClick={() => { setFile(null); setError(null); }}>Заменить</Button>}
      </div>
      <Input label="Тема работы" value={title} onChange={e => setTitle(e.target.value)} maxLength={200} disabled={uploading} />
      {profiles ? (
        <Select label="Преподаватель" value={teacher} onChange={e => setTeacher(e.target.value)} disabled={uploading || people.status === 'loading'}
          hint={people.status === 'loading' ? 'Загружаем список…' : undefined}>
          <option value="">Выберите преподавателя</option>
          {candidates.map(c => (
            <option key={c.user_id} value={c.user_id}>{c.display_name}{c.position ? ` — ${c.position}` : ''}</option>
          ))}
        </Select>
      ) : (
        <Input label="Идентификатор преподавателя" hint="Сервис анкет не подключён — узнайте идентификатор у преподавателя"
          value={teacher} onChange={e => setTeacher(e.target.value.trim())} disabled={uploading} />
      )}
      {uploading && (
        <div role="status" aria-live="polite">
          <div className={s.progress}><span style={{ width: `${progress}%` }} /></div>
          <p className={s.progressText} style={{ marginTop: '.8rem' }}>Загрузка… {progress}%</p>
        </div>
      )}
      {error && <p role="alert" className={p.formError}>{error}</p>}
      <div><Button type="submit" loading={uploading}>Отправить на проверку</Button></div>
    </form>
  );
}

function ReviewModal({ api, item, studentName, onClose, onDone }: {
  api: CourseworkApi; item: Submission; studentName?: string; onClose: () => void; onDone: () => void;
}) {
  const [decision, setDecision] = useState<'accepted' | 'changes_requested'>('accepted');
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (decision === 'changes_requested' && !comment.trim()) return setError('Напишите, что нужно исправить.');
    setBusy(true); setError(null);
    try {
      // Версию берём свежую: если студент успел загрузить новый файл, сервер вернёт 412.
      const { etag } = await api.get(item.id);
      await api.review(item.id, { decision, comment: comment.trim() }, etag);
      toast(decision === 'accepted' ? 'Работа принята' : 'Работа отправлена на доработку');
      onDone();
    } catch (err) { setError(humanMessage(err)); setBusy(false); }
  };
  return (
    <Modal title="Проверка работы" onClose={onClose} busy={busy} actions={<>
      <Button variant="secondary" onClick={onClose} disabled={busy}>Отмена</Button>
      <Button type="submit" form="review-form" loading={busy}>Отправить отзыв</Button>
    </>}>
      <form id="review-form" className={p.formGrid} onSubmit={submit} noValidate>
        <p><strong>{item.title}</strong>{studentName && <span className={p.muted}> — {studentName}</span>}</p>
        <fieldset className={`${p.fieldset} ${s.decision}`}>
          <legend className={p.legend}>Решение</legend>
          <label className={s.radio}><input type="radio" name="decision" checked={decision === 'accepted'} onChange={() => setDecision('accepted')} />Принять работу</label>
          <label className={s.radio}><input type="radio" name="decision" checked={decision === 'changes_requested'} onChange={() => setDecision('changes_requested')} />Отправить на доработку</label>
        </fieldset>
        <TextArea label="Комментарий" value={comment} onChange={e => setComment(e.target.value)} maxLength={2000}
          hint={decision === 'changes_requested' ? 'Опишите, что нужно исправить' : 'Необязательно'} />
        {error && <p role="alert" className={p.formError}>{error}</p>}
      </form>
    </Modal>
  );
}

function DeleteModal({ api, item, onClose, onDone }: { api: CourseworkApi; item: Submission; onClose: () => void; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const confirm = async () => {
    setBusy(true);
    try {
      const { etag } = await api.get(item.id);
      await api.remove(item.id, etag);
      toast('Работа удалена');
      onDone();
    } catch (e) { toast(humanMessage(e), true); setBusy(false); }
  };
  return (
    <Modal title="Удалить работу?" onClose={onClose} busy={busy} actions={<>
      <Button variant="secondary" onClick={onClose} disabled={busy}>Отмена</Button>
      <Button variant="danger" onClick={confirm} loading={busy}>Удалить</Button>
    </>}>
      <p className={p.muted}>«{item.title}» будет удалена вместе с файлом. Отменить это действие нельзя.</p>
    </Modal>
  );
}

const personName = (name: string | undefined) => name === undefined ? '…' : name || 'Имя не указано';
