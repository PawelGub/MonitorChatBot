import os
import json
import logging
from collections import defaultdict
from datetime import datetime, date
from dotenv import load_dotenv
import openai
from flask import Flask, request
import requests

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загружаем переменные окружения
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')  # Может быть пустым для бесплатного режима

# Настройка OpenRouter клиента (бесплатный)
openrouter_client = openai.OpenAI(
    api_key=OPENROUTER_API_KEY or "not-needed",  # OpenRouter позволяет без ключа для free моделей [citation:6]
    base_url="https://openrouter.ai/api/v1",
    default_headers={
        "HTTP-Referer": "https://monitorchatbot.onrender.com",  # Твой сайт
        "X-Title": "MonitorChatBot"  # Название проекта
    }
)

# Хранилище сообщений и кэша дайджестов
message_store = defaultdict(list)
digest_cache = {}  # {chat_id: {"last_msg_id": int, "digest": str, "date": date}}

# Функция для отправки ответов в Telegram
def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'Markdown'
    })

# Функция для вызова OpenRouter с бесплатными моделями [citation:6]
def call_free_ai(prompt, system_prompt="Ты полезный ассистент."):
    try:
        response = openrouter_client.chat.completions.create(
            model="deepseek/deepseek-chat:free",  # Бесплатная модель
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=1000
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Ошибка OpenRouter: {e}")
        return None

# Функция для парсинга JSON из ответа
def parse_json_response(content):
    if not content:
        return None
    try:
        # Очищаем от markdown-оберток
        content = content.strip()
        if content.startswith('```json'):
            content = content[7:]
        elif content.startswith('```'):
            content = content[3:]
        if content.endswith('```'):
            content = content[:-3]
        content = content.strip()

        return json.loads(content)
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON: {e}, контент: {content[:200]}")
        return None

# Создаем Flask приложение
app = Flask(__name__)

@app.route('/', methods=['POST'])
def webhook():
    """Принимаем обновления от Telegram"""
    try:
        update = request.get_json()

        if 'message' in update:
            msg = update['message']
            chat_id = msg['chat']['id']
            text = msg.get('text', '')
            user = msg['from']
            user_name = user.get('first_name', '')
            username = user.get('username', '')
            message_id = msg['message_id']  # Важно для кэша

            # Сохраняем сообщение
            msg_data = {
                'message_id': message_id,
                'user_name': user_name,
                'username': username,
                'text': text,
                'date': datetime.now()
            }
            message_store[chat_id].append(msg_data)

            # Ограничиваем размер хранилища (последние 2000 сообщений)
            if len(message_store[chat_id]) > 2000:
                message_store[chat_id] = message_store[chat_id][-2000:]

            # Обрабатываем команды
            if text == '/start':
                send_message(chat_id, "👋 Привет! Я MonitorChatBot с **бесплатным AI**\n\nКоманды:\n/stats - статистика\n/digest - умный дайджест за сегодня (с кэшем)\n/clearcache - сбросить кэш дайджеста\n/help - помощь")

            elif text == '/help':
                help_text = """
🤖 **MonitorChatBot — Бесплатный AI**

• `/stats` - статистика по последним 100 сообщениям
• `/digest` - **умный дайджест** (кэшируется, обновляется только новыми сообщениями)
• `/clearcache` - сбросить кэш дайджеста (если хочешь перегенерировать с нуля)
• `/status` - статус бота

**AI:** OpenRouter (бесплатные модели DeepSeek)
**Кэш:** дайджест хранится и обновляется только новыми сообщениями
                """
                send_message(chat_id, help_text)

            elif text == '/status':
                today_count = len([m for m in message_store[chat_id] if m['date'].date() == date.today()])
                cached = chat_id in digest_cache and digest_cache[chat_id]['date'] == date.today()
                status = f"📊 **Статус**\n• Сообщений сегодня: {today_count}\n• Всего сохранено: {len(message_store[chat_id])}\n• Кэш дайджеста: {'✅' if cached else '❌'}\n• AI: бесплатный OpenRouter"
                send_message(chat_id, status)

            elif text == '/clearcache':
                if chat_id in digest_cache:
                    del digest_cache[chat_id]
                    send_message(chat_id, "✅ Кэш дайджеста очищен")
                else:
                    send_message(chat_id, "❌ Кэша не было")

            elif text == '/stats':
                recent = message_store[chat_id][-100:]
                if not recent:
                    send_message(chat_id, "❌ Нет сообщений для анализа")
                else:
                    user_stats = defaultdict(int)
                    for m in recent:
                        user_stats[m['user_name']] += 1

                    sorted_users = sorted(user_stats.items(), key=lambda x: x[1], reverse=True)

                    report = f"📊 **Последние {len(recent)} сообщений**\n\n👥 **Статистика:**\n"
                    for user, count in sorted_users[:10]:
                        report += f"• {user}: {count}\n"

                    send_message(chat_id, report)

            elif text == '/digest':
                # Отправляем "печатает..."
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendChatAction",
                              json={'chat_id': chat_id, 'action': 'typing'})

                today = date.today()
                today_msgs = [m for m in message_store[chat_id] if m['date'].date() == today]

                if len(today_msgs) < 5:
                    send_message(chat_id, "⚠️ Слишком мало сообщений за сегодня (меньше 5)")
                    return 'OK', 200

                # ===== УМНОЕ КЭШИРОВАНИЕ =====
                last_msg_id = today_msgs[-1]['message_id']
                cached = digest_cache.get(chat_id)

                # Если есть кэш и он за сегодня
                if cached and cached['date'] == today:
                    # Находим новые сообщения (после последнего обработанного)
                    new_msgs = [m for m in today_msgs if m['message_id'] > cached['last_msg_id']]

                    if not new_msgs:
                        # Нет новых сообщений - возвращаем кэш
                        send_message(chat_id, cached['digest'] + "\n\n_⚡ из кэша (нет новых сообщений)_")
                        return 'OK', 200

                    # Есть новые сообщения - обновляем дайджест
                    new_text = "\n".join([f"{m['user_name']}: {m['text']}" for m in new_msgs])

                    prompt = f"""У меня есть текущий дайджест дня. Обнови его с учетом новых сообщений.

Текущий дайджест:
{cached['digest']}

Новые сообщения:
{new_text}

Верни ОБНОВЛЕННУЮ версию в ТОЧНО таком же формате:
{{
    "summary": "общее резюме дня (3-5 предложений)",
    "topics": [
        {{
            "topic": "название темы",
            "participants": ["Имя1", "Имя2"],
            "key_points": "основные мысли по теме"
        }}
    ]
}}"""

                    system = "Ты аналитик чатов. Обновляй существующий дайджест, добавляя новые темы."

                else:
                    # Нет кэша - генерируем с нуля
                    messages_text = "\n".join([
                        f"{msg['user_name']}: {msg['text']}"
                        for msg in today_msgs[-50:]
                    ])

                    prompt = f"""Проанализируй сообщения из чата за сегодня.

Сообщения:
{messages_text}

Верни ТОЛЬКО JSON:
{{
    "summary": "общее резюме дня (3-5 предложений)",
    "topics": [
        {{
            "topic": "название темы",
            "participants": ["Имя1", "Имя2"],
            "key_points": "основные мысли по теме"
        }}
    ]
}}"""

                    system = "Ты аналитик чатов. Отвечай строго в JSON формате."

                # Вызываем бесплатный AI
                response_content = call_free_ai(prompt, system)

                if not response_content:
                    send_message(chat_id, "❌ Ошибка при обращении к AI. Попробуй позже.")
                    return 'OK', 200

                # Парсим JSON
                result = parse_json_response(response_content)

                if not result:
                    send_message(chat_id, f"❌ Ошибка при обработке ответа AI. Попробуй позже.\n\nОтвет: {response_content[:200]}...")
                    return 'OK', 200

                # Форматируем ответ
                digest = f"📅 **Дайджест за {today.strftime('%d.%m.%Y')}**\n\n"
                digest += f"📝 **Резюме:**\n{result.get('summary', 'Нет резюме')}\n\n"
                digest += "🔍 **Ключевые темы:**\n"

                for i, topic in enumerate(result.get('topics', []), 1):
                    digest += f"\n{i}. **{topic.get('topic', 'Без темы')}**\n"
                    digest += f"   👥 Участники: {', '.join(topic.get('participants', ['-']))}\n"
                    digest += f"   💭 {topic.get('key_points', '')}\n"

                digest += f"\n📊 Проанализировано сообщений: {len(today_msgs)}"

                # Сохраняем в кэш
                digest_cache[chat_id] = {
                    'last_msg_id': last_msg_id,
                    'digest': digest,
                    'date': today
                }

                send_message(chat_id, digest)

            elif text.startswith('/'):
                send_message(chat_id, "❌ Неизвестная команда. Напиши /help")

        return 'OK', 200

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return 'OK', 200

@app.route('/health')
def health():
    return 'OK', 200

@app.route('/')
def home():
    return 'Bot is running with FREE AI!', 200

# Функция для установки вебхука
def set_webhook():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    webhook_url = "https://monitorchatbot.onrender.com/"
    response = requests.post(url, json={'url': webhook_url})
    if response.json().get('ok'):
        print(f"✅ Вебхук установлен на {webhook_url}")
    else:
        print(f"❌ Ошибка установки вебхука: {response.json()}")

if __name__ == "__main__":
    print("🚀 MonitorChatBot с БЕСПЛАТНЫМ AI и кэшем запускается...")
    print("🤖 AI: OpenRouter (бесплатные модели DeepSeek) [citation:6]")

    # Устанавливаем вебхук
    set_webhook()

    # Запускаем Flask сервер
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)