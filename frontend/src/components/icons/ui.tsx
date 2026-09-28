// Все иконки приложения — один стиль: контур 24×24, линия 2, скруглённые концы, цвет текста.
// Размер задаёт место использования (CSS), толщину линии не меняйте — иначе иконки разной «жирности».
import type { SVGProps } from 'react';

type P = SVGProps<SVGSVGElement>;
export const base = (props: P) => ({
  viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2,
  strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true, ...props,
});

export const IconSearch = (p: P) => <svg {...base(p)}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>;
export const IconDots = (p: P) => <svg {...base(p)}><circle cx="5" cy="12" r="1.3" fill="currentColor" /><circle cx="12" cy="12" r="1.3" fill="currentColor" /><circle cx="19" cy="12" r="1.3" fill="currentColor" /></svg>;
export const IconCheck = (p: P) => <svg {...base(p)}><path d="m5 12.5 4.5 4.5L19 7.5" /></svg>;
export const IconClose = (p: P) => <svg {...base(p)}><path d="M6 6l12 12M18 6 6 18" /></svg>;
export const IconUpload = (p: P) => <svg {...base(p)}><path d="M12 15V4M7.5 8.5 12 4l4.5 4.5" /><path d="M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15" /></svg>;
export const IconDownload = (p: P) => <svg {...base(p)}><path d="M12 4v11M7.5 10.5 12 15l4.5-4.5" /><path d="M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15" /></svg>;
export const IconFile = (p: P) => <svg {...base(p)}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5M9 13h6M9 17h4" /></svg>;
export const IconCalendarEmpty = (p: P) => <svg {...base(p)}><rect x="3.5" y="5" width="17" height="15.5" rx="2.5" /><path d="M3.5 10h17M8 3v4M16 3v4M10 15h4" /></svg>;
export const IconAlert = (p: P) => <svg {...base(p)}><circle cx="12" cy="12" r="9" /><path d="M12 7.5v5.5M12 16.5v.01" /></svg>;
export const IconLock = (p: P) => <svg {...base(p)}><rect x="5" y="10.5" width="14" height="10" rx="2.5" /><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" /></svg>;
export const IconVideo = (p: P) => <svg {...base(p)}><rect x="3" y="6" width="13" height="12" rx="2.5" /><path d="m16 10.5 5-3v9l-5-3" /></svg>;
export const IconArrowLeft = (p: P) => <svg {...base(p)}><path d="M15 5l-7 7 7 7" /></svg>;
export const IconArrowRight = (p: P) => <svg {...base(p)}><path d="M9 5l7 7-7 7" /></svg>;
export const IconPlus = (p: P) => <svg {...base(p)}><path d="M12 5v14M5 12h14" /></svg>;
export const IconUserOff = (p: P) => <svg {...base(p)}><circle cx="10" cy="8" r="3.5" /><path d="M3.5 20c.5-3.5 3-5.5 6.5-5.5 1.3 0 2.5.3 3.5.8M16 16l4 4M20 16l-4 4" /></svg>;
/** Свой сервис вуза (коробка). */
export const IconBox = (p: P) => <svg {...base(p)}><path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z" /><path d="m4 7.5 8 4.5 8-4.5M12 12v9" /></svg>;
/** Администрирование (здание вуза). */
export const IconBuilding = (p: P) => <svg {...base(p)}><path d="M3 21h18M5 21V10l7-5 7 5v11M9.5 21v-5h5v5M9 12.5h.01M15 12.5h.01" /></svg>;
