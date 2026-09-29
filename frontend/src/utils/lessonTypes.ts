// Типы занятий расписания и их цвета: лекция — зелёный, семинар — красный, лабораторная — синий.
// Цвета — токены темы (--lesson-*), поэтому метки читаются и в светлой, и в тёмной теме.
import type { CSSProperties } from 'react';

export type LessonType = 'lecture' | 'seminar' | 'lab';

export const LESSON_TYPES: Record<LessonType, string> = {
  lecture: 'Лекция',
  seminar: 'Семинар',
  lab: 'Лабораторная',
};

/** CSS-переменные --lesson (метка) и --lesson-text (подпись) для элемента с типом занятия. */
export function lessonStyle(type: LessonType | null | undefined): CSSProperties | undefined {
  if (!type || !(type in LESSON_TYPES)) return undefined;
  return { '--lesson': `var(--lesson-${type})`, '--lesson-text': `var(--lesson-${type}-text)` } as CSSProperties;
}
