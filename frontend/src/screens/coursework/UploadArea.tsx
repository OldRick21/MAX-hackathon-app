import { useId, useRef, useState, type DragEvent } from 'react';
import { IconUpload } from '../../components/icons/ui';
import { Button } from '../../components/ui';
import s from './coursework.module.css';

export const MAX_FILE = 20 * 1024 * 1024;

/** Проверка до отправки: ограничения из спецификации курсовых (PDF, не больше 20 МБ, не пустой). */
export function validatePdf(file: File): string | null {
  const isPdf = file.type === 'application/pdf' || (!file.type && /\.pdf$/i.test(file.name));
  if (!isPdf) return 'Можно загрузить только PDF-файл.';
  if (file.size === 0) return 'Файл пустой. Выберите другой.';
  if (file.size > MAX_FILE) return 'Файл слишком большой. Максимальный размер — 20 МБ.';
  return null;
}

export function UploadArea({ onFile, disabled }: { onFile: (f: File) => void; disabled?: boolean }) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const id = useId();
  const drop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f && !disabled) onFile(f);
  };
  return (
    <div
      className={`${s.drop} ${over ? s.over : ''}`}
      onDragOver={e => { e.preventDefault(); if (!disabled) setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
    >
      <span className={s.dropIcon}><IconUpload /></span>
      <p className={s.dropTitle} id={`${id}-t`}>Перетащите файл сюда</p>
      <p className={s.or}>или</p>
      <Button onClick={() => input.current?.click()} disabled={disabled} aria-describedby={`${id}-h`}>Выбрать файл</Button>
      <p className={s.dropText} id={`${id}-h`}>PDF, до 20 МБ</p>
      <input
        ref={input}
        type="file"
        accept="application/pdf,.pdf"
        hidden
        onChange={e => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ''; }}
      />
    </div>
  );
}
