import 'dotenv/config';
import { readFile } from 'node:fs/promises';
import { Bot, Keyboard } from '@maxhub/max-bot-api';
import { chatView } from './chat-flow.mjs';

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

function keyboard(buttons, withApp = false) {
  const rows = buttons.map((button) => [button.kind === 'link'
    ? Keyboard.button.link(button.text, button.url)
    : Keyboard.button.callback(button.text, `study-chat:${button.payload}`)]);
  if (withApp) rows.unshift([Keyboard.button.openApp('Открыть платформу', botLink, me.user_id)]);
  return Keyboard.inlineKeyboard(rows);
}

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
    const image = await bot.api.uploadImage({ source });
    return typeof image.toJson === 'function' ? image.toJson() : image;
  } catch (error) {
    console.error('Приветственная иллюстрация недоступна, продолжаю без неё:', error.message);
    return null;
  }
}

async function renderCallback(ctx, selection = '') {
  try {
    if (ctx.message?.recipient?.chat_type && ctx.message.recipient.chat_type !== 'dialog') {
      return ctx.answerOnCallback({
        message: { text: 'Чаты учебных групп доступны в личном диалоге с ботом.', attachments: [] },
      });
    }
    const maxUserId = ctx.user?.user_id;
    if (!maxUserId) throw new Error('callback without user id');
    const view = chatView(await resolve(maxUserId), selection);
    const attachments = view.buttons.length ? [keyboard(view.buttons)] : [];
    return ctx.answerOnCallback({ message: { text: view.text, attachments } });
  } catch (error) {
    console.error('Не удалось получить чат учебной группы:', error.message);
    return ctx.answerOnCallback({
      message: { text: 'Сервис временно недоступен. Попробуйте ещё раз позднее.', attachments: [] },
    });
  }
}

const welcomeImage = await uploadWelcomeImage();

async function showMenu(ctx, { withImage = false } = {}) {
  if (ctx.message?.recipient?.chat_type && ctx.message.recipient.chat_type !== 'dialog') return;

  const attachments = [];
  if (withImage && welcomeImage) attachments.push(welcomeImage);
  attachments.push(keyboard([
    { kind: 'callback', text: 'Найти чат группы', payload: 'open' },
  ], true));

  await ctx.reply('Добро пожаловать в «Вузы России».\n\nЗдесь можно открыть учебную платформу или перейти в чат своей группы.', {
    attachments,
  });
}

bot.on('bot_started', (ctx) => showMenu(ctx, { withImage: true }));
bot.command('start', (ctx) => showMenu(ctx, { withImage: true }));
bot.action('study-chat:open', (ctx) => renderCallback(ctx));
bot.action(/^study-chat:institution:([0-9a-f-]{36})$/, (ctx) => renderCallback(ctx, `institution:${ctx.match?.[1] || ''}`));
bot.action(/^study-chat:group:([0-9a-f-]{36})$/, (ctx) => renderCallback(ctx, `group:${ctx.match?.[1] || ''}`));
bot.on('message_created', showMenu);

console.log('Подключение к MAX…');
await bot.start();
