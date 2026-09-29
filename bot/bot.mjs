import 'dotenv/config';
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
  if (withApp) rows.unshift([Keyboard.button.openApp('Открыть приложение', botLink, me.user_id)]);
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

async function renderCallback(ctx, selection = '') {
  try {
    if (ctx.message?.recipient?.chat_type && ctx.message.recipient.chat_type !== 'dialog') {
      return ctx.answerOnCallback({
        message: { text: 'Чаты учебных групп доступны только в личном диалоге с ботом.', attachments: [] },
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
      message: { text: 'Не удалось получить чат. Попробуйте ещё раз позже.', attachments: [] },
    });
  }
}

async function showMenu(ctx) {
  if (ctx.message?.recipient?.chat_type && ctx.message.recipient.chat_type !== 'dialog') return;
  await ctx.reply('Привет! Выберите действие.', {
    attachments: [keyboard([{ kind: 'callback', text: 'Чат учебной группы', payload: 'open' }], true)],
  });
}

bot.on('bot_started', showMenu);
bot.command('start', showMenu);
bot.action('study-chat:open', (ctx) => renderCallback(ctx));
bot.action(/^study-chat:institution:([0-9a-f-]{36})$/, (ctx) => renderCallback(ctx, `institution:${ctx.match?.[1] || ''}`));
bot.action(/^study-chat:group:([0-9a-f-]{36})$/, (ctx) => renderCallback(ctx, `group:${ctx.match?.[1] || ''}`));
bot.on('message_created', showMenu);

console.log('Подключение к MAX…');
await bot.start();
