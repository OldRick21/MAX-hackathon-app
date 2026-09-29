import { useEffect, useState, type ReactNode } from 'react';
import { request } from '../../api/http';
import { IconArrowRight, IconLock } from '../../components/icons/ui';
import { Button, LoadingState, Modal, TextArea } from '../../components/ui';
import { useSession } from '../../state/session';
import { Frame } from './EntryScreens';
import './privacy.css';

type Document = { version: string; text: string; policy: string; demo: boolean };

function PrivacyDetails({ title, meta, children, open = false }: {
  title: string; meta?: string; children: ReactNode; open?: boolean;
}) {
  return (
    <details className="privacy-disclosure" open={open}>
      <summary>
        <span>{title}</span>
        {meta && <small>{meta}</small>}
      </summary>
      <div className="privacy-document">{children}</div>
    </details>
  );
}

export function ConsentScreen() {
  const { accept } = useSession();
  const [doc, setDoc] = useState<Document>();
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [declined, setDeclined] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { request<Document>('/api/v1/privacy/document').then(r => setDoc(r.data)).catch(() => setError('Не удалось загрузить документы. Обновите страницу.')); }, []);
  async function proceed() {
    if (!doc || !checked || busy) return;
    setBusy(true); setError('');
    try {
      const { data } = await request<{ challenge: string; version: string }>('/api/v1/privacy/challenge', { method: 'POST' });
      if (data.version !== doc.version) throw new Error('Текст обновлён. Обновите страницу и прочитайте новую версию.');
      await accept({ consent_version: doc.version, consent_challenge: data.challenge });
    } catch (e) { setError(e instanceof Error ? e.message : 'Не удалось сохранить согласие'); }
    finally { setBusy(false); }
  }
  return (
    <Frame>
      <main className="privacy-consent">
        <div className="privacy-heading">
          <span className="privacy-heading-icon" aria-hidden="true"><IconLock /></span>
          <div>
            <h1>Персональные данные</h1>
            <p>Ознакомьтесь с документами и подтвердите согласие, чтобы продолжить работу в приложении.</p>
          </div>
        </div>

        {doc?.demo && <p className="privacy-note" role="note">Демонстрационный текст для хакатона: сведения об операторе ещё не заполнены.</p>}

        {!doc && !error && <div className="privacy-loading"><LoadingState text="Загружаем документы…" /></div>}
        {doc && (
          <div className="privacy-documents">
            <PrivacyDetails title="Согласие на обработку данных" meta={doc.version} open>
              <p className="privacy-text">{doc.text}</p>
            </PrivacyDetails>
            <PrivacyDetails title="Политика обработки данных">
              <p className="privacy-text">{doc.policy}</p>
            </PrivacyDetails>
          </div>
        )}

        {doc && (
          <label className="privacy-check">
            <input type="checkbox" checked={checked} onChange={e => { setChecked(e.target.checked); setDeclined(false); }} />
            <span>Я даю согласие на обработку персональных данных в соответствии с приведённым текстом.</span>
          </label>
        )}

        <div className="privacy-actions">
          <Button disabled={!doc || !checked} loading={busy} onClick={proceed}>Согласиться и продолжить</Button>
          <Button variant="ghost" disabled={busy} onClick={() => { setDeclined(true); setChecked(false); }}>Отказаться</Button>
        </div>
        {declined && <p className="privacy-feedback" role="status">Аккаунт не создан. Можно закрыть приложение или вернуться к согласию.</p>}
        {error && <p className="privacy-feedback privacy-feedback-error" role="alert">{error}</p>}
      </main>
    </Frame>
  );
}

