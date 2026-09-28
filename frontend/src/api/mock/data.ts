// Вымышленные данные для разработки без сервера. Формат — строго как в контрактах API.

import type { Group, InstitutionView, Profile, ProfileCard, ScheduleEvent, ServiceView, Submission } from '../types';
import { addDays, dayKey, mskToIso, now, startOfWeek } from '../../utils/time';

export const ME = '6f1c2a4e-3b7d-4c1e-9a52-0d4e8b7a1c01';

export const INST_MEPHI = '2b0f5c1a-8d3e-4f6a-b1c2-5e9d7a3f0b11';
export const INST_MTUCI = '9a7e3d2c-1b4f-4e8a-a6c5-3d2f1e0b9c22';

export const institutions: InstitutionView[] = [
  { id: INST_MEPHI, display_name: 'Национальный исследовательский ядерный университет “МИФИ”', locale: 'ru', default_locale: 'ru', status: 'active', profiles: ['student', 'teacher'] },
  { id: INST_MTUCI, display_name: 'Московский технический университет связи и информатики', locale: 'ru', default_locale: 'ru', status: 'active', profiles: ['teacher', 'admin'] },
];

/** Вуз, в котором пользователь ещё не состоит, — для формы заявки. */
export const extraInstitution: InstitutionView = {
  id: '5c3d1e2f-7a8b-4c9d-8e0f-1a2b3c4d5e33', display_name: 'Московский физико-технический институт', locale: 'ru',
  default_locale: 'ru', status: 'active', profiles: [],
};

const T = {
  ivanov: 'a1e0c7b2-0000-4000-8000-000000000001',
  petrova: 'a1e0c7b2-0000-4000-8000-000000000002',
  sokolov: 'a1e0c7b2-0000-4000-8000-000000000003',
  kuznetsova: 'a1e0c7b2-0000-4000-8000-000000000004',
  morozov: 'a1e0c7b2-0000-4000-8000-000000000005',
};
const S = {
  belova: 'b2f1d8c3-0000-4000-8000-000000000011',
  orlov: 'b2f1d8c3-0000-4000-8000-000000000012',
  zaytseva: 'b2f1d8c3-0000-4000-8000-000000000013',
  frolov: 'b2f1d8c3-0000-4000-8000-000000000014',
};

/** Анкеты по вузам. Тип профиля (студент/преподаватель) в анкете по контракту не хранится. */
export const cards: Record<string, ProfileCard[]> = {
  [INST_MEPHI]: [
    { user_id: ME, display_name: 'Геннадий Лужин', about: 'Студент кафедры 42. Интересуюсь криптографическими протоколами и CTF.', position: null, academic_degree: null },
    { user_id: T.ivanov, display_name: 'Иванов Сергей Андреевич', about: 'Веду курс по безопасности компьютерных сетей.', position: 'Доцент кафедры 42 «Криптология и кибербезопасность»', academic_degree: 'Кандидат технических наук' },
    { user_id: T.petrova, display_name: 'Петрова Анна Викторовна', about: '', position: 'Старший преподаватель кафедры 42', academic_degree: null },
    { user_id: T.sokolov, display_name: 'Соколов Дмитрий Олегович', about: 'Научные интересы: программно-аппаратная защита информации.', position: 'Профессор кафедры 36', academic_degree: 'Доктор технических наук' },
    { user_id: S.belova, display_name: 'Белова Мария', about: 'Староста группы Б21-161.', position: null, academic_degree: null },
    { user_id: S.orlov, display_name: 'Орлов Никита', about: '', position: null, academic_degree: null },
    { user_id: S.zaytseva, display_name: 'Зайцева Полина', about: 'Олимпиадное программирование.', position: null, academic_degree: null },
    { user_id: S.frolov, display_name: 'Фролов Артём', about: '', position: null, academic_degree: null },
  ],
  [INST_MTUCI]: [
    { user_id: ME, display_name: 'Геннадий Лужин', about: 'Ассистент кафедры информационной безопасности.', position: 'Ассистент кафедры ИБ', academic_degree: null },
    { user_id: T.kuznetsova, display_name: 'Кузнецова Елена Павловна', about: '', position: 'Заведующая кафедрой ИБ', academic_degree: 'Доктор технических наук' },
    { user_id: T.morozov, display_name: 'Морозов Павел Игоревич', about: '', position: 'Доцент кафедры ИБ', academic_degree: 'Кандидат физико-математических наук' },
  ],
};

export const groups: Record<string, Group[]> = {
  [INST_MEPHI]: [
    { id: 'c3a2e9d4-0000-4000-8000-000000000021', name: 'Б21-161' },
    { id: 'c3a2e9d4-0000-4000-8000-000000000022', name: 'Б21-162' },
  ],
  '5c3d1e2f-7a8b-4c9d-8e0f-1a2b3c4d5e33': [
    { id: 'c3a2e9d4-0000-4000-8000-000000000041', name: 'Б05-321' },
    { id: 'c3a2e9d4-0000-4000-8000-000000000042', name: 'Б05-322' },
  ],
  [INST_MTUCI]: [
    { id: 'c3a2e9d4-0000-4000-8000-000000000031', name: 'БИБ2301' },
    { id: 'c3a2e9d4-0000-4000-8000-000000000032', name: 'БИБ2302' },
  ],
};

