// Сессия пользователя: вход через MAX и список вузов.
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { loadBackend, type Backend } from '../api';
import { NotInMaxError } from '../api/backend';
import { humanMessage } from '../api/http';
import type { InstitutionView, MaxUserInfo, User } from '../api/types';

export type SessionPhase =
  | { kind: 'booting' }
  | { kind: 'not-in-max' }
  | { kind: 'error'; message: string }
  | { kind: 'expired' }
  | { kind: 'ready' };

interface SessionValue {
  phase: SessionPhase;
  backend: Backend | null;
  user: User | null;
  maxUser: MaxUserInfo | null;
  institutions: InstitutionView[];
  retry: () => void;
  /** Перечитать список вузов без повторного входа (после одобрения заявки). */
  reloadInstitutions: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<SessionPhase>({ kind: 'booting' });
  const [backend, setBackend] = useState<Backend | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [institutions, setInstitutions] = useState<InstitutionView[]>([]);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    setPhase({ kind: 'booting' });
    (async () => {
      const b = backend ?? await loadBackend();
      if (!alive) return;
      setBackend(b);
      try {
        const u = await b.login();
        window.WebApp?.ready?.();
        const list = await b.listInstitutions();
        if (!alive) return;
        setUser(u);
        setInstitutions(list);
        setPhase({ kind: 'ready' });
      } catch (e) {
        if (!alive) return;
        if (e instanceof NotInMaxError) setPhase({ kind: 'not-in-max' });
        else setPhase({ kind: 'error', message: humanMessage(e, 'Не удалось войти. Попробуйте ещё раз.') });
      }
    })();
    return () => { alive = false; };
    // backend создаётся один раз; повторный вход — через retry
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt]);

  const reloadInstitutions = useCallback(async () => {
    if (backend) setInstitutions(await backend.listInstitutions());
  }, [backend]);

  useEffect(() => {
    const onExpired = () => setPhase({ kind: 'expired' });
    window.addEventListener('vuzy:session-expired', onExpired);
    return () => window.removeEventListener('vuzy:session-expired', onExpired);
  }, []);

  return (
    <SessionContext.Provider value={{
      phase, backend, user, maxUser: backend?.maxUser() ?? null, institutions,
      retry: () => setAttempt(a => a + 1),
      reloadInstitutions,
    }}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession() {
  const v = useContext(SessionContext);
  if (!v) throw new Error('useSession вне SessionProvider');
  return v;
}

/** Backend после успешного входа. */
export function useBackend(): Backend {
  const { backend } = useSession();
  if (!backend) throw new Error('Backend ещё не готов');
  return backend;
}
