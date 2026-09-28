// Базовые компоненты интерфейса (handoff, раздел 20).
import {
  forwardRef, useEffect, useId, useRef, useState,
  type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes,
} from 'react';
import { createPortal } from 'react-dom';
import { IconAlert, IconCheck, IconClose, IconSearch } from '../icons/ui';
import s from './ui.module.css';

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(' ');

// ---------- Button ----------

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'normal' | 'small';
  block?: boolean;
  loading?: boolean;
  icon?: ReactNode;
};

export function Button({ variant = 'primary', size = 'normal', block, loading, icon, children, className, disabled, ...rest }: ButtonProps) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cx(s.button, s[variant], size === 'small' && s.small, block && s.block, className)}
    >
      {loading ? <span className={s.spinner} aria-hidden="true" /> : icon}
      {children}
    </button>
  );
}

export const IconButton = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & { label: string }>(
  function IconButton({ label, className, children, ...rest }, ref) {
    return (
      <button ref={ref} type="button" aria-label={label} title={label} {...rest} className={cx(s.iconButton, className)}>
        {children}
      </button>
    );
  },
);

// ---------- Inputs ----------

type FieldProps = { label?: string; hint?: string; error?: string | null };

export function Input({ label, hint, error, className, id, ...rest }: InputHTMLAttributes<HTMLInputElement> & FieldProps) {
  const auto = useId();
  const inputId = id ?? auto;
  return (
    <div className={cx(s.field, className)}>
      {label && <label className={s.label} htmlFor={inputId}>{label}</label>}
      <input id={inputId} className={s.input} aria-invalid={!!error || undefined} aria-describedby={hint || error ? `${inputId}-d` : undefined} {...rest} />
      {(error || hint) && <span id={`${inputId}-d`} className={error ? s.error : s.hint}>{error || hint}</span>}
    </div>
  );
}

export function TextArea({ label, hint, error, className, id, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement> & FieldProps) {
  const auto = useId();
  const inputId = id ?? auto;
  return (
    <div className={cx(s.field, className)}>
      {label && <label className={s.label} htmlFor={inputId}>{label}</label>}
      <textarea id={inputId} className={s.input} aria-invalid={!!error || undefined} {...rest} />
      {(error || hint) && <span className={error ? s.error : s.hint}>{error || hint}</span>}
    </div>
  );
}

export function Select({ label, hint, error, className, id, children, ...rest }:
  React.SelectHTMLAttributes<HTMLSelectElement> & FieldProps) {
  const auto = useId();
  const inputId = id ?? auto;
  return (
    <div className={cx(s.field, className)}>
      {label && <label className={s.label} htmlFor={inputId}>{label}</label>}
      <select id={inputId} className={s.input} aria-invalid={!!error || undefined} {...rest}>{children}</select>
      {(error || hint) && <span className={error ? s.error : s.hint}>{error || hint}</span>}
    </div>
  );
}

export function SearchInput({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <div className={cx(s.searchWrap, className)}>
      <IconSearch />
      <input type="search" className={s.input} {...rest} />
    </div>
  );
}

// ---------- Avatar ----------

export function initials(name: string | null | undefined) {
  const parts = (name ?? '').trim().split(/\s+/).filter(Boolean);
  return (parts.slice(0, 2).map(p => p[0]).join('') || '?').toUpperCase();
}

export function Avatar({ name, src, size, className }: { name?: string | null; src?: string | null; size: string; className?: string }) {
  const [broken, setBroken] = useState(false);
  return (
    <span className={cx(s.avatar, className)} style={{ width: size, height: size, fontSize: `calc(${size} * 0.34)` }} aria-hidden="true">
      {src && !broken ? <img src={src} alt="" onError={() => setBroken(true)} /> : initials(name)}
    </span>
  );
}

// ---------- Badge ----------

export type BadgeTone = 'neutral' | 'accent' | 'success' | 'warning' | 'error' | 'info';

export function Badge({ tone = 'neutral', dot, children }: { tone?: BadgeTone; dot?: boolean; children: ReactNode }) {
  return <span className={cx(s.badge, tone !== 'neutral' && s[tone], dot && s.dot)}>{children}</span>;
}

// ---------- Loading / Empty / Error ----------

export const Spinner = ({ className }: { className?: string }) => <span className={cx(s.spinner, className)} aria-hidden="true" />;

export function Skeleton({ width, height, radius, className }: { width?: string; height?: string; radius?: string; className?: string }) {
  return <span className={cx(s.skeleton, className)} style={{ width, height, borderRadius: radius }} aria-hidden="true" />;
}

export function LoadingState({ text = 'Загрузка…' }: { text?: string }) {
  return <div className={s.loading} role="status"><Spinner />{text}</div>;
}

export function EmptyState({ icon, title, text, children }: { icon?: ReactNode; title: string; text?: string; children?: ReactNode }) {
  return (
    <div className={s.state}>
      {icon && <div className={s.stateIcon}>{icon}</div>}
      <p className={s.stateTitle}>{title}</p>
      {text && <p className={s.stateText}>{text}</p>}
      {children && <div className={s.stateActions}>{children}</div>}
    </div>
  );
}

export function ErrorState({ title = 'Не удалось загрузить данные', text, onRetry, children }:
  { title?: string; text?: string; onRetry?: () => void; children?: ReactNode }) {
  return (
    <div className={s.state} role="alert">
      <div className={cx(s.stateIcon, s.errorIcon)}><IconAlert /></div>
      <p className={s.stateTitle}>{title}</p>
      {text && <p className={s.stateText}>{text}</p>}
      {(onRetry || children) && (
        <div className={s.stateActions}>
          {onRetry && <Button onClick={onRetry}>Повторить</Button>}
          {children}
        </div>
      )}
    </div>
  );
}

// ---------- Modal ----------

export function Modal({ title, onClose, children, actions, busy }:
  { title: string; onClose: () => void; children: ReactNode; actions?: ReactNode; busy?: boolean }) {
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const first = ref.current?.querySelector<HTMLElement>('input, textarea, select, button:not([data-close])');
    (first ?? ref.current)?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) onClose(); };
    document.addEventListener('keydown', onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = overflow;
      prev?.focus?.();
    };
  }, [onClose, busy]);
  return createPortal(
    <div className={s.backdrop} onMouseDown={e => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div ref={ref} className={s.modal} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <div className={s.modalHead}>
          <h2 id={titleId} className={s.modalTitle}>{title}</h2>
          <IconButton label="Закрыть" data-close onClick={onClose} disabled={busy}><IconClose /></IconButton>
        </div>
        {children}
        {actions && <div className={s.modalActions}>{actions}</div>}
      </div>
    </div>,
    document.body,
  );
}

