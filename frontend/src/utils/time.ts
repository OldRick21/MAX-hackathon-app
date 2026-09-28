// Время в интерфейсе — московское (спецификация расписания: по умолчанию Europe/Moscow).
// В Москве с 2014 года нет перехода на летнее время, поэтому смещение постоянное: UTC+3.

export const TIME_ZONE = 'Europe/Moscow';
const OFFSET = 3 * 3600_000;
export const DAY = 86400_000;

let clockOverride: number | null = null;
/** Только для тестовых данных: подменить «сейчас» (например, ?now=2026-09-22T12:30). */
export function setClock(iso: string | null) {
  const t = iso ? Date.parse(iso.length <= 16 ? `${iso}:00+03:00` : iso) : NaN;
  clockOverride = Number.isFinite(t) ? t : null;
}
export const now = () => new Date(clockOverride ?? Date.now());

/** Начало суток по Москве. */
export function startOfDay(d: Date): Date {
  const shifted = d.getTime() + OFFSET;
  return new Date(shifted - (shifted % DAY) - OFFSET);
}

/** Понедельник недели по Москве. */
export function startOfWeek(d: Date): Date {
  const day = startOfDay(d);
  const weekday = (new Date(day.getTime() + OFFSET).getUTCDay() + 6) % 7;
  return new Date(day.getTime() - weekday * DAY);
}

export const addDays = (d: Date, n: number) => new Date(d.getTime() + n * DAY);
export const dayKey = (d: Date | string) => new Date(new Date(d).getTime() + OFFSET).toISOString().slice(0, 10);

const timeFmt = new Intl.DateTimeFormat('ru-RU', { timeZone: TIME_ZONE, hour: '2-digit', minute: '2-digit' });
const dayFmt = new Intl.DateTimeFormat('ru-RU', { timeZone: TIME_ZONE, weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
const shortFmt = new Intl.DateTimeFormat('ru-RU', { timeZone: TIME_ZONE, day: 'numeric', month: 'long' });
const dateFmt = new Intl.DateTimeFormat('ru-RU', { timeZone: TIME_ZONE, day: '2-digit', month: '2-digit', year: 'numeric' });
const weekdayFmt = new Intl.DateTimeFormat('ru-RU', { timeZone: TIME_ZONE, weekday: 'long' });

export const formatTime = (iso: string | Date) => timeFmt.format(new Date(iso));
export const formatRange = (from: string, to: string) => `${formatTime(from)}-${formatTime(to)}`;
export const formatDate = (iso: string | Date) => dateFmt.format(new Date(iso));
export const formatShort = (d: Date) => shortFmt.format(d);

/** «Вторник, 22 сентября 2026» — как в макете. */
export function formatDayTitle(d: Date) {
  const parts = dayFmt.formatToParts(d);
  const get = (t: string) => parts.find(p => p.type === t)?.value ?? '';
  const weekday = get('weekday');
  return `${weekday.charAt(0).toUpperCase()}${weekday.slice(1)}, ${get('day')} ${get('month')} ${get('year')}`;
}

export function formatWeekday(d: Date) {
  const w = weekdayFmt.format(d);
  return w.charAt(0).toUpperCase() + w.slice(1);
}

/** Дата и время по Москве → ISO UTC. */
export function mskToIso(date: string, time: string) {
  return new Date(`${date}T${time}:00+03:00`).toISOString();
}
