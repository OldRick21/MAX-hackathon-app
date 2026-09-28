import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { humanMessage, isAbort } from '../../api/http';
import type { ProfileCard, ServiceView } from '../../api/types';
import { SectionGate } from '../../components/SectionGate';
import { IconArrowRight, IconUserOff } from '../../components/icons/ui';
import { Avatar, Button, EmptyState, ErrorState, SearchInput, Skeleton } from '../../components/ui';
import { useInstitution } from '../../state/institution';
import { useBackend, useSession } from '../../state/session';
import p from '../pages.module.css';
import s from './users.module.css';

export function UsersScreen() {
  return <SectionGate section="users">{service => <Users service={service} />}</SectionGate>;
}

export function cardSubtitle(c: ProfileCard) {
  return [c.position, c.academic_degree].filter(Boolean).join(' · ');
}

export function UserRow({ card, to, isMe, photo }: { card: ProfileCard; to: string; isMe: boolean; photo?: string }) {
  const meta = cardSubtitle(card) || (card.about ? card.about.split('\n')[0] : '');
  return (
    <Link to={to} className={s.row}>
      <Avatar name={card.display_name} src={isMe ? photo : undefined} size="var(--row-avatar, 6.4rem)" />
      <span className={s.rowText}>
        <span className={s.rowName}>{card.display_name}{isMe && ' (вы)'}</span>
        {meta && <span className={s.rowMeta}>{meta}</span>}
      </span>
      <span className={s.rowAction}><span>Профиль</span><IconArrowRight /></span>
    </Link>
  );
}

type ListState =
  | { status: 'loading'; items: ProfileCard[] }
  | { status: 'ready' | 'more'; items: ProfileCard[]; cursor: string | null }
  | { status: 'error'; items: ProfileCard[]; message: string };

function Users({ service }: { service: ServiceView }) {
  const backend = useBackend();
  const { user, maxUser } = useSession();
  const { institution } = useInstitution();
  const api = useMemo(() => backend.profiles(service), [backend, service]);
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  const [state, setState] = useState<ListState>({ status: 'loading', items: [] });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(query), 300);
    return () => clearTimeout(t);
  }, [query]);

  useEffect(() => {
    const ctrl = new AbortController();
    setState({ status: 'loading', items: [] });
    (async () => {
      // Страница после фильтрации на сервере может быть пустой при next_cursor ≠ null (spec user-profile) —
      // дочитываем, пока не наберём результаты или не кончится список.
      let items: ProfileCard[] = [];
      let cursor: string | null = null;
      for (let i = 0; i < 5; i++) {
        const page = await api.listUsers(debounced, cursor, ctrl.signal);
        items = [...items, ...page.items];
        cursor = page.next_cursor;
        if (items.length >= 20 || !cursor) break;
      }
      if (!ctrl.signal.aborted) setState({ status: 'ready', items, cursor });
    })().catch(e => { if (!isAbort(e)) setState({ status: 'error', items: [], message: humanMessage(e) }); });
    return () => ctrl.abort();
  }, [api, debounced, attempt]);

  const loadMore = async () => {
    if (state.status !== 'ready' || !state.cursor) return;
    setState({ ...state, status: 'more' });
    try {
      const page = await api.listUsers(debounced, state.cursor);
      setState({ status: 'ready', items: [...state.items, ...page.items], cursor: page.next_cursor });
    } catch (e) {
      setState({ status: 'error', items: state.items, message: humanMessage(e) });
    }
  };

  const base = `/institution/${institution.id}`;
  return (
    <div>
      <div className={p.pageHead}>
        <div>
          <h1 className={p.title}>Пользователи</h1>
          <p className={p.subtitle}>Участники вуза, которые заполнили анкету</p>
        </div>
      </div>
      <SearchInput
        className={s.search}
        placeholder="Поиск по имени или должности"
        aria-label="Поиск пользователей"
        value={query}
        onChange={e => setQuery(e.target.value)}
      />
      {state.status === 'loading' ? (
        <ul className={s.list} aria-busy="true" aria-label="Загрузка списка">
          {[0, 1, 2, 3].map(i => (
            <li key={i} className={s.skeletonRow}>
              <Skeleton width="6.4rem" height="6.4rem" radius="30%" />
              <span style={{ flex: 1, display: 'grid', gap: '.8rem' }}><Skeleton width="40%" height="2.4rem" /><Skeleton width="60%" height="2rem" /></span>
            </li>
          ))}
        </ul>
      ) : state.status === 'error' && !state.items.length ? (
        <ErrorState title="Не удалось загрузить пользователей" text={state.message} onRetry={() => setAttempt(a => a + 1)} />
      ) : !state.items.length ? (
        <EmptyState icon={<IconUserOff />} title={debounced ? 'По вашему запросу ничего не найдено' : 'Пока никто не заполнил анкету'}
          text={debounced ? 'Проверьте написание или попробуйте искать по фамилии.' : undefined}>
          {debounced && <Button variant="secondary" onClick={() => setQuery('')}>Сбросить поиск</Button>}
        </EmptyState>
      ) : (
        <>
          <ul className={s.list}>
            {state.items.map(c => (
              <li key={c.user_id}>
                <UserRow card={c} to={`${base}/users/${c.user_id === user?.id ? 'me' : c.user_id}`} isMe={c.user_id === user?.id} photo={maxUser?.photo_url} />
              </li>
            ))}
          </ul>
          {state.status === 'error' && <p className={p.formError} role="alert" style={{ marginTop: '1.6rem' }}>{state.message}</p>}
          {(state.status === 'more' || (state.status === 'ready' && state.cursor)) && (
            <div className={s.more}><Button variant="secondary" onClick={loadMore} loading={state.status === 'more'}>Показать ещё</Button></div>
          )}
        </>
      )}
    </div>
  );
}
