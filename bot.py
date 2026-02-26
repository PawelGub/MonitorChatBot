import os
import json
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, date
from dotenv import load_dotenv
from pyrogram import Client, filters, enums
from pyrogram.types import Message
import openai

import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())
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

# Создаем клиента для бота
app = Client(
    "monitor_chat_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Хранилище сообщений по чатам
message_store = defaultdict(list)
MAX_MESSAGES_PER_CHAT = 5000

@app.on_message(filters.text & ~filters.bot)
async def store_message(client, message: Message):
    """Сохраняем каждое входящее текстовое сообщение"""
    try:
        chat_id = message.chat.id

        # Получаем информацию об отправителе
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

        # Формируем запись
        msg_data = {
            'user_id': user_id,
            'user_name': user_name,
            'username': username,
            'text': message.text,
            'date': message.date,
            'message_id': message.id
        }

        message_store[chat_id].append(msg_data)

        # Ограничиваем размер хранилища
        if len(message_store[chat_id]) > MAX_MESSAGES_PER_CHAT:
            message_store[chat_id] = message_store[chat_id][-MAX_MESSAGES_PER_CHAT:]

        # Логируем
        chat_title = message.chat.title if message.chat.title else "Личка"
        logger.info(f"[{chat_title}] {user_name}: {message.text[:30]}...")

    except Exception as e:
        logger.error(f"Ошибка при сохранении сообщения: {e}")

@app.on_message(filters.command("start"))
async def start_command(client, message: Message):
    await message.reply(
        "👋 Привет! Я **MonitorChatBot** — анализатор чатов с AI\n\n"
        "**Команды:**\n"
        "/stats - статистика по последним 100 сообщениям\n"
        "/digest - AI-дайджест за сегодняшний день\n"
        "/last @username - последние сообщения пользователя\n"
        "/help - помощь"
    )

@app.on_message(filters.command("help"))
async def help_command(client, message: Message):
    help_text = """
🤖 **MonitorChatBot — Команды**

**AI-аналитика:**
• `/digest` - DeepSeek делает краткое резюме всех сообщений за сегодня с указанием авторов ключевых мыслей

**Статистика:**
• `/stats` - статистика по последним 100 сообщениям
• `/last @username` - последние сообщения пользователя

**Управление:**
• `/clear` - очистить сохраненные сообщения в этом чате
• `/status` - статус бота
• `/help` - это сообщение

**Как работает:** Я сохраняю все сообщения и использую DeepSeek AI для анализа.
    """
    await message.reply(help_text)

@app.on_message(filters.command("digest"))
async def digest_command(client, message: Message):
    """Команда /digest - AI-дайджест за сегодня"""
    chat_id = message.chat.id

    # Отправляем "печатает..."
    await client.send_chat_action(chat_id, enums.ChatAction.TYPING)

    # Проверяем наличие сообщений
    if chat_id not in message_store or not message_store[chat_id]:
        await message.reply("❌ Нет сохраненных сообщений. Я только начал слушать этот чат!")
        return

    # Получаем сегодняшние сообщения
    today = date.today()
    today_messages = [
        msg for msg in message_store[chat_id]
        if msg['date'].date() == today
    ]

    if len(today_messages) < 5:
        await message.reply("⚠️ Слишком мало сообщений за сегодня (меньше 5). Нужно больше для анализа.")
        return

    # Формируем промпт для DeepSeek
    messages_text = "\n".join([
        f"[{msg['user_name']}]: {msg['text']}"
        for msg in today_messages[-50:]  # Берем последние 50 за день
    ])

    prompt = f"""Проанализируй следующие сообщения из Telegram-чата за сегодняшний день.
    
    Твоя задача: 
    1. Выдели 3-5 ключевых тем обсуждения
    2. Для каждой темы укажи, кто из участников высказывал ключевые мысли (используй имена в квадратных скобках)
    3. Напиши краткое резюме (3-5 предложений) о чем был день
    
    Формат ответа (строго соблюдай JSON):
    {{
        "summary": "общее резюме дня",
        "topics": [
            {{
                "topic": "название темы",
                "participants": ["Имя1", "Имя2"],
                "key_points": "основные мысли по теме"
            }}
        ]
    }}
    
    Сообщения:
    {messages_text}
    """

    try:
        # Запрос к DeepSeek
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "Ты аналитик чатов. Отвечай только в JSON формате."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=1000,
            response_format={"type": "json_object"}
        )

        result = json.loads(response.choices[0].message.content)

        # Форматируем ответ
        digest_text = f"📅 **Дайджест за {today.strftime('%d.%m.%Y')}**\n\n"
        digest_text += f"📝 **Общее резюме:**\n{result['summary']}\n\n"
        digest_text += "🔍 **Ключевые темы:**\n"

        for i, topic in enumerate(result['topics'], 1):
            digest_text += f"\n{i}. **{topic['topic']}**\n"
            digest_text += f"   👥 Участники: {', '.join(topic['participants'])}\n"
            digest_text += f"   💭 {topic['key_points']}\n"

        digest_text += f"\n📊 Всего сообщений за сегодня: {len(today_messages)}"

        await message.reply(digest_text)

    except Exception as e:
        logger.error(f"Ошибка DeepSeek API: {e}")
        await message.reply("❌ Ошибка при обращении к AI. Попробуй позже.")

