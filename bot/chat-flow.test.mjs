import test from 'node:test';
import assert from 'node:assert/strict';
import { chatView, errorView, helpView, menuView } from './chat-flow.mjs';

const first = '00000000-0000-4000-8000-000000000001';
const second = '00000000-0000-4000-8000-000000000002';
const groupA = '00000000-0000-4000-8000-000000000011';
const groupB = '00000000-0000-4000-8000-000000000012';
const groupC = '00000000-0000-4000-8000-000000000013';

const buttons = (view) => view.rows.flat();
const payloads = (view) => buttons(view).map((button) => button.payload ?? button.url);

test('empty response offers only the way back to the menu', () => {
  assert.deepEqual(payloads(chatView({ institutions: [] })), ['menu']);
});

test('one group immediately offers its link and a way back', () => {
  const view = chatView({ institutions: [{ id: first, name: 'Вуз', groups: [
    { id: groupA, name: 'ИВТ-1', chat_url: 'https://max.ru/join/one' },
  ] }] });
  assert.match(view.text, /ИВТ-1 · Вуз/);
  assert.deepEqual(view.rows[0], [{ kind: 'link', text: 'Открыть чат', url: 'https://max.ru/join/one' }]);
  assert.deepEqual(payloads(view), ['https://max.ru/join/one', 'menu']);
});

test('multiple institutions and groups are selected in two steps, back returns one step', () => {
  const data = { institutions: [
    { id: first, name: 'Первый', groups: [{ id: groupA, name: 'A', chat_url: 'https://max.ru/a' }] },
    { id: second, name: 'Второй', groups: [
      { id: groupB, name: 'B', chat_url: 'https://max.ru/b' },
      { id: groupC, name: 'C', chat_url: 'https://max.ru/c' },
    ] },
  ] };
  assert.deepEqual(payloads(chatView(data)), [`institution:${first}`, `institution:${second}`, 'menu']);

  const institution = chatView(data, `institution:${second}`);
  assert.match(institution.text, /Второй/);
  // Короткие названия групп — в один ряд; «Назад» — к списку вузов, рядом «Главное меню».
  assert.deepEqual(institution.rows[0].map((button) => button.payload), [`group:${groupB}`, `group:${groupC}`]);
  assert.deepEqual(institution.rows.at(-1).map((button) => button.payload), ['open', 'menu']);

  const group = chatView(data, `group:${groupB}`);
  assert.equal(group.rows[0][0].url, 'https://max.ru/b');
  assert.deepEqual(group.rows.at(-1).map((button) => button.payload), [`institution:${second}`, 'menu']);
  // У вуза одна группа: «Назад» ведёт сразу к списку вузов.
  assert.deepEqual(chatView(data, `group:${groupA}`).rows.at(-1).map((button) => button.payload), ['open', 'menu']);
});

test('institutions without groups are not offered', () => {
  const data = { institutions: [
    { id: first, name: 'Пустой', groups: [] },
    { id: second, name: 'Второй', groups: [
      { id: groupB, name: 'B', chat_url: 'https://max.ru/b' },
      { id: groupC, name: 'C', chat_url: 'https://max.ru/c' },
    ] },
  ] };
  const view = chatView(data);
  assert.match(view.text, /Второй/);
  assert.deepEqual(view.rows.at(-1).map((button) => button.payload), ['menu']);
});

test('long group names get a row each', () => {
  const view = chatView({ institutions: [{ id: first, name: 'Вуз', groups: [
    { id: groupA, name: 'Очень длинное название группы', chat_url: 'https://max.ru/a' },
    { id: groupB, name: 'B', chat_url: 'https://max.ru/b' },
  ] }] });
  assert.equal(view.rows[0].length, 1);
  assert.equal(view.rows[1].length, 1);
});

test('stale selection does not expose another link and offers a refresh', () => {
  const view = chatView({ institutions: [] }, `group:${groupA}`);
  assert.equal(buttons(view).some((button) => button.kind === 'link'), false);
  assert.deepEqual(payloads(view), ['open', 'menu']);
});

test('menu, help and error screens keep navigation', () => {
  assert.deepEqual(payloads(menuView()), ['open', 'help']);
  assert.deepEqual(payloads(helpView()), ['menu']);
  assert.deepEqual(payloads(errorView(`group:${groupA}`)), [`group:${groupA}`, 'menu']);
  assert.deepEqual(payloads(errorView()), ['open', 'menu']);
});
