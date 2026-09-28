// Учебные группы — сущность ядра. Здесь только просмотр: студенту — своя группа,
// преподавателю и админу — все группы с составом. Ведёт группы администратор в «Администрирование» → «Группы».
import { Link } from 'react-router-dom';
import { humanMessage } from '../../api/http';
import { IconUsers } from '../../components/icons/figma';
import { IconArrowLeft } from '../../components/icons/ui';
import { EmptyState, ErrorState, LoadingState } from '../../components/ui';
import { useAsync } from '../../hooks/useAsync';
import { useProfilesApi } from '../../hooks/useServices';
import { useUserNames } from '../../hooks/useUserNames';
import { useInstitution } from '../../state/institution';
import { useBackend } from '../../state/session';
import p from '../pages.module.css';
import s from './schedule.module.css';

export function GroupsScreen() {
  const backend = useBackend();
  const { institution, profile } = useInstitution();
  const people = useProfilesApi();
  const groups = useAsync(signal => backend.listGroups(institution.id, profile, signal), [backend, institution.id, profile]);
  const ids = (groups.data ?? []).flatMap(g => g.user_ids ?? []);
  const names = useUserNames(people?.api ?? null, people?.service.id ?? '', ids);
  const back = <Link to={`/institution/${institution.id}/schedule`} className={p.back}><IconArrowLeft />Расписание</Link>;

  if (groups.status === 'error') return <>{back}<ErrorState text={humanMessage(groups.error)} onRetry={groups.reload} /></>;
  if (groups.status === 'loading') return <>{back}<LoadingState /></>;
  const list = groups.data ?? [];
  if (profile === 'student') {
    return <>{back}<EmptyState icon={<IconUsers />} title={list[0] ? `Моя группа: ${list[0].name}` : 'Группа пока не назначена'}
      text={list[0] ? 'Вы видите занятия этой группы в расписании.' : 'Обратитесь к администратору вуза: он назначит вам группу.'} /></>;
  }
  return (
    <div>
      {back}
      <div className={p.pageHead}><div>
        <h1 className={p.title}>Учебные группы</h1>
        <p className={p.subtitle}>Группы ведёт администратор вуза в разделе «Администрирование».</p>
      </div></div>
      {!list.length ? <EmptyState icon={<IconUsers />} title="Групп пока нет" /> : (
        <ul className={s.groupList}>
          {list.map(g => (
            <li key={g.id} className={`${p.card} ${s.groupRow}`}>
              <span className={s.groupName}>{g.name}</span>
              <span className={p.muted}>
                {(g.user_ids ?? []).map(id => names[id] || `без анкеты · ${id.slice(0, 8)}`).join(', ') || 'Нет студентов'}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
