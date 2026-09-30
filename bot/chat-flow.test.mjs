import test from 'node:test';
import assert from 'node:assert/strict';
import { chatView } from './chat-flow.mjs';

const first = '00000000-0000-4000-8000-000000000001';
const second = '00000000-0000-4000-8000-000000000002';
const groupA = '00000000-0000-4000-8000-000000000011';
const groupB = '00000000-0000-4000-8000-000000000012';

test('empty response has no buttons', () => {
  assert.deepEqual(chatView({ institutions: [] }).buttons, []);
});

test('one group immediately offers its link', () => {
  const view = chatView({ institutions: [{ id: first, name: 'Вуз', groups: [
    { id: groupA, name: 'ИВТ-1', chat_url: 'https://max.ru/join/one' },
  ] }] });
  assert.match(view.text, /ИВТ-1 · Вуз/);
  assert.deepEqual(view.buttons, [{ kind: 'link', text: 'Открыть чат', url: 'https://max.ru/join/one' }]);
});

test('multiple institutions and groups are selected in two steps', () => {
  const data = { institutions: [
    { id: first, name: 'Первый', groups: [{ id: groupA, name: 'A', chat_url: 'https://max.ru/a' }] },
    { id: second, name: 'Второй', groups: [{ id: groupB, name: 'B', chat_url: 'https://max.ru/b' }] },
  ] };
  assert.deepEqual(chatView(data).buttons.map((button) => button.payload),
    [`institution:${first}`, `institution:${second}`]);
  assert.deepEqual(chatView(data, `institution:${second}`).buttons.map((button) => button.payload), [`group:${groupB}`]);
  assert.match(chatView(data, `institution:${second}`).text, /Второй/);
  assert.equal(chatView(data, `group:${groupB}`).buttons[0].url, 'https://max.ru/b');
});

test('stale selection does not expose another link', () => {
  assert.deepEqual(chatView({ institutions: [] }, `group:${groupA}`).buttons, []);
});
