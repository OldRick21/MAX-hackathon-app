// Проверка доступа к разделу: каталог загружается → раздел есть → показываем; иначе «Нет доступа».
import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { Profile, ServiceView } from '../api/types';
import { PROFILE_LABEL, findService, useInstitution, type NativeSection } from '../state/institution';
import { IconLock } from './icons/ui';
import { Button, EmptyState, ErrorState, LoadingState } from './ui';

/** checkKey — строка, меняющаяся вместе с условием find (чтобы не перезапускать проверку на каждом рендере). */
export function AccessDenied({ find, checkKey }: { find: (services: ServiceView[]) => boolean; checkKey: string }) {
  const { profiles, profile, setProfile, catalogFor } = useInstitution();
  const [alternative, setAlternative] = useState<Profile | null>(null);
  const findRef = useRef(find);
  findRef.current = find;
  useEffect(() => {
    setAlternative(null);
    let alive = true;
    (async () => {
      for (const p of profiles) {
        if (p === profile) continue;
        try {
          if (findRef.current(await catalogFor(p))) { if (alive) setAlternative(p); return; }
        } catch { /* проверка необязательна */ }
      }
    })();
    return () => { alive = false; };
  }, [profiles, profile, catalogFor, checkKey]);
  return (
    <EmptyState icon={<IconLock />} title="Нет доступа" text="У текущего профиля нет прав на использование этого сервиса.">
      {alternative && <Button onClick={() => setProfile(alternative)}>Переключить профиль на «{PROFILE_LABEL[alternative]}»</Button>}
    </EmptyState>
  );
}

export function SectionGate({ section, children }: { section: NativeSection; children: (service: ServiceView) => ReactNode }) {
  const { catalog, reloadCatalog } = useInstitution();
  if (catalog.status === 'loading') return <LoadingState />;
  if (catalog.status === 'error') return <ErrorState title="Не удалось загрузить сервисы" text={catalog.message} onRetry={reloadCatalog} />;
  const service = findService(catalog.value, section);
  if (!service) return <AccessDenied checkKey={section} find={list => !!findService(list, section)} />;
  return <>{children(service)}</>;
}
