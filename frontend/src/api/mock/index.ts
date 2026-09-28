// Тестовая реализация Backend. Повторяет правила доступа из спецификаций сервисов,
// чтобы интерфейс вёл себя так же, как с настоящим сервером.
//
// Сценарии для проверки состояний (только в режиме mock), через адрес страницы:
//   ?scenario=single   — у пользователя один вуз (экран выбора не показывается)
//   ?scenario=none     — пользователь не состоит ни в одном вузе
//   ?scenario=errors   — первая загрузка расписания и курсовых падает (проверка «Повторить»)
//   ?now=2026-09-22T12:30 — подменить текущее время (проверка «текущего занятия»)

import type { Backend, CourseworkApi, ProfilesApi, ScheduleApi } from '../backend';
import { ApiError } from '../http';
import type { Profile, ProfileCard, ScheduleEvent, ServiceView, Submission } from '../types';
import { now, setClock } from '../../utils/time';
import avatar from './assets/demo-avatar.webp';
import * as D from './data';

const params = new URLSearchParams(location.search);
const scenario = params.get('scenario');
setClock(params.get('now'));

const wait = (ms = 250 + Math.random() * 350) => new Promise(r => setTimeout(r, ms));
const clone = <T,>(v: T): T => structuredClone(v);
const failOnce = new Set(scenario === 'errors' ? ['schedule', 'coursework'] : []);
function maybeFail(key: string) {
  if (failOnce.delete(key)) throw new ApiError('Сервис временно недоступен. Попробуйте позже.', 503);
}

// Версии ресурсов для If-Match.
const revisions = new Map<string, number>();
const etagOf = (id: string) => `"r${revisions.get(id) ?? 1}"`;
const bump = (id: string) => revisions.set(id, (revisions.get(id) ?? 1) + 1);
function checkEtag(id: string, etag: string) {
  if (etag !== etagOf(id)) throw new ApiError('Данные успели измениться. Обновите страницу и повторите действие.', 412);
}

