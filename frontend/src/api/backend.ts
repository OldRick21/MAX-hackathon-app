// Единый интерфейс данных для UI. Компоненты работают только с ним,
// а реализация выбирается в api/index.ts: настоящая (real/) или тестовая (mock/).

import type {
  AcademicCardPatch, EventInput, Group, GroupDirectory, GroupEntry, InstitutionView, JoinOption, JoinRequest, JoinRequestItem, MaxUserInfo, Page,
  Profile, ProfileCard,
  ReviewInput, ScheduleEvent, ScheduleImportResult, SelfCardPatch, ServiceView, Submission, SubmissionStatus, User, Versioned, WidgetData, WidgetView,
} from './types';

export interface ScheduleApi {
  /** Занятия, пересекающие [from, to). Диапазон — не больше 31 суток. */
  listEvents(q: { from: string; to: string; group_id?: string; teacher_id?: string }, signal?: AbortSignal): Promise<ScheduleEvent[]>;
  getEvent(id: string): Promise<Versioned<ScheduleEvent>>;
  createEvent(input: EventInput): Promise<ScheduleEvent>;
  updateEvent(id: string, input: EventInput, etag: string): Promise<ScheduleEvent>;
  deleteEvent(id: string, etag: string): Promise<void>;
  listGroups(signal?: AbortSignal): Promise<Group[]>;
  /** Импорт из файла (.json, .xlsx, .csv/.txt из 1С, .xml): dryRun — только проверка, иначе всё или ничего. */
  importFile(file: File, dryRun: boolean): Promise<ScheduleImportResult>;
}

export interface ProfilesApi {
  getMe(signal?: AbortSignal): Promise<Versioned<ProfileCard>>;
  patchMe(patch: SelfCardPatch, etag: string): Promise<Versioned<ProfileCard>>;
  listUsers(q: string, cursor?: string | null, signal?: AbortSignal): Promise<Page<ProfileCard>>;
  getUser(id: string, signal?: AbortSignal): Promise<Versioned<ProfileCard>>;
  patchUser(id: string, patch: AcademicCardPatch, etag: string): Promise<Versioned<ProfileCard>>;
}

export type UploadProgress = (loaded: number, total: number) => void;

export interface CourseworkApi {
  list(status?: SubmissionStatus, signal?: AbortSignal): Promise<Submission[]>;
  get(id: string): Promise<Versioned<Submission>>;
  create(input: { title: string; teacher_id: string; file: File }, onProgress?: UploadProgress): Promise<Submission>;
  replaceFile(id: string, file: File, etag: string, onProgress?: UploadProgress): Promise<Submission>;
  review(id: string, input: ReviewInput, etag: string): Promise<Submission>;
  remove(id: string, etag: string): Promise<void>;
  download(id: string): Promise<Blob>;
}

export type FrameStatus = 'loading' | 'ready' | 'closed';

/** Встроенный клиент сервиса в iframe (протокол SDK v1). */
export interface ServiceFrameHandle {
  close(): void;
}

export interface FrameCallbacks {
  onStatus(status: FrameStatus): void;
  onError(error: unknown): void;
}

export interface Backend {
  mode: 'mock' | 'real';
  coreCall?: <T>(path: string, options?: import('./http').RequestOptions) => Promise<T>;
  coreCallMeta?: <T>(path: string, options?: import('./http').RequestOptions) => Promise<{data: T; etag: string | null}>;
  /** Имя из MAX — только подсказка в форме регистрации. */
  maxUser(): MaxUserInfo | null;
  /** Аватар пользователя (один на все вузы) или null, если его нет или он недоступен. */
  getAvatar(userId: string, signal?: AbortSignal): Promise<Blob | null>;
  /** Загрузить свой аватар: картинка уже уменьшена до квадрата. */
  setAvatar(image: Blob): Promise<void>;
  deleteAvatar(): Promise<void>;
  /** Вход через MAX. Бросает NotInMaxError, если приложение открыто не из MAX. */
  login(consent?: { consent_version: string; consent_challenge: string }): Promise<User>;
  clearSession?(): void;
  listInstitutions(signal?: AbortSignal): Promise<InstitutionView[]>;
  getInstitution(id: string, signal?: AbortSignal): Promise<InstitutionView>;
  /** Заявки на вступление: зарегистрированные вузы с группами, свои заявки, подача и отзыв. */
  listJoinOptions(signal?: AbortSignal): Promise<JoinOption[]>;
  listJoinRequests(signal?: AbortSignal): Promise<JoinRequest[]>;
  submitJoinRequests(fullName: string, items: JoinRequestItem[]): Promise<JoinRequest[]>;
  withdrawJoinRequest(id: string): Promise<JoinRequest>;
  getProfiles(institutionId: string, signal?: AbortSignal): Promise<Profile[]>;
  listServices(institutionId: string, profile: Profile, signal?: AbortSignal): Promise<ServiceView[]>;
  /** Виджеты главного экрана профиля (ядро фильтрует по включению и правам). */
  listWidgets(institutionId: string, profile: Profile, signal?: AbortSignal): Promise<WidgetView[]>;
  /** Данные виджета из API сервиса (service session экземпляра); null — показывать нечего. Таймаут 3 с. */
  widgetData(institutionId: string, profile: Profile, widget: WidgetView, signal?: AbortSignal): Promise<WidgetData | null>;
  /** Учебные группы из ядра: все группы вуза с составом — любому участнику. */
  listGroups(institutionId: string, profile: Profile, signal?: AbortSignal): Promise<GroupDirectory>;
  /** Правка групп — любой администратор вуза. */
  createGroup(institutionId: string, profile: Profile, name: string): Promise<GroupEntry>;
  renameGroup(institutionId: string, profile: Profile, group: GroupEntry, name: string): Promise<void>;
  deleteGroup(institutionId: string, profile: Profile, group: GroupEntry): Promise<void>;
  setGroupMembers(institutionId: string, profile: Profile, group: GroupEntry, userIds: string[]): Promise<void>;

  schedule(service: ServiceView): ScheduleApi;
  profiles(service: ServiceView): ProfilesApi;
  coursework(service: ServiceView): CourseworkApi;
  /** Закрыть сервисные сессии нативных экранов (при смене вуза/профиля). */
  releaseServices(): void;

  openFrame(service: ServiceView, menuId: string, host: HTMLElement, cb: FrameCallbacks): Promise<ServiceFrameHandle>;
  /** Открыть внешнюю ссылку (через MAX, если он доступен). */
  openLink(url: string): void;
}

export class NotInMaxError extends Error {
  constructor() {
    super('Откройте приложение через бота в MAX.');
  }
}
