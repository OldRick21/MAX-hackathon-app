// Типы по контрактам из MAX-hackathon-app/docs (core/CORE_API_OPENAPI.yaml и services/*/OPENAPI.yaml).
// Названия полей совпадают с API — не переименовывать.

export type UUID = string;
export type DateTime = string;
export type Locale = 'ru' | 'en';

/** Профиль пользователя в вузе (кто он в этом вузе). */
export type Profile = 'student' | 'teacher' | 'admin';

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

// ---------- Ядро ----------

export interface User {
  id: UUID;
  max_user_id: string;
  created_at: DateTime;
}

export type InstitutionStatus = 'pending' | 'active' | 'suspended';

export interface InstitutionView {
  id: UUID;
  display_name: string;
  locale: Locale;
  default_locale: Locale;
  status: InstitutionStatus;
  profiles: Profile[];
}

// ---------- Заявки на вступление ----------

/** Вуз, в который можно подать заявку: только зарегистрированные вузы и их группы. */
export interface JoinOption {
  id: UUID;
  display_name: string;
  groups: { id: UUID; name: string }[];
  /** Профили, которые у пользователя уже есть в этом вузе. */
  profiles: Profile[];
}

export type JoinProfile = 'student' | 'teacher';
export type JoinRequestStatus = 'pending' | 'approved' | 'rejected' | 'withdrawn';

export interface JoinRequestItem {
  institution_id: UUID;
  profile: JoinProfile;
  group_id?: UUID;
}

export interface JoinRequest {
  id: UUID;
  institution_id: UUID;
  institution_name: string;
  user_id: UUID;
  full_name: string;
  profile: JoinProfile;
  group_id: UUID | null;
  group_name: string | null;
  status: JoinRequestStatus;
  decision_reason: string | null;
  created_at: DateTime;
  reviewed_at: DateTime | null;
}

export interface ProfileList {
  user_id: UUID;
  institution_id: UUID;
  profiles: Profile[];
}

export interface MenuView {
  id: string;
  display_name: string;
  locale: Locale;
  entrypoint_path: string;
  order: number;
}

export interface ServiceView {
  id: UUID;
  institution_id: UUID;
  service_type: string;
  deployment: 'cloud' | 'local';
  display_name: string;
  locale: Locale;
  api_base_url: string;
  client_base_url: string;
  profile: Profile;
  /** Роли пользователя в сервисе. Показывать людям не нужно — только для решений UI. */
  roles: string[];
  permissions: string[];
  menus: MenuView[];
}

export interface CoreTokenPair {
  access_token: string;
  refresh_token: string;
  token_type: 'Bearer';
  expires_in: number;
  refresh_expires_in: number;
  session_id: UUID;
}

export interface ServiceTokenPair extends CoreTokenPair {
  parent_session_id: UUID;
  institution_id: UUID;
  service_id: UUID;
  profile: Profile;
}

// ---------- Расписание (schedule) ----------

export interface Group {
  id: UUID;
  name: string;
}

export interface GroupStudents {
  group_id: UUID;
  user_ids: UUID[];
}

export type EventStatus = 'scheduled' | 'cancelled';

export interface ScheduleEvent {
  id: UUID;
  title: string;
  starts_at: DateTime;
  ends_at: DateTime;
  group_ids: UUID[];
  teacher_ids: UUID[];
  location: string;
  description: string;
  status: EventStatus;
}

export type EventInput = Omit<ScheduleEvent, 'id'>;

// ---------- Анкеты (user-profile) ----------

export interface ProfileCard {
  user_id: UUID;
  display_name: string | null;
  about: string;
  position: string | null;
  academic_degree: string | null;
}

export interface SelfCardPatch {
  display_name?: string;
  about?: string;
}

export interface AcademicCardPatch {
  position?: string | null;
  academic_degree?: string | null;
}

// ---------- Курсовые (coursework) ----------

export type SubmissionStatus = 'submitted' | 'accepted' | 'changes_requested';

export interface FileInfo {
  original_name: string;
  size_bytes: number;
  media_type: 'application/pdf';
  sha256: string;
}

export interface Review {
  decision: 'accepted' | 'changes_requested';
  comment: string;
  reviewer_id: UUID;
  reviewed_at: DateTime;
}

export interface Submission {
  id: UUID;
  student_id: UUID;
  teacher_id: UUID;
  title: string;
  status: SubmissionStatus;
  version: number;
  file: FileInfo;
  review: Review | null;
  created_at: DateTime;
  updated_at: DateTime;
}

export interface ReviewInput {
  decision: 'accepted' | 'changes_requested';
  comment: string;
}

/** Ответ с версией ресурса: ETag нужен для If-Match при изменении. */
export interface Versioned<T> {
  data: T;
  etag: string;
}

// ---------- Данные MAX (только для отображения) ----------

/** Из WebApp.initDataUnsafe.user: подпись не проверена, поэтому только подсказка имени. */
export interface MaxUserInfo {
  first_name?: string;
  last_name?: string;
}
