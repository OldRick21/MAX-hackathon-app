import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useBackend, useSession } from '../state/session';
import { createPlatform } from './platform';
import styles from './platform.css?inline';
import ui from './platform.module.css';

export function PlatformLinks() {
  const backend = useBackend();
  const [staff, setStaff] = useState(false);
  useEffect(() => {
    let alive = true;
    backend.coreCall?.<{roles: string[]}>('/api/v1/platform/me').then(r => {
      if (alive) setStaff(r.roles.includes('platform_support'));
    }).catch(() => {});
    return () => { alive = false; };
  }, [backend]);
  if (!backend.coreCall) return null;
  return <nav className={ui.links} aria-label="Платформа">
    <Link to="/applications">Подключить вуз</Link>
    {staff && <Link to="/support">Поддержка платформы</Link>}
  </nav>;
}

export function PlatformScreen({support = false}: {support?: boolean}) {
  const backend = useBackend();
  const { user } = useSession();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const shadow = ref.current!.shadowRoot ?? ref.current!.attachShadow({mode:'open'});
    const css = document.createElement('style');
    css.textContent = styles;
    const heading = document.createElement('h1');
    const status = document.createElement('p');
    const error = document.createElement('p'); error.setAttribute('role','alert');
    const host = document.createElement('div');
    shadow.replaceChildren(css, heading, status, error, host);
    const ui = createPlatform({
      core: {call: backend.coreCall, callMeta: backend.coreCallMeta}, host,
      setHeader: (_: string, title: string) => { heading.textContent = title; },
      setStatus: (text: string) => { status.textContent = text; },
      showError: (e: Error) => { error.textContent = e.message; },
      clearError: () => { error.textContent = ''; }, onChanged: () => {},
    });
    (async () => {
      if (support) {
        if (!await ui.detect()) throw new Error('Для этого раздела нужны права поддержки платформы.');
        await ui.showSupport();
      } else await ui.showApplications();
    })().catch(e => { error.textContent = e.message; });
    return () => { ui.cancel(); shadow.replaceChildren(); };
  }, [backend, support]);
  return <main className={ui.page}>
    <Link to="/">← К вузам</Link><PlatformLinks />
    <p>ID пользователя: {user?.id}</p><div ref={ref} />
  </main>;
}
