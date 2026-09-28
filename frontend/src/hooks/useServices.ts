import { useMemo } from 'react';
import { findService, useInstitution, type NativeSection } from '../state/institution';
import { useBackend } from '../state/session';

/** Сервис встроенного раздела и его API; null, если текущему профилю раздел недоступен. */
export function useSection(section: NativeSection) {
  const { catalog } = useInstitution();
  return catalog.status === 'ready' ? findService(catalog.value, section) : null;
}

export function useProfilesApi() {
  const backend = useBackend();
  const { catalog } = useInstitution();
  const services = catalog.status === 'ready' ? catalog.value : undefined;
  const service = findService(services, 'users') ?? findService(services, 'home');
  return useMemo(() => (service ? { api: backend.profiles(service), service } : null), [backend, service]);
}

export function useScheduleApi() {
  const backend = useBackend();
  const service = useSection('schedule');
  return useMemo(() => (service ? { api: backend.schedule(service), service } : null), [backend, service]);
}

export function useCourseworkApi() {
  const backend = useBackend();
  const service = useSection('coursework');
  return useMemo(() => (service ? { api: backend.coursework(service), service } : null), [backend, service]);
}
