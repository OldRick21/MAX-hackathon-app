// Контекст выбранного вуза: профили пользователя, текущий профиль и каталог сервисов.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { humanMessage, isAbort } from '../api/http';
import type { InstitutionView, Profile, ServiceView } from '../api/types';
import { useBackend } from './session';

export const PROFILE_LABEL: Record<Profile, string> = {
  student: 'Студент',
  teacher: 'Преподаватель',
  admin: 'Администратор',
};

type Load<T> = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; value: T };

interface InstitutionValue {
  institution: InstitutionView;
  profiles: Profile[];
  profile: Profile;
  setProfile: (p: Profile) => void;
  catalog: Load<ServiceView[]>;
  reloadCatalog: () => void;
  /** Каталог другого профиля — чтобы подсказать «Переключить профиль» на экране «Нет доступа». */
  catalogFor: (p: Profile) => Promise<ServiceView[]>;
}

const Ctx = createContext<InstitutionValue | null>(null);

const storeKey = (id: string) => `vuzy.profile.${id}`;
function rememberedProfile(id: string): Profile | null {
  try { return sessionStorage.getItem(storeKey(id)) as Profile | null; } catch { return null; }
}

/** Загружает карточку вуза и профили; пока не готово — рендерит fallback. */
export function InstitutionProvider({ institutionId, children, fallback }: {
  institutionId: string;
  children: ReactNode;
  fallback: (state: { status: 'loading' } | { status: 'error'; message: string; retry: () => void }) => ReactNode;
}) {
  const backend = useBackend();
  const [base, setBase] = useState<Load<{ institution: InstitutionView; profiles: Profile[] }>>({ status: 'loading' });
  const [profile, setProfileState] = useState<Profile | null>(null);
  const [catalog, setCatalog] = useState<Load<ServiceView[]>>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const [catalogAttempt, setCatalogAttempt] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    setBase({ status: 'loading' });
    Promise.all([backend.getInstitution(institutionId, ctrl.signal), backend.getProfiles(institutionId, ctrl.signal)])
      .then(([institution, profiles]) => {
        if (ctrl.signal.aborted) return;
        setBase({ status: 'ready', value: { institution, profiles } });
        const saved = rememberedProfile(institutionId);
        // Если профилей несколько, admin автоматически не выбираем (docs/core/CORE_CLIENT.md).
        const initial = saved && profiles.includes(saved) ? saved : profiles.find(p => p !== 'admin') ?? profiles[0] ?? null;
        setProfileState(initial);
      })
      .catch(e => { if (!isAbort(e)) setBase({ status: 'error', message: humanMessage(e) }); });
    return () => { ctrl.abort(); backend.releaseServices(); };
  }, [backend, institutionId, attempt]);

  const ready = base.status === 'ready' ? base.value : null;
  const blocked = ready && ready.institution.status !== 'active';

  useEffect(() => {
    if (!ready || !profile || blocked) return;
    const ctrl = new AbortController();
    setCatalog({ status: 'loading' });
    backend.listServices(institutionId, profile, ctrl.signal)
      .then(v => { if (!ctrl.signal.aborted) setCatalog({ status: 'ready', value: v }); })
      .catch(e => { if (!isAbort(e)) setCatalog({ status: 'error', message: humanMessage(e, 'Не удалось загрузить список сервисов.') }); });
    return () => { ctrl.abort(); backend.releaseServices(); };
  }, [backend, institutionId, profile, ready, blocked, catalogAttempt]);

  const setProfile = useCallback((p: Profile) => {
    try { sessionStorage.setItem(storeKey(institutionId), p); } catch { /* приватный режим */ }
    setProfileState(p);
  }, [institutionId]);

  const catalogFor = useCallback((p: Profile) => backend.listServices(institutionId, p), [backend, institutionId]);

  const value = useMemo<InstitutionValue | null>(() => ready && profile ? {
    institution: ready.institution,
    profiles: ready.profiles,
    profile,
    setProfile,
    catalog: blocked ? { status: 'ready', value: [] } : catalog,
    reloadCatalog: () => setCatalogAttempt(a => a + 1),
    catalogFor,
  } : null, [ready, profile, setProfile, catalog, blocked, catalogFor]);

  if (base.status === 'loading') return <>{fallback({ status: 'loading' })}</>;
  if (base.status === 'error') return <>{fallback({ status: 'error', message: base.message, retry: () => setAttempt(a => a + 1) })}</>;
  if (!value) return <>{fallback({ status: 'error', message: 'В этом вузе у вас пока нет профиля. Обратитесь к администратору вуза.', retry: () => setAttempt(a => a + 1) })}</>;
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useInstitution() {
  const v = useContext(Ctx);
  if (!v) throw new Error('useInstitution вне InstitutionProvider');
  return v;
}

// ---------- Поиск сервисов в каталоге ----------

/** Меню встроенных экранов: тип сервиса → id меню (таблицы меню в docs/services/<сервис>/SPEC.md). */
export const NATIVE_MENUS = {
  home: { type: 'user-profile', menus: ['home'] },
  users: { type: 'user-profile', menus: ['users'] },
  schedule: { type: 'schedule', menus: ['schedule', 'schedule_admin'] },
  coursework: { type: 'coursework', menus: ['coursework', 'coursework_admin'] },
} as const;

export type NativeSection = keyof typeof NATIVE_MENUS;

export function findService(services: ServiceView[] | undefined, section: NativeSection): ServiceView | null {
  const def = NATIVE_MENUS[section];
  return services?.find(s => s.service_type === def.type && s.menus.some(m => (def.menus as readonly string[]).includes(m.id))) ?? null;
}

/** Сервисы и меню, которые открываются во встроенном клиенте (iframe). */
export function frameMenus(services: ServiceView[] | undefined) {
  return (services ?? []).flatMap(s => s.menus
    .filter(menu => !Object.values(NATIVE_MENUS).some(d => d.type === s.service_type && (d.menus as readonly string[]).includes(menu.id)))
    .map(menu => ({ service: s, menu })));
}
