import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import type { ServiceFrameHandle } from '../../api/backend';
import { ApiError, humanMessage } from '../../api/http';
import type { ServiceView } from '../../api/types';
import { AccessDenied } from '../../components/SectionGate';
import { ErrorState, LoadingState } from '../../components/ui';
import { useInstitution } from '../../state/institution';
import { useBackend } from '../../state/session';
import p from '../pages.module.css';
import s from './services.module.css';

type FrameState = 'loading' | 'loaded' | 'error' | 'unavailable';

/**
 * ServiceContainer: клиент сервиса в iframe по протоколу SDK.
 * Состояния: loading → loaded; error (можно повторить); unavailable (сервис недоступен);
 * «нет доступа» проверяется до открытия. Пустой iframe при ошибке не остаётся.
 */
export function ServiceContainer({ service, menuId }: { service: ServiceView; menuId: string }) {
  const backend = useBackend();
  const host = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<FrameState>('loading');
  const [message, setMessage] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let handle: ServiceFrameHandle | null = null;
    let alive = true;
    setState('loading');
    backend.openFrame(service, menuId, host.current!, {
      onStatus: st => { if (alive && st === 'ready') setState('loaded'); },
      onError: e => {
        if (!alive) return;
        setMessage(humanMessage(e));
        setState(e instanceof ApiError && (e.status === 503 || e.status === 409) ? 'unavailable' : 'error');
      },
    }).then(
      h => { if (alive) handle = h; else h.close(); },
      e => {
        if (!alive) return;
        setMessage(humanMessage(e));
        setState(e instanceof ApiError && (e.status === 503 || e.status === 409 || e.status === 404) ? 'unavailable' : 'error');
      },
    );
    return () => { alive = false; handle?.close(); host.current?.replaceChildren(); };
  }, [backend, service, menuId, attempt]);

  return (
    <div className={s.container} aria-busy={state === 'loading'}>
      <div ref={host} className={s.frameHost} data-hidden={state !== 'loaded'} />
      {state !== 'loaded' && (
        <div className={s.overlay}>
          {state === 'loading' && <LoadingState text="Открываем сервис…" />}
          {state === 'error' && <ErrorState title="Не удалось открыть сервис" text={message} onRetry={() => setAttempt(a => a + 1)} />}
          {state === 'unavailable' && (
            <ErrorState title="Сервис недоступен" text="Сервис сейчас не отвечает. Попробуйте позже или сообщите администратору вуза." onRetry={() => setAttempt(a => a + 1)} />
          )}
        </div>
      )}
    </div>
  );
}

export function ServiceScreen() {
  const { serviceId = '', menuId = '' } = useParams();
  const { catalog, reloadCatalog } = useInstitution();
  if (catalog.status === 'loading') return <LoadingState />;
  if (catalog.status === 'error') return <ErrorState title="Не удалось загрузить сервисы" text={catalog.message} onRetry={reloadCatalog} />;
  const service = catalog.value.find(x => x.id === serviceId);
  const menu = service?.menus.find(m => m.id === menuId);
  if (!service || !menu) {
    return <AccessDenied checkKey={`${serviceId}:${menuId}`} find={list => list.some(x => x.id === serviceId && x.menus.some(m => m.id === menuId))} />;
  }
  return (
    <div>
      <div className={p.pageHead}>
        <div>
          <h1 className={p.title}>{menu.display_name}</h1>
          {service.display_name !== menu.display_name && <p className={p.subtitle}>{service.display_name}</p>}
        </div>
      </div>
      <ServiceContainer key={`${service.id}:${service.profile}:${menuId}`} service={service} menuId={menuId} />
    </div>
  );
}
