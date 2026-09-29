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
import type { GroupEntry, JoinRequest, Profile, ProfileCard, ScheduleEvent, ServiceView, Submission, WidgetData, WidgetView } from '../types';
import { dayKey, now, setClock } from '../../utils/time';
import avatar from './assets/demo-avatar.webp';
import * as D from './data';

const params = new URLSearchParams(location.search);
const scenario = params.get('scenario');
setClock(params.get('now'));

const plural = (n: number, one: string, few: string, many: string) =>
  n % 10 === 1 && n % 100 !== 11 ? one : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? few : many;
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

  const insts = scenario === 'none' ? [] : scenario === 'single' ? D.institutions.slice(0, 1) : [...D.institutions];
  const joinRequests: JoinRequest[] = [];
  const avatars = new Map<string, Blob | null>();
  const joinOptions = () => [...D.institutions, D.extraInstitution].map(i => ({
    id: i.id, display_name: i.display_name, groups: clone(D.groups[i.id] ?? []),
    profiles: insts.find(x => x.id === i.id)?.profiles ?? [],
  }));

  const has = (s: ServiceView, p: string) => s.permissions.includes(p);

  const schedule = (s: ServiceView): ScheduleApi => {
    const inst = s.institution_id;
    const myGroup = () => D.groups[inst]?.find(g => D.groupStudents[g.id]?.includes(D.ME));
    // Как сервис: видно всё; без фильтров — «своё» (студенту — группа, преподавателю — его занятия).
    const mine = (e: ScheduleEvent) => {
      if (s.profile === 'admin' || writesAny()) return true;
      if (s.profile === 'teacher') return e.teacher_ids.includes(D.ME);
      const g = myGroup();
      return !!g && e.group_ids.includes(g.id);
    };
    const find = (id: string) => {
      const e = events[inst].find(x => x.id === id);
      if (!e) throw new ApiError('Не удалось найти данные. Возможно, их удалили.', 404);
      return e;
    };
    // Как сервис: администратор правит всегда, преподаватель — если ему включили schedule.write.
    function writesAny() { return (s.profile === 'admin' || s.profile === 'teacher') && has(s, 'schedule.write'); }
    const requireWrite = (_input?: Omit<ScheduleEvent, 'id'>, _existing?: string) => {
      if (!writesAny()) throw new ApiError('Нет доступа к этому действию.', 403);
    };
    const groupList = () => (D.groups[inst] ??= []);
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
          .filter(e => q.group_id || q.teacher_id || mine(e))
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
        return clone(groupList());
      },
      async importFile(file, dryRun) {
        // Демо-режим: разбирается только JSON-список; настоящий разбор всех типов — в сервисе расписания.
        await wait();
        requireWrite();
        if (!file.name.toLowerCase().endsWith('.json')) {
          throw new ApiError('В демо-режиме проверяется только JSON; .xlsx, .csv, .txt и .xml разбирает сервис расписания.', 422, 'INVALID_FILE');
        }
        let rows: unknown;
        try { rows = JSON.parse(await file.text()); } catch { throw new ApiError('Файл не является JSON', 422, 'INVALID_FILE'); }
        const list = Array.isArray(rows) ? rows : [];
        return { total: list.length, to_create: list.length, duplicates: 0, created: dryRun ? 0 : list.length, errors: [], preview: [] };
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
          : true; // администратор видит все работы; удаляет — только с coursework.manage
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

  // Группы правит любой администратор вуза.
  const canManageGroups = (_id: string, profile: Profile) => profile === 'admin';
  const requireGroups = (id: string, profile: Profile) => {
    if (!canManageGroups(id, profile)) throw new ApiError('Нет доступа к этому действию.', 403);
  };

  // Данные виджетов — из тех же тестовых данных, с правилами доступа сервисов.
  async function mockWidgetData(svc: ServiceView, widget: WidgetView): Promise<WidgetData | null> {
    const inst = svc.institution_id;
    const day = (shift: number) => { const d = new Date(now()); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() + shift); return d; };
    if (widget.widget_id === 'me') {
      const c = (await profiles(svc).getMe()).data;
      const group = D.groups[inst]?.find(g => D.groupStudents[g.id]?.includes(D.ME));
      const label = { student: 'Студент', teacher: 'Преподаватель', admin: 'Администратор' }[svc.profile];
      // Как у сервиса «Люди»: считаются только поля, которые человек меняет сам; имя из заявки уже видно.
      const fields: [keyof ProfileCard, string][] = [['display_name', 'имя'], ['about', 'о себе']];
      // В демо у пользователя всегда есть имя из заявки, поэтому «имя» считается заполненным.
      const missing = fields.filter(([k]) => k !== 'display_name' && !String(c[k] ?? '').trim()).map(([, l]) => l);
      return { kind: 'profile', user_id: D.ME, title: c.display_name || 'Без имени',
        lines: [svc.profile === 'student' && group ? `${label} · Группа ${group.name}` : label, ...[c.position, c.academic_degree].filter((x): x is string => !!x)],
        progress: Math.round(100 * (fields.length - missing.length) / fields.length), ...(missing.length ? { hint: `Добавьте ${missing.join(', ')}` } : {}) };
    }
    if (widget.widget_id === 'today') {
      const api = schedule(svc);
      for (let shift = 0; shift < 7; shift++) {
        const items = await api.listEvents({ from: day(shift).toISOString(), to: day(shift + 1).toISOString() });
        if (items.length) return { kind: 'events', day: dayKey(day(shift)), items: items.slice(0, 10).map(e => ({
          title: e.title, starts_at: e.starts_at, ends_at: e.ends_at, place: e.location || 'онлайн', status: e.status })) };
      }
      return { kind: 'events', day: null, items: [], empty_text: 'На ближайшую неделю занятий нет' };
    }
    if (widget.widget_id === 'today_admin') {
      const names = new Map((D.groups[inst] ?? []).map(g => [g.id, g.name]));
      const today = events[inst].filter(e => Date.parse(e.starts_at) < day(1).getTime() && Date.parse(e.ends_at) > day(0).getTime())
        .sort((a, b) => a.starts_at.localeCompare(b.starts_at));
      return { kind: 'events', day: today.length ? dayKey(day(0)) : null, empty_text: 'Сегодня занятий в вузе нет',
        items: today.slice(0, 10).map(e => ({ title: e.title, starts_at: e.starts_at, ends_at: e.ends_at, status: e.status,
          place: [e.location || 'онлайн', e.group_ids.map(g => names.get(g) ?? '—').join(', ')].filter(Boolean).join(' · ') })),
        ...(today.length > 10 ? { more: today.length - 10 } : {}) };
    }
    if (widget.widget_id === 'today_stats') {
      const today = events[inst].filter(e => Date.parse(e.starts_at) < day(1).getTime() && Date.parse(e.ends_at) > day(0).getTime());
      const cancelled = today.filter(e => e.status === 'cancelled').length;
      return { kind: 'stat', value: today.length, unit: plural(today.length, 'занятие', 'занятия', 'занятий'), caption: `сегодня в вузе${cancelled ? `, отменено ${cancelled}` : ''}`,
        tone: cancelled ? 'warning' : 'normal' };
    }
    const works = (submissions[inst] ?? []);
    if (widget.widget_id === 'my_work') {
      const mine = works.filter(w => w.student_id === D.ME);
      const text = { submitted: ['на проверке', 'accent'], accepted: ['принята', 'normal'], changes_requested: ['нужны правки', 'warning'] } as const;
      return { kind: 'list', total: mine.length, empty_text: 'Работа ещё не загружена',
        items: mine.slice(0, 3).map(w => ({ title: w.title, subtitle: `Версия ${w.version}`, badge: text[w.status][0], tone: text[w.status][1] })) };
    }
    if (widget.widget_id === 'to_review') {
      const n = works.filter(w => w.teacher_id === D.ME && w.status === 'submitted').length;
      return { kind: 'stat', value: n, unit: plural(n, 'работа', 'работы', 'работ'), caption: n ? 'ждут проверки' : 'новых работ нет', tone: n ? 'warning' : 'normal' };
    }
    return null;
  }

  return {
    mode: 'mock',
    maxUser: () => ({ first_name: 'Геннадий', last_name: 'Лужин' }),
    async getAvatar(userId) {
      await wait(100);
      const own = avatars.get(userId);
      if (own !== undefined) return own;
      // Демо: у себя аватар есть с самого начала, пока его не удалят.
      return userId === D.ME ? (await fetch(avatar)).blob() : null;
    },
    async setAvatar(image) { await wait(); avatars.set(D.ME, image); },
    async deleteAvatar() { await wait(); avatars.set(D.ME, null); },
    async login() {
      await wait(400);
      return { id: D.ME, max_user_id: '100200300', created_at: '2026-09-01T09:00:00Z' };
    },
    async listInstitutions() { await wait(); return clone(insts); },
    async listJoinOptions() { await wait(); return joinOptions(); },
    async listJoinRequests() { await wait(); return clone(joinRequests); },
    async submitJoinRequests(fullName, items) {
      await wait();
      if (!fullName.trim()) throw new ApiError('Укажите имя.', 422);
      const created = items.map(item => {
        const opt = joinOptions().find(o => o.id === item.institution_id);
        if (!opt) throw new ApiError('Вуз не найден.', 404);
        if (joinRequests.some(r => r.institution_id === opt.id && r.status === 'pending')) throw new ApiError(`Заявка в вуз «${opt.display_name}» уже ждёт решения.`, 409);
        const group = opt.groups.find(g => g.id === item.group_id);
        if (item.profile === 'student' && !group) throw new ApiError('Выберите группу из списка.', 422);
        return {
          id: crypto.randomUUID(), institution_id: opt.id, institution_name: opt.display_name, user_id: D.ME,
          full_name: fullName.trim(), profile: item.profile, group_id: group?.id ?? null, group_name: group?.name ?? null,
          status: 'pending' as const, decision_reason: null, created_at: new Date().toISOString(), reviewed_at: null,
        };
      });
      joinRequests.unshift(...created);
      return clone(created);
    },
    async withdrawJoinRequest(id) {
      await wait();
      const r = joinRequests.find(x => x.id === id);
      if (!r || r.status !== 'pending') throw new ApiError('Заявка уже рассмотрена.', 409);
      r.status = 'withdrawn';
      return clone(r);
    },
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
    async listWidgets(id, profile) {
      await wait(150);
      const services = await this.listServices(id, profile);
      return services.flatMap(svc => D.widgetSeeds
        .filter(w => w.type === svc.service_type && w.profiles.includes(profile) && w.perms.every(p => svc.permissions.includes(p)))
        .map(w => ({ service_id: svc.id, service_type: svc.service_type, widget_id: w.id, display_name: w.title, locale: 'ru' as const,
          kind: w.kind, size: w.size, data_url: `${svc.api_base_url}${w.path}`,
          open_menu: svc.menus.some(m => m.id === w.open) ? w.open : null, order: w.order })));
    },
    async widgetData(id, profile, widget) {
      await wait(200);
      const svc = (await this.listServices(id, profile)).find(x => x.id === widget.service_id);
      if (!svc) throw new ApiError('Нет доступа к этому действию.', 403);
      return mockWidgetData(svc, widget);
    },
    async listGroups(id, profile) {
      await wait(150);
      const manage = canManageGroups(id, profile);
      const items: GroupEntry[] = (D.groups[id] ?? []).map(g => ({
        ...g, user_ids: [...(D.groupStudents[g.id] ?? [])].sort(),
        ...(manage ? { etag: etagOf(`group:${g.id}`), members_etag: etagOf(`members:${g.id}`) } : {}),
      })).sort((a, b) => a.name.localeCompare(b.name, 'ru'));
      return clone({
        items, can_manage: manage,
        my_group_ids: items.filter(g => g.user_ids.includes(D.ME)).map(g => g.id),
        ...(manage ? { students: D.students[id] ?? [] } : {}),
      });
    },
    async createGroup(id, profile, name) {
      await wait(); requireGroups(id, profile);
      const list = (D.groups[id] ??= []);
      if (list.some(g => g.name.toLowerCase() === name.trim().toLowerCase())) throw new ApiError('Группа с таким названием уже есть.', 409, 'GROUP_ALREADY_EXISTS');
      const g = { id: crypto.randomUUID(), name: name.trim() };
      list.push(g);
      return { ...g, user_ids: [], etag: etagOf(`group:${g.id}`), members_etag: etagOf(`members:${g.id}`) };
    },
    async renameGroup(id, profile, group, name) {
      await wait(); requireGroups(id, profile); checkEtag(`group:${group.id}`, group.etag ?? '');
      const g = (D.groups[id] ?? []).find(x => x.id === group.id);
      if (!g) throw new ApiError('Не удалось найти данные. Возможно, их удалили.', 404);
      g.name = name.trim(); bump(`group:${group.id}`);
    },
    async deleteGroup(id, profile, group) {
      await wait(); requireGroups(id, profile); checkEtag(`group:${group.id}`, group.etag ?? '');
      if ((D.groupStudents[group.id] ?? []).length) throw new ApiError('В группе есть студенты или занятия. Сначала уберите их.', 409, 'GROUP_IN_USE');
      D.groups[id] = (D.groups[id] ?? []).filter(x => x.id !== group.id);
    },
    async setGroupMembers(id, profile, group, userIds) {
      await wait(); requireGroups(id, profile); checkEtag(`members:${group.id}`, group.members_etag ?? '');
      const taken = (D.groups[id] ?? []).filter(g => g.id !== group.id).some(g => (D.groupStudents[g.id] ?? []).some(u => userIds.includes(u)));
      if (taken) throw new ApiError('Студент уже состоит в другой группе. Сначала уберите его оттуда.', 409, 'STUDENT_ALREADY_GROUPED');
      D.groupStudents[group.id] = [...userIds]; bump(`members:${group.id}`);
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
