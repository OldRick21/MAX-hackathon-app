import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { useBackend, useSession } from '../state/session';
import { createPlatform } from './platform';
import styles from './platform.css?inline';
import ui from './platform.module.css';

/** Заявка на подключение нового вуза. Рассматривает её оператор платформы в операторской панели. */
export function PlatformScreen() {
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
    ui.showApplications().catch((e: Error) => { error.textContent = e.message; });
    return () => { ui.cancel(); shadow.replaceChildren(); };
  }, [backend]);
  return <main className={ui.page}>
    <Link to="/join">← К регистрации</Link>
    <p>ID пользователя: {user?.id}</p><div ref={ref} />
  </main>;
}