/** Членство студентов в группах (агрегат «состав группы»). */
export const groupStudents: Record<string, string[]> = {
  'c3a2e9d4-0000-4000-8000-000000000021': [ME, S.belova, S.orlov],
  'c3a2e9d4-0000-4000-8000-000000000022': [S.zaytseva, S.frolov],
};

// Пары МИФИ (время московское).
const PAIRS = [['08:30', '10:05'], ['10:15', '11:50'], ['11:55', '13:30'], ['14:30', '16:05'], ['16:15', '17:50'], ['18:00', '19:35']];

type Slot = [weekday: number, pair: number, title: string, location: string, teachers: string[], groupIdx: number[], extra?: Partial<ScheduleEvent>];

const MEPHI_WEEK: Slot[] = [
  [0, 1, 'Теория информации', 'А-100', [T.sokolov], [0, 1]],
  [0, 2, 'Криптографические протоколы', 'НЛК-4.66', [T.ivanov], [0]],
  [1, 2, 'Безопасность компьютерных сетей', 'НЛК-3.2', [T.ivanov], [0]],
  [1, 3, 'Криптографические протоколы', 'НЛК-4.66', [T.ivanov], [0]],
  [1, 4, 'Программно-аппаратные средства защиты информации', 'НЛК-3.3', [T.sokolov], [0]],
  [2, 0, 'Иностранный язык', 'Б-210', [T.petrova], [0, 1]],
  [2, 1, 'Методы анализа защищённости', '', [T.ivanov], [0],
    { description: 'Онлайн-занятие. Ссылка: https://max.ru/join/mephi-security-lab' }],
  [3, 2, 'Безопасность компьютерных сетей', 'НЛК-3.2', [T.ivanov], [0]],
  [3, 3, 'Защита информации от утечки по техническим каналам', 'К-608', [T.sokolov], [0], { status: 'cancelled' }],
  [4, 1, 'Программно-аппаратные средства защиты информации', 'НЛК-3.3', [T.sokolov], [0]],
  [4, 2, 'Семинар по выпускной работе', '', [T.petrova], [0]],
  [0, 3, 'Лабораторный практикум по сетям (ассистент)', 'НЛК-3.1', [ME], [1]],
  [2, 3, 'Лабораторный практикум по сетям (ассистент)', 'НЛК-3.1', [ME], [1]],
];

const MTUCI_WEEK: Slot[] = [
  [0, 1, 'Основы информационной безопасности', 'Ауд. 318', [ME], [0]],
  [1, 2, 'Основы информационной безопасности', 'Ауд. 318', [ME], [1]],
  [2, 0, 'Криптографические методы защиты', 'Ауд. 402', [T.morozov], [0, 1]],
  [3, 1, 'Сети и системы передачи данных', 'Ауд. 215', [T.kuznetsova], [0]],
  [4, 3, 'Консультация по курсовым работам', '', [ME], [0, 1],
    { description: 'Подключиться: https://max.ru/join/mtuci-consult' }],
];

let seq = 0;
const eventId = () => `e0000000-0000-4000-8000-${String(++seq).padStart(12, '0')}`;

function buildEvents(week: Slot[], inst: string): ScheduleEvent[] {
  const out: ScheduleEvent[] = [];
  const monday = startOfWeek(now());
  for (let w = -2; w <= 3; w++) {
    for (const [wd, pair, title, location, teachers, gi, extra] of week) {
      const date = dayKey(addDays(monday, w * 7 + wd));
      out.push({
        id: eventId(),
        title,
        starts_at: mskToIso(date, PAIRS[pair][0]),
        ends_at: mskToIso(date, PAIRS[pair][1]),
        group_ids: gi.map(i => groups[inst][i].id),
        teacher_ids: teachers,
        location,
        description: '',
        status: 'scheduled',
        ...extra,
      });
    }
  }
  return out;
}

export const buildAllEvents = (): Record<string, ScheduleEvent[]> => ({
  [INST_MEPHI]: buildEvents(MEPHI_WEEK, INST_MEPHI),
  [INST_MTUCI]: buildEvents(MTUCI_WEEK, INST_MTUCI),
});

const sha = 'a3f5c1e9b7d2046e8f1a3c5e7b9d1f2a4c6e8b0d2f4a6c8e0b2d4f6a8c0e2b4d';

