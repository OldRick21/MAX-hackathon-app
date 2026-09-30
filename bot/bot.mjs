import 'dotenv/config';
import { readFile } from 'node:fs/promises';
import { Bot, Keyboard } from '@maxhub/max-bot-api';
import { chatView, errorView, helpView, menuView } from './chat-flow.mjs';

const token = process.env.BOT_TOKEN?.trim();
const coreToken = process.env.BOT_CORE_TOKEN?.trim();
const coreUrl = (process.env.CORE_INTERNAL_URL || 'http://backend:8000').replace(/\/$/, '');

if (!token) throw new Error('Укажи BOT_TOKEN в файле .env');
if (!coreToken || coreToken.length < 32) throw new Error('Укажи BOT_CORE_TOKEN длиной не менее 32 символов');

const bot = new Bot(token, {
  clientOptions: {
    baseUrl: 'https://platform-api2.max.ru',
  },
});

const me = await bot.api.getMyInfo();

if (!me.username) {
  throw new Error('API не вернул username бота');
}

const botLink = `https://max.ru/${me.username}`;

function keyboard(rows, withApp = false) {
  const buttons = rows.map((row) => row.map((button) => (button.kind === 'link'
    ? Keyboard.button.link(button.text, button.url)
    : Keyboard.button.callback(button.text, `study-chat:${button.payload}`))));
  if (withApp) buttons.unshift([Keyboard.button.openApp('Открыть платформу', botLink, me.user_id)]);
  return Keyboard.inlineKeyboard(buttons);
}

const inDialog = (ctx) => !ctx.message?.recipient?.chat_type || ctx.message.recipient.chat_type === 'dialog';

async function resolve(maxUserId) {
  const response = await fetch(`${coreUrl}/api/v1/internal/bot/group-chats/resolve`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${coreToken}`, 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ max_user_id: String(maxUserId) }),
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error(`core returned ${response.status}`);
  return response.json();
}

async function uploadWelcomeImage() {
  try {
    const source = await readFile(new URL('./assets/welcome.png', import.meta.url));
    // Зависший API MAX не должен задерживать запуск бота: через 10 секунд — без картинки.
    const image = await Promise.race([
      bot.api.uploadImage({ source }),
      new Promise((_, reject) => { setTimeout(() => reject(new Error('таймаут 10 с')), 10000).unref(); }),
    ]);
    return typeof image.toJson === 'function' ? image.toJson() : image;
  } catch (error) {
    console.error('Приветственная иллюстрация недоступна, продолжаю без неё:', error.message);
    return null;
  }
}

const welcomeImage = await uploadWelcomeImage();

// Отправка с картинкой; если MAX её не принял (например, вложение устарело), — то же без неё.
async function withImageFallback(send, view, withApp, withImage) {
  const buttons = keyboard(view.rows, withApp);
  if (withImage && welcomeImage) {
    try {
      return await send({ text: view.text, attachments: [welcomeImage, buttons] });
    } catch (error) {
      console.error('Не удалось отправить картинку, отправляю без неё:', error.message);
    }
  }
  return send({ text: view.text, attachments: [buttons] });
}

async function showMenu(ctx, { withImage = false } = {}) {
  if (!inDialog(ctx)) return;
  await withImageFallback(({ text, attachments }) => ctx.reply(text, { attachments }), menuView(), true, withImage);
}

// Ответ на нажатие кнопки заменяет текущее сообщение — так кнопка «Назад» возвращает предыдущий экран.
const answer = (ctx, view, withApp = false, withImage = false) =>
  withImageFallback((message) => ctx.answerOnCallback({ message }), view, withApp, withImage);

async function renderCallback(ctx, selection = '') {
  if (!inDialog(ctx)) {
    return ctx.answerOnCallback({
      message: { text: 'Чаты учебных групп доступны в личном диалоге с ботом.', attachments: [] },
    });
  }
  let view;
  try {
    const maxUserId = ctx.user?.user_id;
    if (!maxUserId) throw new Error('callback without user id');
    view = chatView(await resolve(maxUserId), selection);
  } catch (error) {
    console.error('Не удалось получить чат учебной группы:', error.message);
    view = errorView(selection);
  }
  return answer(ctx, view);
}

bot.on('bot_started', (ctx) => showMenu(ctx, { withImage: true }));
bot.command('start', (ctx) => showMenu(ctx, { withImage: true }));
bot.action('study-chat:menu', (ctx) => answer(ctx, menuView(), true, true));
bot.action('study-chat:help', (ctx) => answer(ctx, helpView()));
bot.action('study-chat:open', (ctx) => renderCallback(ctx));
bot.action(/^study-chat:institution:([0-9a-f-]{36})$/, (ctx) => renderCallback(ctx, `institution:${ctx.match?.[1] || ''}`));
bot.action(/^study-chat:group:([0-9a-f-]{36})$/, (ctx) => renderCallback(ctx, `group:${ctx.match?.[1] || ''}`));
bot.on('message_created', showMenu);

console.log('Подключение к MAX…');
await bot.start();
