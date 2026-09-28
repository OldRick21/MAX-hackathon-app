import type { Submission, SubmissionStatus } from '../../api/types';
import { IconFile } from '../../components/icons/ui';
import { Badge, type BadgeTone } from '../../components/ui';
import { formatBytes } from '../../utils/links';
import { formatDate } from '../../utils/time';
import type { ReactNode } from 'react';
import s from './coursework.module.css';

/** Статусы API → подписи. Черновика и «Загружено» в контракте нет: работа сразу уходит на проверку. */
export const STATUS: Record<SubmissionStatus, { label: string; tone: BadgeTone }> = {
  submitted: { label: 'На проверке', tone: 'warning' },
  changes_requested: { label: 'Требует доработки', tone: 'error' },
  accepted: { label: 'Проверено', tone: 'success' },
};

export function StatusBadge({ status }: { status: SubmissionStatus }) {
  return <Badge tone={STATUS[status].tone} dot>{STATUS[status].label}</Badge>;
}

export function CourseworkCard({ item, personLabel, personName, actions }: {
  item: Submission; personLabel: string; personName: string; actions: ReactNode;
}) {
  const r = item.review;
  return (
    <article className={s.card}>
      <div className={s.cardTop}>
        <h3 className={s.cardTitle}>{item.title}</h3>
        <StatusBadge status={item.status} />
      </div>
      <div className={s.meta}>
        <span>{personLabel}: <strong>{personName}</strong></span>
        <span>Загружено {formatDate(item.created_at)}</span>
        {item.version > 1 && <span>Обновлено {formatDate(item.updated_at)}</span>}
      </div>
      <div className={s.file}>
        <IconFile />
        <span>{item.file.original_name} · {formatBytes(item.file.size_bytes)} · версия {item.version}</span>
      </div>
      {r && (
        <div className={`${s.review} ${r.decision === 'accepted' ? s.accepted : s.changes}`}>
          <span className={s.reviewHead}>{r.decision === 'accepted' ? 'Работа принята' : 'Нужно доработать'}</span>
          {r.comment && <span className={s.reviewText}>{r.comment}</span>}
          <span className={s.reviewDate}>Отзыв от {formatDate(r.reviewed_at)}</span>
        </div>
      )}
      <div className={s.actions}>{actions}</div>
    </article>
  );
}
