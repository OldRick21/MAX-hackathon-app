// Цвета профилей в «Людях»: студент — синий, преподаватель — зелёный, администратор — фиолетовый.
// Цвета — токены темы (--role-*), как у типов занятий в расписании (utils/lessonTypes.ts).
import type { CSSProperties } from 'react';
import type { Profile } from '../api/types';

export const PROFILE_ORDER: Profile[] = ['student', 'teacher', 'admin'];

/** CSS-переменные --role (метка) и --role-text (подпись) для элемента профиля. */
export function profileStyle(profile: Profile): CSSProperties {
  return { '--role': `var(--role-${profile})`, '--role-text': `var(--role-${profile}-text)` } as CSSProperties;
}
