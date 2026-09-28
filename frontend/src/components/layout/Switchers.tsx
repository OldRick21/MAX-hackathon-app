import { useNavigate } from 'react-router-dom';
import type { Profile } from '../../api/types';
import { PROFILE_LABEL, useInstitution } from '../../state/institution';
import { useSession } from '../../state/session';
import { IconChevron } from '../icons/figma';
import { Dropdown } from '../ui';
import s from './layout.module.css';

const cx = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(' ');

function SwitcherFace({ text, uni, interactive }: { text: string; uni?: boolean; interactive: boolean }) {
  return (
    <>
      <span className={s.switcherText}>{text}</span>
      {interactive && (
        <>
          <span className={s.switcherDivider} aria-hidden="true" />
          <IconChevron className={s.switcherChevron} />
        </>
      )}
      {uni && <span className="visually-hidden">— сменить вуз</span>}
    </>
  );
}

/** Сокращённое название для переключателя: «МИФИ» из «… “МИФИ”», иначе первые слова. */
export function shortInstitutionName(name: string) {
  const quoted = name.match(/[«“"]([^»”"]+)[»”"]/);
  if (quoted) return quoted[1];
  return name.length > 32 ? `${name.slice(0, 30).trimEnd()}…` : name;
}

export function UniversitySwitcher() {
  const { institutions } = useSession();
  const { institution } = useInstitution();
  const navigate = useNavigate();
  if (institutions.length < 2) return null;
  return (
    <Dropdown
      label="Выбор вуза"
      items={institutions.map(i => ({
        key: i.id,
        label: i.display_name,
        checked: i.id === institution.id,
        onSelect: () => { if (i.id !== institution.id) navigate(`/institution/${i.id}`); },
      }))}
      trigger={({ toggle, ref, ...aria }) => (
        <button ref={ref} type="button" className={cx(s.switcher, s.switcherUni)} onClick={toggle} title={institution.display_name} {...aria}>
          <SwitcherFace text={shortInstitutionName(institution.display_name)} uni interactive />
        </button>
      )}
    />
  );
}

export function ProfileSwitcher() {
  const { profiles, profile, setProfile } = useInstitution();
  if (profiles.length < 2) {
    return <span className={cx(s.switcher, s.switcherStatic)}><SwitcherFace text={PROFILE_LABEL[profile]} interactive={false} /></span>;
  }
  return (
    <Dropdown
      label="Профиль"
      items={profiles.map((p: Profile) => ({ key: p, label: PROFILE_LABEL[p], checked: p === profile, onSelect: () => setProfile(p) }))}
      trigger={({ toggle, ref, ...aria }) => (
        <button ref={ref} type="button" className={s.switcher} onClick={toggle} aria-label={`Профиль: ${PROFILE_LABEL[profile]}. Сменить профиль`} {...aria}>
          <SwitcherFace text={PROFILE_LABEL[profile]} interactive />
        </button>
      )}
    />
  );
}
