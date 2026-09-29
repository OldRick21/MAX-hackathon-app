// Настоящая реализация Backend: ядро на том же origin + сервисы по api_base_url из каталога.

import type { Backend, CourseworkApi, ProfilesApi, ScheduleApi, UploadProgress } from '../backend';
import { NotInMaxError } from '../backend';
import { ApiError, newIdempotencyKey, request, toApiError } from '../http';
import { loadBridge, maxUserInfo, openExternal, waitInitData } from '../max';
import type {
  Group, GroupDirectory, InstitutionView, JoinOption, JoinRequest, Page, Profile, ProfileCard, ProfileList, ScheduleEvent,
  ScheduleImportResult, ServiceView, Submission, User,
  WidgetData, WidgetView,
} from '../types';
import { CoreSession, listAll } from './core';
import { openServiceFrame } from './frame';
import { ServiceSession } from './serviceSession';

const PROFILE_ORDER: Profile[] = ['student', 'teacher', 'admin'];

function need(etag: string | null): string {
  if (!etag) throw new ApiError('Сервер не вернул версию данных. Обновите страницу.');
  return etag;
}

/** Загрузка файла с прогрессом (fetch не умеет отдавать прогресс отправки). */
function upload<T>(url: string, method: string, form: FormData, token: string, headers: Record<string, string>, onProgress?: UploadProgress): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url);
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
    xhr.timeout = 120000;
    xhr.upload.onprogress = e => { if (e.lengthComputable) onProgress?.(e.loaded, e.total); };
    xhr.onload = () => {
      let payload: unknown = null;
      try { payload = JSON.parse(xhr.responseText); } catch { /* пустой ответ */ }
      if (xhr.status >= 200 && xhr.status < 300 && payload) resolve(payload as T);
      else reject(toApiError(xhr.status, payload, url));
    };
    xhr.onerror = xhr.ontimeout = () => reject(new ApiError('Не удалось загрузить файл. Проверьте интернет и повторите.'));
    xhr.send(form);
  });
}

