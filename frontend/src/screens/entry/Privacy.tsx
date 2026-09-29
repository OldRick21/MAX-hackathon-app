import { useEffect, useState } from 'react';
import { request } from '../../api/http';
import { useSession } from '../../state/session';
import './privacy.css';

type Document = { version: string; text: string; policy: string; demo: boolean };

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
  return <main className="privacy-card">
    <h1>Персональные данные</h1>
    <p>Для работы приложения требуется ваше согласие. Создание аккаунта произойдёт после подтверждения.</p>
    {doc?.demo && <p role="note">Демонстрационный текст для хакатона: сведения об операторе ещё не заполнены.</p>}
    {doc && <><details open><summary>Согласие · {doc.version}</summary><p className="privacy-text">{doc.text}</p></details>
      <details><summary>Политика обработки данных</summary><p className="privacy-text">{doc.policy}</p></details>
      <label><input type="checkbox" checked={checked} onChange={e => { setChecked(e.target.checked); setDeclined(false); }} /> Я даю согласие на обработку персональных данных по приведённому тексту.</label></>}
    <div className="privacy-actions"><button disabled={!doc || !checked || busy} onClick={proceed}>{busy ? 'Сохраняем…' : 'Согласиться и продолжить'}</button>
      <button disabled={busy} onClick={() => { setDeclined(true); setChecked(false); }}>Отказаться</button></div>
    {declined && <p role="status">Аккаунт не создан. Можно закрыть приложение или вернуться к согласию.</p>}
    {error && <p role="alert">{error}</p>}
  </main>;
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
  return <main className="privacy-card"><h1>Состояние отзыва согласия</h1><p>После подтверждения сервером все сессии завершаются. Повторный вход не отменяет удаление старых данных.</p>
    <p role="status">{status}</p><details><summary>Квитанция для проверки удаления</summary><p>Сохраните её и не передавайте другим.</p><code className="privacy-receipt">{receipt}</code></details>
    <button onClick={() => { sessionStorage.removeItem('privacy-erasure-receipt'); location.reload(); }}>Вернуться ко входу</button></main>;
}

export function PrivacySettings() {
  const { backend, withdrawn } = useSession();
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [doc, setDoc] = useState<{ text: string; accepted_at: string }>();
  const [message, setMessage] = useState('');
  const [feedback, setFeedback] = useState('');
  const [busy, setBusy] = useState(false);
  async function show() {
    setOpen(true);
    try { setDoc(await backend?.coreCall?.('/api/v1/privacy/me')); }
    catch { setFeedback('Не удалось загрузить подтверждение согласия.'); }
  }
  async function revoke() {
    if (!backend?.coreCall || busy) return;
    setBusy(true);
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
    setBusy(true);
    try {
      const r = await backend.coreCall<{ id: string }>('/api/v1/privacy/requests', { method: 'POST', body: { message } });
      setFeedback(`Обращение зарегистрировано: ${r.id}`); setMessage('');
    } catch { setFeedback('Не удалось зарегистрировать обращение.'); }
    finally { setBusy(false); }
  }
  return <><button className="privacy-launch" onClick={show}>Персональные данные</button>
    {open && <div className="privacy-overlay"><section className="privacy-card" role="dialog" aria-modal="true" aria-label="Персональные данные">
      <button onClick={() => setOpen(false)} disabled={busy}>Закрыть</button><h2>Персональные данные</h2>
      {doc && <details><summary>Моё согласие · {new Date(doc.accepted_at).toLocaleDateString('ru')}</summary><p className="privacy-text">{doc.text}</p></details>}
      <label>Запрос сведений или исправления<textarea value={message} maxLength={2000} onChange={e => setMessage(e.target.value)} /></label>
      <button disabled={busy || !message.trim() || !backend?.coreCall} onClick={sendRequest}>Отправить обращение</button>
      <p>Отзыв завершит все сессии и запустит удаление ваших данных из всех вузов и сервисов.</p>
      {!confirm ? <button disabled={!backend?.coreCall} onClick={() => setConfirm(true)}>Отозвать согласие</button>
        : <div className="privacy-actions"><button disabled={busy} onClick={revoke}>Подтверждаю отзыв и удаление</button><button disabled={busy} onClick={() => setConfirm(false)}>Отмена</button></div>}
      {feedback && <p role="status">{feedback}</p>}
    </section></div>}</>;
}
