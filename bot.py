import os
import json
import asyncio
import logging
import threading
from collections import defaultdict
from datetime import datetime, date
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
import openai
from flask import Flask, request

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загружаем переменные окружения
load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')

# Настройка DeepSeek клиента
deepseek_client = openai.OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com/v1"
)

# Хранилище сообщений по чатам
message_store = defaultdict(list)
MAX_MESSAGES_PER_CHAT = 5000

# ============================================
# Flask приложение для вебхуков
# ============================================
webhook_app = Flask(__name__)
bot_instance = None  # Сюда сохраним клиента
loop_instance = None  # Сюда сохраним цикл событий

@webhook_app.route('/', methods=['POST'])
def webhook():
    """Обработчик входящих обновлений от Telegram"""
    global bot_instance, loop_instance
    if bot_instance and loop_instance:
        update = request.get_json()
        asyncio.run_coroutine_threadsafe(
            bot_instance.handle_update(update),
            loop_instance
        )
    return "OK", 200

@webhook_app.route('/')
def health():
    return "Bot is running", 200

# ============================================
# Функция запуска бота
# ============================================
def start_bot():
    global bot_instance, loop_instance

    # Создаем цикл событий
    loop_instance = asyncio.new_event_loop()
    asyncio.set_event_loop(loop_instance)

    # Импортируем Pyrogram
    from pyrogram import Client, filters, enums
    from pyrogram.types import Message

    # Создаем клиента
    bot_instance = Client(
        "monitor_chat_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN
    )

    # ========== ВСЕ ОБРАБОТЧИКИ ==========

    @bot_instance.on_message(filters.text & ~filters.bot)
    async def store_message(client, message: Message):
        try:
            chat_id = message.chat.id

            if message.from_user:
                user_name = message.from_user.first_name
                if message.from_user.last_name:
                    user_name += f" {message.from_user.last_name}"
                username = message.from_user.username or ""
                user_id = message.from_user.id
            else:
                user_name = "Unknown"
                username = ""
                user_id = 0

            msg_data = {
                'user_id': user_id,
                'user_name': user_name,
                'username': username,
                'text': message.text,
                'date': message.date,
                'message_id': message.id
            }

            message_store[chat_id].append(msg_data)

            if len(message_store[chat_id]) > MAX_MESSAGES_PER_CHAT:
                message_store[chat_id] = message_store[chat_id][-MAX_MESSAGES_PER_CHAT:]

            chat_title = message.chat.title if message.chat.title else "Личка"
            logger.info(f"[{chat_title}] {user_name}: {message.text[:30]}...")

        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")

    @bot_instance.on_message(filters.command("start"))
    async def start_command(client, message: Message):
        await message.reply(
            "👋 Привет! Я **MonitorChatBot** — анализатор чатов с AI\n\n"
            "**Команды:**\n"
            "/stats - статистика по последним 100 сообщениям\n"
            "/digest - AI-дайджест за сегодня\n"
            "/last @username - сообщения пользователя\n"
            "/help - помощь"
        )

    @bot_instance.on_message(filters.command("help"))
    async def help_command(client, message: Message):
        help_text = """
🤖 **MonitorChatBot — Команды**

**AI-аналитика:**
• `/digest` - резюме дня с указанием авторов

**Статистика:**
• `/stats` - статистика по последним 100 сообщениям
• `/last @username` - последние сообщения пользователя

**Управление:**
• `/clear` - очистить сохраненные сообщения
• `/status` - статус бота
• `/help` - это сообщение
        """
        await message.reply(help_text)

    @bot_instance.on_message(filters.command("digest"))
    async def digest_command(client, message: Message):
        chat_id = message.chat.id
        await client.send_chat_action(chat_id, enums.ChatAction.TYPING)

        if chat_id not in message_store or not message_store[chat_id]:
            await message.reply("❌ Нет сохраненных сообщений")
            return

        today = date.today()
        today_messages = [
            msg for msg in message_store[chat_id]
            if msg['date'].date() == today
        ]

        if len(today_messages) < 5:
            await message.reply("⚠️ Мало сообщений за сегодня (меньше 5)")
            return

        messages_text = "\n".join([
            f"[{msg['user_name']}]: {msg['text']}"
            for msg in today_messages[-50:]
        ])

        prompt = f"""Проанализируй сообщения за сегодня.
        
        Формат JSON:
        {{
            "summary": "общее резюме",
            "topics": [
                {{
                    "topic": "тема",
                    "participants": ["Имя1", "Имя2"],
                    "key_points": "основные мысли"
                }}
            ]
        }}
        
        Сообщения:
        {messages_text}
        """

        try:
            response = deepseek_client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": "Ты аналитик чатов. Отвечай только JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=1000,
                response_format={"type": "json_object"}
            )

            result = json.loads(response.choices[0].message.content)

            digest_text = f"📅 **Дайджест за {today.strftime('%d.%m.%Y')}**\n\n"
            digest_text += f"📝 **Резюме:**\n{result['summary']}\n\n"
            digest_text += "🔍 **Темы:**\n"

            for i, topic in enumerate(result['topics'], 1):
                digest_text += f"\n{i}. **{topic['topic']}**\n"
                digest_text += f"   👥 {', '.join(topic['participants'])}\n"
                digest_text += f"   💭 {topic['key_points']}\n"

            digest_text += f"\n📊 Всего: {len(today_messages)}"
            await message.reply(digest_text)

        except Exception as e:
            logger.error(f"Ошибка DeepSeek: {e}")
            await message.reply("❌ Ошибка AI")

    @bot_instance.on_message(filters.command("stats"))
    async def stats_command(client, message: Message):
        chat_id = message.chat.id
        await client.send_chat_action(chat_id, enums.ChatAction.TYPING)

        if chat_id not in message_store or not message_store[chat_id]:
            await message.reply("❌ Нет сообщений")
            return

        recent = message_store[chat_id][-100:]
        user_stats = defaultdict(int)

        for msg in recent:
            user_stats[msg['user_name']] += 1

        sorted_users = sorted(user_stats.items(), key=lambda x: x[1], reverse=True)

        report = f"📊 **Последние {len(recent)} сообщений**\n\n👥 **Статистика:**\n"
        for user, count in sorted_users:
            report += f"• {user}: {count}\n"

        report += "\n💬 **Последние 10:**\n"
        for msg in recent[-10:]:
            time = msg['date'].strftime("%H:%M")
            text = msg['text'][:50] + ("..." if len(msg['text']) > 50 else "")
            report += f"[{time}] {msg['user_name']}: {text}\n"

        await message.reply(report)

    @bot_instance.on_message(filters.command("last"))
    async def last_command(client, message: Message):
        if len(message.command) < 2:
            await message.reply("❌ Укажи: `/last @username`")
            return

        username = message.command[1].replace('@', '')
        chat_id = message.chat.id

        if chat_id not in message_store:
            await message.reply("❌ Нет сообщений")
            return

        user_msgs = [
            msg for msg in message_store[chat_id]
            if msg['username'] and username.lower() in msg['username'].lower()
        ]

        if not user_msgs:
            await message.reply(f"❌ Нет сообщений от @{username}")
            return

        recent = user_msgs[-10:]
        report = f"📝 **@{username}** (последние {len(recent)}):\n\n"
        for msg in recent:
            time = msg['date'].strftime("%H:%M")
            text = msg['text'][:50] + ("..." if len(msg['text']) > 50 else "")
            report += f"[{time}] {text}\n"

        await message.reply(report)

    @bot_instance.on_message(filters.command("clear"))
    async def clear_command(client, message: Message):
        chat_id = message.chat.id
        if chat_id in message_store:
            count = len(message_store[chat_id])
            message_store[chat_id] = []
            await message.reply(f"✅ Очищено {count} сообщений")
        else:
            await message.reply("❌ Нет сообщений")

    @bot_instance.on_message(filters.command("status"))
    async def status_command(client, message: Message):
        chat_id = message.chat.id
        today_count = len([m for m in message_store.get(chat_id, [])
                           if m['date'].date() == date.today()])

        status = f"""
📊 **Статус**
• Сегодня: {today_count}
• Всего: {len(message_store.get(chat_id, []))}
• Чатов: {len(message_store)}
• AI: {'✅' if DEEPSEEK_API_KEY else '❌'}
        """
        await message.reply(status)

    # ========== ЗАПУСК ==========
    async def start_and_set_webhook():
        await bot_instance.start()
        # Устанавливаем вебхук
        webhook_url = "https://monitorchatbot.onrender.com/"
        await bot_instance.set_webhook(webhook_url)
        print(f"✅ Вебхук установлен на {webhook_url}")
        print("✅ Бот запущен и слушает сообщения через вебхук")

        # Держим бота живым
        while True:
            await asyncio.sleep(60)

    loop_instance.run_until_complete(start_and_set_webhook())

# ============================================
# ТОЧКА ВХОДА
# ============================================
if __name__ == "__main__":
    print("🚀 MonitorChatBot на Render (вебхук режим)")
    print(f"🤖 DeepSeek: {'✅' if DEEPSEEK_API_KEY else '❌'}")

    # Запускаем бота в отдельном потоке
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()

    # Запускаем Flask сервер
    port = int(os.environ.get("PORT", 10000))
    webhook_app.run(host='0.0.0.0', port=port)