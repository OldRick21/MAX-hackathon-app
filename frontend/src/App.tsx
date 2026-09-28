import { PlatformLinks, PlatformScreen } from './platform/PlatformScreen';
import { BrowserRouter, Navigate, Route, Routes, useParams } from 'react-router-dom';
import { AppLayout } from './components/layout/AppLayout';
import { ErrorState, ToastHost } from './components/ui';
import { isUUID } from './api/http';
import { InstitutionProvider } from './state/institution';
import { SessionProvider, useSession } from './state/session';
import { AllServicesScreen } from './screens/services/AllServicesScreen';
import { CourseworkScreen } from './screens/coursework/CourseworkScreen';
import {
  BootScreen, NoInstitutionsScreen, NotInMaxScreen, SelectInstitutionScreen, SessionProblemScreen,
} from './screens/entry/EntryScreens';
import { HomeScreen } from './screens/home/HomeScreen';
import { GroupsScreen } from './screens/schedule/GroupsScreen';
import { ScheduleScreen } from './screens/schedule/ScheduleScreen';
import { ServiceScreen } from './screens/services/ServiceScreen';
import { UserProfileScreen } from './screens/users/UserProfileScreen';
import { UsersScreen } from './screens/users/UsersScreen';

function Root() {
  const { institutions } = useSession();
  if (institutions.length === 0) return <><PlatformLinks /><NoInstitutionsScreen /></>;
  // Один вуз — сразу его интерфейс, без экрана выбора (handoff, раздел 4).
  if (institutions.length === 1) return <Navigate to={`/institution/${institutions[0].id}`} replace />;
  return <><PlatformLinks /><SelectInstitutionScreen /></>;
}

function InstitutionRoute() {
  const { institutionId = '' } = useParams();
  const { institutions } = useSession();
  if (!isUUID(institutionId) || !institutions.some(i => i.id === institutionId)) return <Navigate to="/" replace />;
  return (
    <InstitutionProvider
      key={institutionId}
      institutionId={institutionId}
      fallback={state => state.status === 'loading'
        ? <BootScreen text="Загружаем данные вуза…" />
        : <div style={{ padding: '4rem 1.6rem' }}><ErrorState title="Не удалось открыть вуз" text={state.message} onRetry={state.retry} /></div>}
    >
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<HomeScreen />} />
          <Route path="schedule" element={<ScheduleScreen />} />
          <Route path="schedule/groups" element={<GroupsScreen />} />
          <Route path="users" element={<UsersScreen />} />
          <Route path="users/:userId" element={<UserProfileScreen />} />
          <Route path="coursework" element={<CourseworkScreen />} />
          <Route path="services" element={<AllServicesScreen />} />
          <Route path="service/:serviceId/:menuId" element={<ServiceScreen />} />
          <Route path="*" element={<Navigate to={`/institution/${institutionId}`} replace />} />
        </Route>
      </Routes>
    </InstitutionProvider>
  );
}

function Gate() {
  const { phase } = useSession();
  switch (phase.kind) {
    case 'booting': return <BootScreen />;
    case 'not-in-max': return <NotInMaxScreen />;
    case 'expired': return <SessionProblemScreen title="Сессия завершилась" text="Войдите заново, чтобы продолжить работу." />;
    case 'error': return <SessionProblemScreen title="Не удалось войти" text={phase.message} />;
    case 'ready':
      return (
        <Routes>
          <Route path="/" element={<Root />} />
          <Route path="/applications" element={<PlatformScreen />} />
          <Route path="/support" element={<PlatformScreen support />} />
          <Route path="/institution" element={<Root />} />
          <Route path="/institution/:institutionId/*" element={<InstitutionRoute />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      );
  }
}

export default function App() {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <SessionProvider>
        <Gate />
        <ToastHost />
      </SessionProvider>
    </BrowserRouter>
  );
}