export function createRealBackend(): Backend {
  const core = new CoreSession(() => window.dispatchEvent(new Event('vuzy:session-expired')));
  const sessions = new Map<string, ServiceSession>();
  const session = (s: ServiceView) => {
    const key = `${s.id}:${s.profile}`;
    let ss = sessions.get(key);
    if (!ss) { ss = new ServiceSession(core, s); sessions.set(key, ss); }
    else if (!ss.apiBase && s.api_base_url) ss.apiBase = s.api_base_url.replace(/\/+$/, '');
    return ss;
  };

  const schedule = (s: ServiceView): ScheduleApi => {
    const ss = session(s);
    return {
      async listEvents(q, signal) {
        return listAll<ScheduleEvent>(async cursor => {
          const params = new URLSearchParams({ from: q.from, to: q.to, limit: '100' });
          if (q.group_id) params.set('group_id', q.group_id);
          if (q.teacher_id) params.set('teacher_id', q.teacher_id);
          if (cursor) params.set('cursor', cursor);
          return (await ss.call<Page<ScheduleEvent>>(`/api/v1/schedule/events?${params}`, { signal })).data;
        }, e => e.id);
      },
      async getEvent(id) {
        const r = await ss.call<ScheduleEvent>(`/api/v1/schedule/events/${id}`);
        return { data: r.data, etag: need(r.etag) };
      },
      async createEvent(input) {
        return (await ss.call<ScheduleEvent>('/api/v1/schedule/events', {
          method: 'POST', body: input, headers: { 'Idempotency-Key': newIdempotencyKey() },
        })).data;
      },
      async updateEvent(id, input, etag) {
        return (await ss.call<ScheduleEvent>(`/api/v1/schedule/events/${id}`, {
          method: 'PUT', body: input, headers: { 'If-Match': etag },
        })).data;
      },
      async deleteEvent(id, etag) {
        await ss.call(`/api/v1/schedule/events/${id}`, { method: 'DELETE', headers: { 'If-Match': etag } });
      },
      async listGroups(signal) {
        return listAll<Group>(async cursor => {
          const params = new URLSearchParams({ limit: '100', ...(cursor ? { cursor } : {}) });
          return (await ss.call<Page<Group>>(`/api/v1/schedule/groups?${params}`, { signal })).data;
        }, g => g.id);
      },
      async importFile(file, dryRun) {
        const params = new URLSearchParams({ name: file.name, dry_run: String(dryRun) });
        return (await ss.call<ScheduleImportResult>(`/api/v1/schedule/import?${params}`, {
          method: 'POST', blob: file, headers: { 'Content-Type': 'application/octet-stream' },
        })).data;
      },
    };
  };

  const profiles = (s: ServiceView): ProfilesApi => {
    const ss = session(s);
    return {
      async getMe(signal) {
        const r = await ss.call<ProfileCard>('/api/v1/profile/me', { signal });
        return { data: r.data, etag: need(r.etag) };
      },
      async patchMe(patch, etag) {
        const r = await ss.call<ProfileCard>('/api/v1/profile/me', { method: 'PATCH', body: patch, headers: { 'If-Match': etag } });
        return { data: r.data, etag: need(r.etag) };
      },
      async listUsers(q, cursor, signal) {
        const params = new URLSearchParams({ limit: '50' });
        if (q.trim()) params.set('q', q.trim());
        if (cursor) params.set('cursor', cursor);
        return (await ss.call<Page<ProfileCard>>(`/api/v1/profile/users?${params}`, { signal })).data;
      },
      async getUser(id, signal) {
        const r = await ss.call<ProfileCard>(`/api/v1/profile/users/${id}`, { signal });
        return { data: r.data, etag: need(r.etag) };
      },
      async patchUser(id, patch, etag) {
        const r = await ss.call<ProfileCard>(`/api/v1/profile/users/${id}`, { method: 'PATCH', body: patch, headers: { 'If-Match': etag } });
        return { data: r.data, etag: need(r.etag) };
      },
    };
  };

  const coursework = (s: ServiceView): CourseworkApi => {
    const ss = session(s);
    return {
      async list(status, signal) {
        return listAll<Submission>(async cursor => {
          const params = new URLSearchParams({ limit: '100' });
          if (status) params.set('status', status);
          if (cursor) params.set('cursor', cursor);
          return (await ss.call<Page<Submission>>(`/api/v1/coursework/submissions?${params}`, { signal })).data;
        }, x => x.id);
      },
      async get(id) {
        const r = await ss.call<Submission>(`/api/v1/coursework/submissions/${id}`);
        return { data: r.data, etag: need(r.etag) };
      },
      async create({ title, teacher_id, file }, onProgress) {
        const form = new FormData();
        form.set('title', title);
        form.set('teacher_id', teacher_id);
        form.set('file', file, file.name);
        return upload<Submission>(ss.url('/api/v1/coursework/submissions'), 'POST', form, await ss.accessToken(),
          { 'Idempotency-Key': newIdempotencyKey() }, onProgress);
      },
      async replaceFile(id, file, etag, onProgress) {
        const form = new FormData();
        form.set('file', file, file.name);
        return upload<Submission>(ss.url(`/api/v1/coursework/submissions/${id}/file`), 'PUT', form, await ss.accessToken(),
          { 'If-Match': etag }, onProgress);
      },
      async review(id, input, etag) {
        return (await ss.call<Submission>(`/api/v1/coursework/submissions/${id}/review`, {
          method: 'PUT', body: input, headers: { 'If-Match': etag },
        })).data;
      },
      async remove(id, etag) {
        await ss.call(`/api/v1/coursework/submissions/${id}`, { method: 'DELETE', headers: { 'If-Match': etag } });
      },
      async download(id) {
        // Файл берём авторизованным fetch: токен не попадает в URL (требование спецификации).
        const { data } = await request<Response>(ss.url(`/api/v1/coursework/submissions/${id}/file`), {
          token: await ss.accessToken(), raw: true,
        });
        return data.blob();
      },
    };
  };

  return {
    mode: 'real',
    coreCall: (path, options) => core.call(path, options),
    coreCallMeta: (path, options) => core.callMeta(path, options),
    maxUser: maxUserInfo,
    async getAvatar(userId, signal) {
      try {
        return await (await core.call<Response>(`/api/v1/users/${userId}/avatar`, { signal, raw: true })).blob();
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    async setAvatar(image) {
      await core.call('/api/v1/users/me/avatar', { method: 'PUT', blob: image });
    },
    async deleteAvatar() {
      await core.call('/api/v1/users/me/avatar', { method: 'DELETE' });
    },
    async login() {
      await loadBridge();
      const data = await waitInitData();
      if (!data) throw new NotInMaxError();
      await core.login(data);
      return core.call<User>('/api/v1/auth/me');
    },
    listInstitutions: signal => listAll<InstitutionView>(async cursor => {
      const params = new URLSearchParams({ limit: '100', ...(cursor ? { cursor } : {}) });
      return core.call<Page<InstitutionView>>(`/api/v1/institution?${params}`, { signal });
    }, i => i.id),
    getInstitution: (id, signal) => core.call<InstitutionView>(`/api/v1/institution/${id}`, { signal }),
    listJoinOptions: async signal => (await core.call<Page<JoinOption>>('/api/v1/join/institutions', { signal })).items,
    listJoinRequests: async signal => (await core.call<Page<JoinRequest>>('/api/v1/join-requests', { signal })).items,
    async submitJoinRequests(full_name, items) {
      return (await core.call<Page<JoinRequest>>('/api/v1/join-requests', { method: 'POST', body: { full_name, items } })).items;
    },
    withdrawJoinRequest: id => core.call<JoinRequest>(`/api/v1/join-requests/${id}/withdraw`, { method: 'POST' }),
    async getProfiles(id, signal) {
      const list = await core.call<ProfileList>(`/api/v1/institution/${id}/profiles`, { signal });
      return PROFILE_ORDER.filter(p => list.profiles.includes(p));
    },
    async listServices(id, profile, signal) {
      const items = await listAll<ServiceView>(async cursor => {
        const params = new URLSearchParams({ profile, locale: 'ru', limit: '100', ...(cursor ? { cursor } : {}) });
        return core.call<Page<ServiceView>>(`/api/v1/institution/${id}/service?${params}`, { signal });
      }, s => s.id);
      return items.map(s => ({ ...s, menus: [...s.menus].sort((a, b) => a.order - b.order || a.id.localeCompare(b.id)) }));
    },
    async listWidgets(id, profile, signal) {
      const params = new URLSearchParams({ profile, locale: 'ru' });
      return (await core.call<Page<WidgetView>>(`/api/v1/institution/${id}/widgets?${params}`, { signal })).items;
    },
    async widgetData(institutionId, profile, widget, signal) {
      // Сессия экземпляра общая с его экранами (ключ id:profile); адрес данных — только из ответа ядра.
      const ss = session({ id: widget.service_id, institution_id: institutionId, profile, api_base_url: '' } as ServiceView);
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 3000);
      signal?.addEventListener('abort', () => ctrl.abort(), { once: true });
      try {
        const data = (await ss.getUrl<WidgetData | null>(widget.data_url, ctrl.signal)).data;
        if (data && data.kind !== widget.kind) throw new ApiError('Виджет ответил в другом формате.');
        return data;
      } finally { clearTimeout(timer); }
    },
    listGroups: (id, profile, signal) => core.call<GroupDirectory>(`/api/v1/institution/${id}/groups?profile=${profile}`, { signal }),
    async createGroup(id, profile, name) {
      const g = await core.call<Group>(`/api/v1/institution/${id}/groups?profile=${profile}`, { method: 'POST', body: { name } });
      return { ...g, user_ids: [] };
    },
    async renameGroup(id, profile, group, name) {
      await core.call(`/api/v1/institution/${id}/groups/${group.id}?profile=${profile}`,
        { method: 'PATCH', body: { name }, headers: { 'If-Match': group.etag ?? '' } });
    },
    async deleteGroup(id, profile, group) {
      await core.call(`/api/v1/institution/${id}/groups/${group.id}?profile=${profile}`,
        { method: 'DELETE', headers: { 'If-Match': group.etag ?? '' } });
    },
    async setGroupMembers(id, profile, group, userIds) {
      await core.call(`/api/v1/institution/${id}/groups/${group.id}/members?profile=${profile}`,
        { method: 'PUT', body: { user_ids: userIds }, headers: { 'If-Match': group.members_etag ?? '' } });
    },
    schedule,
    profiles,
    coursework,
    releaseServices() {
      sessions.forEach(s => s.dispose());
      sessions.clear();
    },
    openFrame: (service, menuId, host, cb) => openServiceFrame(core, service, menuId, 'ru', host, cb),
    openLink: openExternal,
  };
}