export function createMockBackend(): Backend {
  const events = D.buildAllEvents();
  const submissions = D.buildSubmissions();
  const cards = clone(D.cards);

  const insts = scenario === 'none' ? [] : scenario === 'single' ? D.institutions.slice(0, 1) : D.institutions;

  const has = (s: ServiceView, p: string) => s.permissions.includes(p);

  const schedule = (s: ServiceView): ScheduleApi => {
    const inst = s.institution_id;
    const myGroup = () => D.groups[inst]?.find(g => D.groupStudents[g.id]?.includes(D.ME));
    const visible = (e: ScheduleEvent) => {
      if (s.profile === 'admin') return has(s, 'schedule.read_all');
      if (s.profile === 'teacher') return e.teacher_ids.includes(D.ME);
      const g = myGroup();
      return !!g && e.group_ids.includes(g.id);
    };
    const find = (id: string) => {
      const e = events[inst].find(x => x.id === id && visible(x));
      if (!e) throw new ApiError('Не удалось найти данные. Возможно, их удалили.', 404);
      return e;
    };
    // Занятия пишут преподаватели (только свои) и admin с schedule.write (любые) — как сервис.
    const writesAny = () => s.profile === 'admin' && has(s, 'schedule.write');
    const requireWrite = (input?: Omit<ScheduleEvent, 'id'>, existing?: string) => {
      if (!writesAny() && s.profile !== 'teacher') throw new ApiError('Нет доступа к этому действию.', 403);
      if (writesAny()) return;
      if (existing && !events[inst].find(e => e.id === existing)?.teacher_ids.includes(D.ME)) {
        throw new ApiError('Не удалось найти данные. Возможно, их удалили.', 404);
      }
      if (input && !input.teacher_ids.includes(D.ME)) throw new ApiError('Проверьте заполненные поля.', 422);
    };
    const requireGroups = () => {
      if (s.profile !== 'admin' || !has(s, 'groups.manage')) throw new ApiError('Нет доступа к этому действию.', 403);
    };
    const groupList = () => (D.groups[inst] ??= []);
    const findGroup = (id: string) => {
      const g = groupList().find(x => x.id === id);
      if (!g) throw new ApiError('Не удалось найти данные. Возможно, их удалили.', 404);
      return g;
    };
    const validate = (input: ScheduleEvent | Omit<ScheduleEvent, 'id'>) => {
      if (!(Date.parse(input.starts_at) < Date.parse(input.ends_at))) {
        throw new ApiError('Время окончания должно быть позже времени начала.', 422, 'INVALID_TIME_RANGE');
      }
    };
    return {
      async listEvents(q) {
        await wait();
        maybeFail('schedule');
        const from = Date.parse(q.from), to = Date.parse(q.to);
        if (to - from > 31 * 86400_000) throw new ApiError('Проверьте заполненные поля.', 422);
        return clone(events[inst]
          .filter(visible)
          .filter(e => Date.parse(e.starts_at) < to && Date.parse(e.ends_at) > from)
          .filter(e => !q.group_id || e.group_ids.includes(q.group_id))
          .filter(e => !q.teacher_id || e.teacher_ids.includes(q.teacher_id))
          .sort((a, b) => a.starts_at.localeCompare(b.starts_at) || a.id.localeCompare(b.id)));
      },
      async getEvent(id) { await wait(150); return { data: clone(find(id)), etag: etagOf(id) }; },
      async createEvent(input) {
        await wait(); requireWrite(input); validate(input);
        const e = { ...clone(input), id: crypto.randomUUID() };
        events[inst].push(e);
        return clone(e);
      },
      async updateEvent(id, input, etag) {
        await wait(); requireWrite(input, id); checkEtag(id, etag); validate(input);
        const i = events[inst].findIndex(e => e.id === id);
        events[inst][i] = { ...clone(input), id };
        bump(id);
        return clone(events[inst][i]);
      },
      async deleteEvent(id, etag) {
        await wait(); requireWrite(undefined, id); checkEtag(id, etag);
        events[inst] = events[inst].filter(e => e.id !== id);
      },
      async listGroups() {
        await wait(200);
        if (s.profile === 'teacher' || (s.profile === 'admin' && has(s, 'schedule.read_all'))) return clone(groupList());
        const g = myGroup();
        return g ? [clone(g)] : [];
      },
      async getGroup(id) { await wait(150); return { data: clone(findGroup(id)), etag: etagOf(`group:${id}`) }; },
      async createGroup(name) {
        await wait(); requireGroups();
        if (groupList().some(g => g.name.trim().toLowerCase() === name.trim().toLowerCase())) {
          throw new ApiError('Группа с таким названием уже есть.', 409, 'GROUP_ALREADY_EXISTS');
        }
        const g = { id: crypto.randomUUID(), name: name.trim() };
        groupList().push(g);
        return clone(g);
      },
      async renameGroup(id, name, etag) {
        await wait(); requireGroups(); checkEtag(`group:${id}`, etag);
        findGroup(id).name = name.trim();
        bump(`group:${id}`);
        return { data: clone(findGroup(id)), etag: etagOf(`group:${id}`) };
      },
      async deleteGroup(id, etag) {
        await wait(); requireGroups(); checkEtag(`group:${id}`, etag);
        if (D.groupStudents[id]?.length || events[inst].some(e => e.group_ids.includes(id))) {
          throw new ApiError('В группе есть студенты или занятия.', 409, 'GROUP_IN_USE');
        }
        D.groups[inst] = groupList().filter(g => g.id !== id);
      },
      async getStudents(id) {
        await wait(150); requireGroups(); findGroup(id);
        return { data: clone(D.groupStudents[id] ?? []), etag: etagOf(`students:${id}`) };
      },
      async setStudents(id, userIds, etag) {
        await wait(); requireGroups(); findGroup(id); checkEtag(`students:${id}`, etag);
        const taken = userIds.some(u => groupList().some(g => g.id !== id && D.groupStudents[g.id]?.includes(u)));
        if (taken) throw new ApiError('Студент уже состоит в другой группе.', 409, 'STUDENT_ALREADY_GROUPED');
        D.groupStudents[id] = [...userIds];
        bump(`students:${id}`);
        return { data: clone(userIds), etag: etagOf(`students:${id}`) };
      },
    };
  };

  const profiles = (s: ServiceView): ProfilesApi => {
    const inst = s.institution_id;
    const card = (id: string): ProfileCard =>
      cards[inst].find(c => c.user_id === id) ?? { user_id: id, display_name: null, about: '', position: null, academic_degree: null };
    const save = (c: ProfileCard) => {
      const i = cards[inst].findIndex(x => x.user_id === c.user_id);
      if (i >= 0) cards[inst][i] = c; else cards[inst].push(c);
      bump(`card:${inst}:${c.user_id}`);
    };
    const key = (id: string) => `card:${inst}:${id}`;
    return {
      async getMe() { await wait(); return { data: clone(card(D.ME)), etag: etagOf(key(D.ME)) }; },
      async patchMe(patch, etag) {
        await wait(); checkEtag(key(D.ME), etag);
        const c = { ...card(D.ME), ...patch };
        save(c);
        return { data: clone(c), etag: etagOf(key(D.ME)) };
      },
      async listUsers(q) {
        await wait();
        const needle = q.trim().toLowerCase();
        const items = cards[inst]
          .filter(c => c.display_name)
          .filter(c => !needle || [c.display_name, c.position, c.about].some(v => v?.toLowerCase().includes(needle)))
          .sort((a, b) => a.display_name!.localeCompare(b.display_name!, 'ru'));
        return { items: clone(items), next_cursor: null };
      },
      async getUser(id) {
        await wait();
        if (!cards[inst].some(c => c.user_id === id)) throw new ApiError('Не удалось найти данные. Возможно, их удалили.', 404);
        return { data: clone(card(id)), etag: etagOf(key(id)) };
      },
      async patchUser(id, patch, etag) {
        await wait();
        if (s.profile !== 'admin' || !has(s, 'profiles.manage')) throw new ApiError('Нет доступа к этому действию.', 403);
        checkEtag(key(id), etag);
        const c = { ...card(id), ...patch };
        save(c);
        return { data: clone(c), etag: etagOf(key(id)) };
      },
    };
  };

  const coursework = (s: ServiceView): CourseworkApi => {
    const inst = s.institution_id;
    const list = () => submissions[inst] ?? (submissions[inst] = []);
    const visible = (x: Submission) =>
      s.profile === 'student' ? x.student_id === D.ME
        : s.profile === 'teacher' ? x.teacher_id === D.ME
          : has(s, 'coursework.manage');
    const find = (id: string) => {
      const x = list().find(y => y.id === id && visible(y));
      if (!x) throw new ApiError('Не удалось найти данные. Возможно, их удалили.', 404);
      return x;
    };
    const checkFile = (f: File) => {
      if (f.size > 20 * 1024 * 1024) throw new ApiError('Файл слишком большой. Максимальный размер — 20 МБ.', 413);
      if (f.type && f.type !== 'application/pdf') throw new ApiError('Можно загрузить только PDF-файл.', 415);
    };
    const simulateUpload = async (f: File, onProgress?: (l: number, t: number) => void) => {
      for (let i = 1; i <= 10; i++) { await wait(120); onProgress?.(Math.round((f.size * i) / 10), f.size); }
    };
    const fileInfo = (f: File) => ({ original_name: f.name, size_bytes: f.size, media_type: 'application/pdf' as const, sha256: '0'.repeat(64) });
    return {
      async list(status) {
        await wait(); maybeFail('coursework');
        return clone(list().filter(visible).filter(x => !status || x.status === status)
          .sort((a, b) => b.updated_at.localeCompare(a.updated_at)));
      },
      async get(id) { await wait(150); return { data: clone(find(id)), etag: etagOf(id) }; },
      async create({ title, teacher_id, file }, onProgress) {
        if (s.profile !== 'student') throw new ApiError('Нет доступа к этому действию.', 403);
        checkFile(file);
        await simulateUpload(file, onProgress);
        const t = now().toISOString();
        const x: Submission = { id: crypto.randomUUID(), student_id: D.ME, teacher_id, title, status: 'submitted', version: 1,
          file: fileInfo(file), review: null, created_at: t, updated_at: t };
        list().unshift(x);
        return clone(x);
      },
      async replaceFile(id, file, etag, onProgress) {
        const x = find(id);
        checkEtag(id, etag);
        if (x.student_id !== D.ME || x.status === 'accepted') throw new ApiError('Это действие недоступно для работы в текущем статусе.', 409, 'INVALID_STATE');
        checkFile(file);
        await simulateUpload(file, onProgress);
        Object.assign(x, { file: fileInfo(file), version: x.version + 1, status: 'submitted', review: null, updated_at: now().toISOString() });
        bump(id);
        return clone(x);
      },
      async review(id, input, etag) {
        await wait();
        const x = find(id);
        checkEtag(id, etag);
        if (s.profile !== 'teacher' || x.teacher_id !== D.ME || x.student_id === D.ME) throw new ApiError('Нет доступа к этому действию.', 403);
        if (x.status !== 'submitted') throw new ApiError('Это действие недоступно для работы в текущем статусе.', 409, 'INVALID_STATE');
        const t = now().toISOString();
        Object.assign(x, { status: input.decision, review: { ...input, reviewer_id: D.ME, reviewed_at: t }, updated_at: t });
        bump(id);
        return clone(x);
      },
      async remove(id, etag) {
        await wait();
        const x = find(id);
        checkEtag(id, etag);
        const own = x.student_id === D.ME && s.profile === 'student' && x.status !== 'accepted';
        if (!own && !(s.profile === 'admin' && has(s, 'coursework.manage'))) throw new ApiError('Нет доступа к этому действию.', 403);
        submissions[inst] = list().filter(y => y.id !== id);
      },
      async download(id) {
        await wait();
        const x = find(id);
        return new Blob([`%PDF-1.4\n% Демонстрационный файл: ${x.file.original_name}\n%%EOF\n`], { type: 'application/pdf' });
      },
    };
  };

  return {
    mode: 'mock',
    maxUser: () => ({ first_name: 'Геннадий', last_name: 'Лужин', photo_url: avatar }),
    async login() {
      await wait(400);
      return { id: D.ME, max_user_id: '100200300', created_at: '2026-09-01T09:00:00Z' };
    },
    async listInstitutions() { await wait(); return clone(insts); },
    async getInstitution(id) {
      await wait(150);
      const i = insts.find(x => x.id === id);
      if (!i) throw new ApiError('Не удалось найти данные. Возможно, их удалили.', 404);
      return clone(i);
    },
    async getProfiles(id) {
      await wait(150);
      const i = insts.find(x => x.id === id);
      if (!i) throw new ApiError('Нет доступа к этому действию.', 403);
      return (['student', 'teacher', 'admin'] as Profile[]).filter(p => i.profiles.includes(p));
    },
    async listServices(id, profile) {
      await wait();
      return (D.serviceSeeds[id] ?? []).flatMap(seed => {
        const a = seed.access[profile];
        if (!a) return [];
        const { access: _omit, ...rest } = seed;
        return [{ ...clone(rest), institution_id: id, profile, roles: a.roles, permissions: a.permissions,
          menus: seed.menus.filter(m => a.menus.includes(m.id)) }];
      });
    },
    schedule,
    profiles,
    coursework,
    releaseServices() {},
    async openFrame() {
      await wait(900);
      // У тестовых сервисов нет настоящего клиента — показываем состояние «недоступен».
      throw new ApiError('Сервис временно недоступен. Попробуйте позже.', 503);
    },
    openLink(url) { window.open(url, '_blank', 'noopener,noreferrer'); },
  };
}
