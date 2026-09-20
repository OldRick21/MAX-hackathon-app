import 'dotenv/config';
import { Bot, Keyboard } from '@maxhub/max-bot-api';

const token = process.env.BOT_TOKEN?.trim();

if (!token) {
  throw new Error('Укажи BOT_TOKEN в файле .env');
}

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

async function showApp(ctx) {
  const keyboard = Keyboard.inlineKeyboard([
    [Keyboard.button.openApp(
      'Открыть приложение',
      botLink,
      me.user_id
    )],
  ]);

  await ctx.reply('Привет! Нажми кнопку, чтобы открыть приложение.', {
    attachments: [keyboard],
  });
}

bot.on('bot_started', showApp);
bot.command('start', showApp);
bot.on('message_created', showApp);

console.log('Подключение к MAX…');
await bot.start();