@app.on_message(filters.command("stats"))
async def stats_command(client, message: Message):
    """Команда /stats - статистика по последним 100 сообщениям"""
    chat_id = message.chat.id

    try:
        await client.send_chat_action(chat_id, enums.ChatAction.TYPING)
    except:
        pass

    if chat_id not in message_store or not message_store[chat_id]:
        await message.reply("❌ Нет сохраненных сообщений")
        return

    recent = message_store[chat_id][-100:]

    # Считаем статистику
    user_stats = defaultdict(int)
    for msg in recent:
        user_stats[msg['user_name']] += 1

    sorted_users = sorted(user_stats.items(), key=lambda x: x[1], reverse=True)

    report = f"📊 **Анализ последних {len(recent)} сообщений**\n\n"
    report += "👥 **Статистика:**\n"

    for user, count in sorted_users:
        report += f"• {user} — {count} сообщ.\n"

    report += "\n💬 **Последние 10 сообщений:**\n"
    for msg in recent[-10:]:
        time = msg['date'].strftime("%H:%M")
        text = msg['text'][:50] + "..." if len(msg['text']) > 50 else msg['text']
        report += f"[{time}] {msg['user_name']}: {text}\n"

    await message.reply(report)

@app.on_message(filters.command("last"))
async def last_user_messages(client, message: Message):
    """Команда /last @username"""
    if len(message.command) < 2:
        await message.reply("❌ Укажи пользователя: `/last @username`")
        return

    username = message.command[1].replace('@', '')
    chat_id = message.chat.id

    if chat_id not in message_store:
        await message.reply("❌ Нет сохраненных сообщений")
        return

    user_messages = [
        msg for msg in message_store[chat_id]
        if msg['username'] and username.lower() in msg['username'].lower()
    ]

    if not user_messages:
        await message.reply(f"❌ Нет сообщений от @{username}")
        return

    recent = user_messages[-10:]
    report = f"📝 **Последние сообщения от @{username}**\n\n"
    for msg in recent:
        time = msg['date'].strftime("%H:%M")
        text = msg['text'][:50] + "..." if len(msg['text']) > 50 else msg['text']
        report += f"[{time}] {text}\n"

    await message.reply(report)

@app.on_message(filters.command("clear"))
async def clear_messages(client, message: Message):
    chat_id = message.chat.id
    if chat_id in message_store:
        count = len(message_store[chat_id])
        message_store[chat_id] = []
        await message.reply(f"✅ Очищено {count} сообщений")
    else:
        await message.reply("❌ Нет сохраненных сообщений")

@app.on_message(filters.command("status"))
async def status_command(client, message: Message):
    chat_id = message.chat.id
    today_count = len([msg for msg in message_store.get(chat_id, []) if msg['date'].date() == date.today()])

    status_text = f"""
📊 **Статус бота**

**Текущий чат:**
• ID: `{chat_id}`
• Название: {message.chat.title or "Личные сообщения"}

**Статистика:**
• Сообщений сегодня: {today_count}
• Всего сохранено: {len(message_store.get(chat_id, []))}
• Чатов в памяти: {len(message_store)}
• Максимум на чат: {MAX_MESSAGES_PER_CHAT}

**AI:** {'✅ DeepSeek подключен' if DEEPSEEK_API_KEY else '❌ Нет ключа DeepSeek'}
**Бот:** @MonitorChatBot
    """
    await message.reply(status_text)

if __name__ == "__main__":
    import asyncio
    import os
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler

    print("🚀 MonitorChatBot с DeepSeek запускается на Render...")
    print(f"📝 Бот: @MonitorChatBot")
    print(f"🤖 DeepSeek: {'✅ подключен' if DEEPSEEK_API_KEY else '❌ НЕТ КЛЮЧА!'}")

    # 1. СОЗДАЁМ НОВЫЙ ЦИКЛ СОБЫТИЙ и делаем его текущим
    # Это критически важно для Pyrogram в такой среде!
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # 2. Функция для запуска бота в отдельном потоке, используя наш цикл
    def run_bot():
        # Устанавливаем созданный цикл для этого потока
        asyncio.set_event_loop(loop)
        # Запускаем клиента Pyrogram
        app.run()

    # 3. Запускаем бота в фоновом потоке
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # 4. Запускаем простой HTTP-сервер для health checks от Render
    # Render требует, чтобы сервис слушал порт, иначе он будет думать, что приложение не запустилось
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running")

    # Render сам назначает порт через переменную окружения PORT
    port = int(os.environ.get("PORT", 10000))
    server_address = ('0.0.0.0', port)
    httpd = HTTPServer(server_address, HealthCheckHandler)
    print(f"✅ Health check server listening on port {port}")

    # 5. Запускаем HTTP-сервер в основном потоке
    # Это не даст скрипту завершиться
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("🛑 Shutting down...")