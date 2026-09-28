// Экраны до входа в вуз: загрузка, «откройте через MAX», ошибки, выбор вуза.
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import logo from '../../assets/logo-buildings.png';
import { IconArrowRight, IconBuilding } from '../../components/icons/ui';
import { Wordmark } from '../../components/icons/figma';
import { Button, LoadingState, toast } from '../../components/ui';
import { PROFILE_LABEL } from '../../state/institution';
import { useSession } from '../../state/session';
import s from './entry.module.css';

function Frame({ children }: { children: ReactNode }) {
  return (
    <div className={s.screen}>
      <div className={s.brand} aria-label="Вузы России">
        <img src={logo} alt="" />
        <Wordmark />
      </div>
      <div className={s.panel}>{children}</div>
    </div>
  );
}

export function BootScreen({ text = 'Входим через MAX…' }: { text?: string }) {
  return <Frame><LoadingState text={text} /></Frame>;
}

export function NotInMaxScreen() {
  return (
    <Frame>
      <h1 className={s.title}>Откройте приложение в MAX</h1>
      <p className={s.text}>Вход выполняется через мессенджер MAX. Найдите бота «Вузы России» и нажмите кнопку «Открыть».</p>
    </Frame>
  );
}

export function SessionProblemScreen({ title, text }: { title: string; text: string }) {
  const { retry } = useSession();
  return (
    <Frame>
      <h1 className={s.title}>{title}</h1>
      <p className={s.text}>{text}</p>
      <div className={s.center}><Button onClick={retry}>Повторить</Button></div>
    </Frame>
  );
}

export function NoInstitutionsScreen() {
  const { user } = useSession();
  const copy = async () => {
    try { await navigator.clipboard.writeText(user?.id ?? ''); toast('Идентификатор скопирован'); }
    catch { toast('Не удалось скопировать. Выделите идентификатор вручную.', true); }
  };
  return (
    <Frame>
      <h1 className={s.title}>Вы пока не подключены к вузу</h1>
      <p className={s.text}>Передайте администратору вашего вуза этот идентификатор — он добавит вас в систему.</p>
      {user && <code className={s.code}>{user.id}</code>}
      {user && <div className={s.center}><Button variant="secondary" onClick={copy}>Скопировать идентификатор</Button></div>}
    </Frame>
  );
}

export function SelectInstitutionScreen() {
  const { institutions } = useSession();
  const navigate = useNavigate();
  return (
    <Frame>
      <h1 className={s.title}>Выберите вуз</h1>
      <p className={s.text}>Вы состоите в нескольких вузах. Сменить вуз можно в любой момент в верхней части экрана.</p>
      <ul className={s.list}>
        {institutions.map(i => (
          <li key={i.id}>
            <button type="button" className={s.uniButton} onClick={() => navigate(`/institution/${i.id}`)}>
              <span className={s.uniIcon}><IconBuilding /></span>
              <span>
                <span className={s.uniName}>{i.display_name}</span>
                <span className={s.uniMeta} style={{ display: 'block' }}>
                  {i.status === 'active' ? i.profiles.map(p => PROFILE_LABEL[p]).join(', ') : 'Вуз временно недоступен'}
                </span>
              </span>
              <IconArrowRight className={s.arrow} />
            </button>
          </li>
        ))}
      </ul>
    </Frame>
  );
}
