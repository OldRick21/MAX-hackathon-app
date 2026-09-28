import { PlatformLinks } from '../../platform/PlatformScreen';
import { useEffect, useRef, useState } from 'react';
import { Link, NavLink, Outlet } from 'react-router-dom';
import logo from '../../assets/logo-buildings.png';
import { useInstitution } from '../../state/institution';
import { IconBell, Wordmark } from '../icons/figma';
import { bottomNav, buildNav, type NavItem } from './navigation';
import { ProfileSwitcher, UniversitySwitcher } from './Switchers';
import s from './layout.module.css';

const cx = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(' ');

function SidebarItem({ item }: { item: NavItem }) {
  return (
    <NavLink to={item.to} end={item.end} className={({ isActive }) => cx(s.navLink, isActive && s.active)}>
      <item.Icon className={item.small ? s.navIconSmall : undefined} strokeWidth={item.small ? 2.6 : undefined} />
      <span>{item.label}</span>
    </NavLink>
  );
}

function Sidebar({ items, home }: { items: NavItem[]; home: string }) {
  return (
    <aside className={s.sidebar}>
      <Link to={home} className={s.logo} aria-label="Вузы России — на главную">
        <img src={logo} alt="" className={s.logoImg} />
        <Wordmark className={s.wordmark} />
      </Link>
      <nav className={s.nav} aria-label="Разделы">
        {items.map(i => <SidebarItem key={i.key} item={i} />)}
      </nav>
    </aside>
  );
}

function BottomNavigation({ items }: { items: NavItem[] }) {
  return (
    <nav className={s.bottomNav} aria-label="Разделы">
      {items.map(i => (
        <NavLink key={i.key} to={i.to} end={i.end} className={({ isActive }) => cx(s.bottomLink, isActive && s.active)}>
          <i.Icon strokeWidth={i.small ? 2.6 : undefined} />
          <span>{i.short}</span>
        </NavLink>
      ))}
    </nav>
  );
}

/** Уведомления: API уведомлений в контрактах нет, поэтому показываем пустое состояние. */
function NotificationsButton() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', close);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close); };
  }, [open]);
  return (
    <div className={s.bell} ref={ref}>
      <button type="button" className={s.bellButton} aria-label="Уведомления" aria-expanded={open} onClick={() => setOpen(o => !o)}>
        <IconBell />
      </button>
      {open && (
        <div className={s.popover} role="dialog" aria-label="Уведомления">
          <p className={s.popoverTitle}>Уведомлений нет</p>
          <p className={s.popoverText}>Здесь появятся новости от вуза и изменения в расписании.</p>
        </div>
      )}
    </div>
  );
}

export function AppLayout() {
  const { institution, catalog } = useInstitution();
  const base = `/institution/${institution.id}`;
  const items = buildNav(base, catalog.status === 'ready' ? catalog.value : undefined);
  return (
    <div className={s.shell}>
      <Sidebar items={items} home={base} />
      <main className={s.main}>
        <header className={s.header}>
          <Link to={base} className={s.mobileLogo} aria-label="Вузы России — на главную">
            <img src={logo} alt="" />
            <Wordmark />
          </Link>
          <div className={s.switchers}>
            <UniversitySwitcher />
            <ProfileSwitcher />
          </div>
          <NotificationsButton />
        </header>
        <div className={s.content}>
          <PlatformLinks />
          <Outlet />
        </div>
      </main>
      <BottomNavigation items={bottomNav(items)} />
    </div>
  );
}