export function buildSubmissions(): Record<string, Submission[]> {
  const d = (days: number) => addDays(now(), -days).toISOString();
  return {
    [INST_MEPHI]: [
      { id: 'f1000000-0000-4000-8000-000000000001', student_id: ME, teacher_id: T.ivanov, title: 'Анализ протокола TLS 1.3 на устойчивость к атакам понижения версии', status: 'changes_requested', version: 2,
        file: { original_name: 'Лужин_TLS13_v2.pdf', size_bytes: 2_184_512, media_type: 'application/pdf', sha256: sha },
        review: { decision: 'changes_requested', comment: 'Добавьте сравнение с TLS 1.2 и список источников по ГОСТ.', reviewer_id: T.ivanov, reviewed_at: d(2) },
        created_at: d(20), updated_at: d(2) },
      { id: 'f1000000-0000-4000-8000-000000000002', student_id: ME, teacher_id: T.sokolov, title: 'Аппаратный модуль доверенной загрузки: обзор решений', status: 'submitted', version: 1,
        file: { original_name: 'Лужин_АМДЗ.pdf', size_bytes: 1_048_900, media_type: 'application/pdf', sha256: sha },
        review: null, created_at: d(4), updated_at: d(4) },
      { id: 'f1000000-0000-4000-8000-000000000003', student_id: S.belova, teacher_id: ME, title: 'Сегментация корпоративной сети с помощью VLAN', status: 'submitted', version: 1,
        file: { original_name: 'Белова_VLAN.pdf', size_bytes: 3_402_110, media_type: 'application/pdf', sha256: sha },
        review: null, created_at: d(1), updated_at: d(1) },
      { id: 'f1000000-0000-4000-8000-000000000004', student_id: S.orlov, teacher_id: ME, title: 'Настройка IDS Suricata для учебного стенда', status: 'accepted', version: 1,
        file: { original_name: 'Орлов_Suricata.pdf', size_bytes: 980_000, media_type: 'application/pdf', sha256: sha },
        review: { decision: 'accepted', comment: 'Хорошая работа.', reviewer_id: ME, reviewed_at: d(6) },
        created_at: d(12), updated_at: d(6) },
    ],
    [INST_MTUCI]: [],
  };
}

type ServiceSeed = Omit<ServiceView, 'institution_id' | 'profile' | 'roles' | 'permissions'> & {
  access: Partial<Record<Profile, { roles: string[]; permissions: string[]; menus: string[] }>>;
};

const svc = (id: string, type: string, display: string, deployment: 'cloud' | 'local', host: string,
  menus: ServiceView['menus'], access: ServiceSeed['access']): ServiceSeed => ({
  id, service_type: type, deployment, display_name: display, locale: 'ru',
  api_base_url: `https://${host}/api/v1`, client_base_url: `https://${host}`, menus, access,
});

const m = (id: string, name: string, path: string, order = 0) => ({ id, display_name: name, locale: 'ru' as const, entrypoint_path: path, order });
const basic = (menus: string[]) => ({ roles: [], permissions: [], menus });

export const serviceSeeds: Record<string, ServiceSeed[]> = {
  [INST_MEPHI]: [
    svc('5e000000-0000-4000-8000-000000000101', 'user-profile', 'Люди', 'cloud', 'profiles.platform.example',
      [m('home', 'Главная', '/home'), m('users', 'Пользователи', '/users', 10)],
      { student: basic(['home', 'users']), teacher: basic(['home', 'users']) }),
    svc('5e000000-0000-4000-8000-000000000102', 'schedule', 'Расписание', 'cloud', 'schedule.platform.example',
      [m('schedule', 'Расписание', '/schedule')],
      { student: basic(['schedule']), teacher: basic(['schedule']) }),
    svc('5e000000-0000-4000-8000-000000000103', 'coursework', 'Курсовые работы', 'local', 'coursework.mephi.example',
      [m('coursework', 'Курсовые', '/coursework')],
      { student: basic(['coursework']), teacher: basic(['coursework']) }),
  ],
  [INST_MTUCI]: [
    svc('5e000000-0000-4000-8000-000000000201', 'user-profile', 'Люди', 'cloud', 'profiles.platform.example',
      [m('home', 'Главная', '/home'), m('users', 'Пользователи', '/users', 10)],
      { teacher: basic(['home', 'users']), admin: { roles: ['profile_editor'], permissions: ['profiles.manage'], menus: ['home', 'users'] } }),
    svc('5e000000-0000-4000-8000-000000000202', 'schedule', 'Расписание', 'cloud', 'schedule.platform.example',
      [m('schedule', 'Расписание', '/schedule'), m('schedule_admin', 'Расписание', '/schedule')],
      { teacher: basic(['schedule']),
        admin: { roles: ['schedule_editor'], permissions: ['schedule.read_all', 'schedule.write', 'groups.manage'], menus: ['schedule_admin'] } }),
    svc('5e000000-0000-4000-8000-000000000203', 'administration', 'Администрирование', 'cloud', 'admin.platform.example',
      [m('administration', 'Администрирование', '/admin')],
      { admin: { roles: ['owner'], permissions: ['institution.read', 'members.read', 'members.manage'], menus: ['administration'] } }),
    // Свой сервис вуза, подключённый через пульт: открывается в iframe.
    svc('5e000000-0000-4000-8000-000000000204', 'custom.coursework', 'Курсовые работы', 'local', 'coursework.mtuci.example',
      [m('coursework', 'Курсовые работы', '/')],
      { teacher: basic(['coursework']), admin: basic(['coursework']) }),
  ],
};
