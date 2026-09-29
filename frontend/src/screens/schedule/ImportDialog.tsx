// Импорт расписания из файла: один формат — таблица занятий, разные типы файлов.
// Сначала проверка (предпросмотр и ошибки по строкам), затем загрузка — всё или ничего.
import { useState } from 'react';
import type { ScheduleApi } from '../../api/backend';
import { humanMessage } from '../../api/http';
import type { ScheduleImportResult } from '../../api/types';
import { Button, Modal, toast } from '../../components/ui';
import { formatShort, formatTime, formatWeekday } from '../../utils/time';
import p from '../pages.module.css';
import s from './schedule.module.css';

const ACCEPT = '.json,.xlsx,.csv,.txt,.xml';
const COLUMNS = 'Дата, Начало, Конец, Дисциплина, Группы, Преподаватели, Аудитория, Комментарий, Статус, Тип (лекция, семинар, лабораторная)';

export function ImportDialog({ api, groupName, onClose, onDone }: {
  api: ScheduleApi; groupName: Map<string, string>; onClose: () => void; onDone: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [check, setCheck] = useState<ScheduleImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const pick = async (f: File | null) => {
    setFile(f); setCheck(null); setError(null);
    if (!f) return;
    setBusy(true);
    try { setCheck(await api.importFile(f, true)); }
    catch (e) { setError(humanMessage(e, 'Не удалось прочитать файл.')); }
    finally { setBusy(false); }
  };

  const upload = async () => {
    if (!file) return;
    setBusy(true);
    try {
      const done = await api.importFile(file, false);
      toast(done.created ? `Загружено занятий: ${done.created}` : 'Новых занятий нет — всё уже в расписании');
      onDone();
    } catch (e) {
      setError(humanMessage(e, 'Не удалось загрузить расписание.'));
      setBusy(false);
    }
  };

  const ready = check && !check.errors.length && check.to_create > 0;
  return (
    <Modal title="Импорт расписания" onClose={onClose} busy={busy} actions={<>
      <Button variant="secondary" onClick={onClose} disabled={busy}>Отмена</Button>
      <Button onClick={upload} loading={busy && !!check} disabled={!ready || busy}>
        {check && check.to_create ? `Загрузить ${check.to_create}` : 'Загрузить'}
      </Button>
    </>}>
      <div className={s.importBody}>
        <p className={p.muted}>
          Один формат — таблица занятий, файл любого из типов: JSON, Excel (.xlsx), выгрузка 1С (.csv или .txt), XML.
          Первая строка — заголовки: {COLUMNS}. Время — местное, группы и преподаватели — через запятую,
          преподаватель — ФИО как при вступлении в вуз. Уже существующие занятия пропускаются.
        </p>
        <label className={s.importPick}>
          <input type="file" accept={ACCEPT} disabled={busy} onChange={e => void pick(e.target.files?.[0] ?? null)} />
          <span className={s.importPickButton}>{file ? 'Другой файл' : 'Выбрать файл'}</span>
          <span className={s.importFileName}>{file ? file.name : '.json, .xlsx, .csv, .txt, .xml — до 2 МБ'}</span>
        </label>
        {busy && !check && <p className={p.muted}>Проверяем файл…</p>}
        {error && <p className={s.importError} role="alert">{error}</p>}
        {check && (
          <div className={s.importResult}>
            <p><strong>{check.to_create}</strong> новых занятий из {check.total}
              {check.duplicates ? `, уже в расписании: ${check.duplicates}` : ''}
              {check.errors.length ? `, с ошибками: ${check.errors.length}` : ''}.</p>
            {check.errors.length > 0 && (
              <>
                <p className={s.importError}>Исправьте строки в файле и выберите его снова — пока есть ошибки, ничего не загружается.</p>
                <ul className={s.importErrors}>
                  {check.errors.slice(0, 50).map(e => <li key={e.row}><strong>Строка {e.row}:</strong> {e.message}</li>)}
                </ul>
              </>
            )}
            {check.preview.length > 0 && (
              <ul className={s.importPreview}>
                {check.preview.slice(0, 8).map((e, i) => (
                  <li key={i} className={e.status === 'cancelled' ? s.importCancelled : undefined}>
                    <span>{formatWeekday(new Date(e.starts_at))}, {formatShort(new Date(e.starts_at))} · {formatTime(e.starts_at)}–{formatTime(e.ends_at)}</span>
                    <span>{e.title}</span>
                    <span className={p.muted}>{e.group_ids.map(g => groupName.get(g) ?? '—').join(', ')}</span>
                  </li>
                ))}
                {check.to_create > 8 && <li className={p.muted}>…и ещё {check.to_create - 8}</li>}
              </ul>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