export function ErasureScreen({ receipt }: { receipt: string }) {
  const [status, setStatus] = useState('Проверяем состояние удаления…');
  useEffect(() => {
    let alive = true;
    async function check() {
      try {
        const { data } = await request<{ status: string; pending_services: number }>('/api/v1/privacy/erasure-status', { token: receipt });
        if (alive) setStatus(data.status === 'completed' ? 'Удаление в подключённых хранилищах завершено.' : `Удаление выполняется. Ожидаем подтверждение от сервисов: ${data.pending_services}.`);
      } catch { if (alive) setStatus('Не удалось проверить состояние. Квитанция сохранена в этой вкладке; повторим проверку.'); }
    }
    void check(); const timer = setInterval(check, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, [receipt]);
  return (
    <Frame>
      <main className="privacy-consent privacy-erasure">
        <div className="privacy-heading">
          <span className="privacy-heading-icon" aria-hidden="true"><IconLock /></span>
          <div><h1>Отзыв согласия</h1><p>Сервер завершает сессии и удаляет данные из подключённых хранилищ.</p></div>
        </div>
        <p className="privacy-status" role="status">{status}</p>
        <PrivacyDetails title="Квитанция для проверки удаления">
          <p>Сохраните её и не передавайте другим.</p>
          <code className="privacy-receipt">{receipt}</code>
        </PrivacyDetails>
        <div className="privacy-actions">
          <Button variant="secondary" onClick={() => { sessionStorage.removeItem('privacy-erasure-receipt'); location.reload(); }}>Вернуться ко входу</Button>
        </div>
      </main>
    </Frame>
  );
}

export function PrivacySettings() {
  const { backend, withdrawn } = useSession();
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [doc, setDoc] = useState<{ text: string; accepted_at: string }>();
  const [message, setMessage] = useState('');
  const [feedback, setFeedback] = useState('');
  const [busy, setBusy] = useState(false);
  const [loadingDoc, setLoadingDoc] = useState(false);
  async function show() {
    setOpen(true); setFeedback(''); setLoadingDoc(true);
    try { setDoc(await backend?.coreCall?.('/api/v1/privacy/me')); }
    catch { setFeedback('Не удалось загрузить подтверждение согласия.'); }
    finally { setLoadingDoc(false); }
  }
  async function revoke() {
    if (!backend?.coreCall || busy) return;
    setBusy(true); setFeedback('');
    // Persist the capability before sending: a lost response must not lose the receipt.
    const receipt = Array.from(crypto.getRandomValues(new Uint8Array(32)), b => b.toString(16).padStart(2, '0')).join('');
    sessionStorage.setItem('privacy-erasure-receipt', receipt);
    try {
      const r = await backend.coreCall<{ receipt: string }>('/api/v1/privacy/withdraw', { method: 'POST', headers: { 'X-Erasure-Receipt': receipt } });
      withdrawn(r.receipt);
    } catch {
      try {
        await request('/api/v1/privacy/erasure-status', { token: receipt });
        withdrawn(receipt);
      } catch {
        setFeedback('Результат запроса неизвестен. Квитанция сохранена; после восстановления связи проверьте её, обновив страницу.');
      }
    }
    finally { setBusy(false); }
  }
  async function sendRequest() {
    if (!backend?.coreCall || !message.trim()) return;
    setBusy(true); setFeedback('');
    try {
      const r = await backend.coreCall<{ id: string }>('/api/v1/privacy/requests', { method: 'POST', body: { message } });
      setFeedback(`Обращение зарегистрировано: ${r.id}`); setMessage('');
    } catch { setFeedback('Не удалось зарегистрировать обращение.'); }
    finally { setBusy(false); }
  }
  const close = () => { if (!busy) { setOpen(false); setConfirm(false); } };
  return (
    <section className="privacy-home-footer" aria-label="Настройки персональных данных">
      <button type="button" className="privacy-launch" onClick={show}>
        <IconLock aria-hidden="true" />
        <span><strong>Персональные данные</strong><small>Согласие и обращения</small></span>
        <IconArrowRight aria-hidden="true" />
      </button>
      {open && (
        <Modal title="Персональные данные" onClose={close} busy={busy}>
          <p className="privacy-modal-intro">Здесь можно посмотреть принятое согласие, направить обращение или отозвать согласие на обработку данных.</p>

          {loadingDoc ? <div className="privacy-modal-loading"><LoadingState text="Загружаем согласие…" /></div> : doc && (
            <PrivacyDetails title="Моё согласие" meta={new Date(doc.accepted_at).toLocaleDateString('ru')}>
              <p className="privacy-text">{doc.text}</p>
            </PrivacyDetails>
          )}

          <div className="privacy-request">
            <TextArea
              label="Запрос сведений или исправления"
              hint="Опишите, какие сведения хотите получить или исправить. До 2000 символов."
              value={message}
              maxLength={2000}
              onChange={e => setMessage(e.target.value)}
            />
            <div><Button variant="secondary" size="small" loading={busy} disabled={!message.trim() || !backend?.coreCall} onClick={sendRequest}>Отправить обращение</Button></div>
          </div>

          <section className="privacy-danger-zone">
            <div>
              <h3>Отозвать согласие</h3>
              <p>Все сессии завершатся, а удаление данных запустится во всех вузах и сервисах.</p>
            </div>
            {!confirm ? (
              <Button variant="danger" size="small" disabled={!backend?.coreCall} onClick={() => setConfirm(true)}>Отозвать согласие</Button>
            ) : (
              <div className="privacy-confirm">
                <p>Это действие нельзя отменить. Продолжить?</p>
                <div className="privacy-actions">
                  <Button variant="danger" size="small" loading={busy} onClick={revoke}>Подтвердить отзыв</Button>
                  <Button variant="ghost" size="small" disabled={busy} onClick={() => setConfirm(false)}>Отмена</Button>
                </div>
              </div>
            )}
          </section>
          {feedback && <p className="privacy-feedback" role="status">{feedback}</p>}
        </Modal>
      )}
    </section>
  );
}