// ---------- Dropdown ----------

export interface DropdownItem {
  key: string;
  label: ReactNode;
  onSelect: () => void;
  checked?: boolean;
  danger?: boolean;
  /** Не закрывать меню после выбора (переключатели вроде оформления). */
  keepOpen?: boolean;
  /** Линия-разделитель перед пунктом. */
  divider?: boolean;
}

export function Dropdown({ trigger, items, align = 'start', label }: {
  trigger: (props: { open: boolean; toggle: () => void; ref: React.Ref<HTMLButtonElement>; 'aria-expanded': boolean; 'aria-haspopup': 'menu' }) => ReactNode;
  items: DropdownItem[];
  align?: 'start' | 'end';
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (!wrap.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setOpen(false); btn.current?.focus(); }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const els = [...(wrap.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? [])];
        const i = els.indexOf(document.activeElement as HTMLElement);
        els[(i + (e.key === 'ArrowDown' ? 1 : -1) + els.length) % els.length]?.focus();
      }
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    wrap.current?.querySelector<HTMLElement>('[role^="menuitem"]')?.focus();
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);
  const hasChecks = items.some(i => i.checked !== undefined);
  return (
    <div className={s.dropdown} ref={wrap}>
      {trigger({ open, toggle: () => setOpen(o => !o), ref: btn, 'aria-expanded': open, 'aria-haspopup': 'menu' })}
      {open && (
        <div className={cx(s.menu, align === 'end' && s.alignEnd)} role="menu" aria-label={label}>
          {items.map(item => (
            <button
              key={item.key}
              type="button"
              role={hasChecks ? 'menuitemradio' : 'menuitem'}
              aria-checked={hasChecks ? !!item.checked : undefined}
              className={cx(s.menuItem, item.danger && s.dangerItem, item.divider && s.menuDivider)}
              onClick={() => { if (!item.keepOpen) setOpen(false); item.onSelect(); }}
            >
              <span>{item.label}</span>
              {item.checked && <IconCheck className={s.menuCheck} />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------- Toast ----------

let pushToast: ((t: { text: string; error?: boolean }) => void) | null = null;
export const toast = (text: string, error = false) => pushToast?.({ text, error });

export function ToastHost() {
  const [t, setT] = useState<{ text: string; error?: boolean; id: number } | null>(null);
  useEffect(() => {
    let timer = 0;
    pushToast = v => {
      setT({ ...v, id: Date.now() });
      clearTimeout(timer);
      timer = window.setTimeout(() => setT(null), 3200);
    };
    return () => { pushToast = null; clearTimeout(timer); };
  }, []);
  if (!t) return null;
  // Ошибка отличается от успеха не только цветом, но и иконкой (handoff, раздел 33).
  return (
    <div key={t.id} className={cx(s.toast, t.error && s.toastError)} role={t.error ? 'alert' : 'status'} aria-live={t.error ? 'assertive' : 'polite'}>
      {t.error ? <IconAlert /> : <IconCheck />}
      <span>{t.text}</span>
    </div>
  );
}
