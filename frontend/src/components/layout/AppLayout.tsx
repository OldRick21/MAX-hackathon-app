import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import logo from '../../assets/logo-buildings.png';
import { useInstitution } from '../../state/institution';
import { Wordmark } from '../icons/figma';
import { bottomNav, buildNav, sideNav, type NavItem } from './navigation';
import { AccountSwitcher, ProfileSwitcher } from './Switchers';
import s from './layout.module.css';

const cx = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(' ');

function SidebarItem({ item }: { item: NavItem }) {
  return (
    <NavLink to={item.to} end={item.end} className={({ isActive }) => cx(s.navLink, isActive && s.active)}>
      <item.Icon className={item.small ? s.navIconSmall : undefined} strokeWidth={item.small ? 2.6 : undefined} />
      <span className={s.navLabel} title={item.label}>{item.label}</span>
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

export function AppLayout() {
  const { institution, catalog } = useInstitution();
  const location = useLocation();
  const base = `/institution/${institution.id}`;
  const items = buildNav(base, catalog.status === 'ready' ? catalog.value : undefined);
  return (
    <div className={s.shell}>
      <Sidebar items={sideNav(items)} home={base} />
      <main className={s.main}>
        <header className={s.header}>
          <Link to={base} className={s.mobileLogo} aria-label="Вузы России — на главную">
            <img src={logo} alt="" />
            <Wordmark />
          </Link>
          <div className={s.switchers}>
            <ProfileSwitcher />
          </div>
          <div className={s.accountSlot}><AccountSwitcher /></div>
        </header>
        <div className={s.content}>
          {/* Ключ по адресу: при переходе экран появляется заново с короткой анимацией. */}
          <div key={location.pathname} className="screen-in"><Outlet /></div>
        </div>
      </main>
      <BottomNavigation items={bottomNav(items)} />
    </div>
  );
}
