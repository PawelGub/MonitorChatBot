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
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')

# Настройка OpenRouter клиента
openrouter_client = openai.OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
    default_headers={
        "HTTP-Referer": "https://monitorchatbot.onrender.com",
        "X-Title": "MonitorChatBot"
    }
)

# Хранилище сообщений и кэша
message_store = defaultdict(list)
digest_cache = {}  # {chat_id: {"last_msg_id": int, "digest": str, "date": date}}

# 👇 ПОБЕДИТЕЛЬ - лучшая бесплатная модель
FREE_MODEL = "arcee-ai/trinity-large-preview:free"
MAX_MESSAGES = 2000

def send_message(chat_id, text):
    """Отправка сообщения в Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'Markdown'
    })

def call_free_ai(prompt, system_prompt="Ты полезный ассистент."):
    """Вызов бесплатной AI модели"""
    try:
        response = openrouter_client.chat.completions.create(
            model=FREE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,  # Понижаем для более структурированных ответов
            max_tokens=1000
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Ошибка AI: {e}")
        return None

def parse_json_response(content):
    """Парсинг JSON из ответа AI"""
    if not content:
        return None
    try:
        # Очищаем от возможных markdown-оберток
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
        logger.error(f"Ошибка парсинга JSON: {e}")
        return None

# Flask приложение
app = Flask(__name__)

@app.route('/', methods=['POST'])
def webhook():
    """Обработчик вебхука"""
    try:
        update = request.get_json()

        if 'message' in update:
            msg = update['message']
            chat_id = msg['chat']['id']
            text = msg.get('text', '')
            user = msg['from']
            user_name = user.get('first_name', '')
            username = user.get('username', '')
            message_id = msg['message_id']

            # Сохраняем сообщение
            msg_data = {
                'message_id': message_id,
                'user_name': user_name,
                'username': username,
                'text': text,
                'date': datetime.now()
            }
            message_store[chat_id].append(msg_data)

            if len(message_store[chat_id]) > MAX_MESSAGES:
                message_store[chat_id] = message_store[chat_id][-MAX_MESSAGES:]

            # Обработка команд
            if text == '/start':
                send_message(chat_id, "👋 Привет! Я MonitorChatBot с **бесплатным AI**\n\n"
                                      "Команды:\n"
                                      "/stats - статистика\n"
                                      "/digest - дайджест за сегодня\n"
                                      "/clearcache - сбросить кэш\n"
                                      "/help - помощь")

            elif text == '/help':
                help_text = """
🤖 **MonitorChatBot — Команды**

• `/stats` - статистика по последним 100 сообщениям
• `/digest` - **дайджест за сегодня** (с кэшированием)
• `/clearcache` - сбросить кэш дайджеста
• `/status` - статус бота

**AI модель:** Trinity Large Preview (бесплатно, умная)
                """
                send_message(chat_id, help_text)

            elif text == '/status':
                today_count = len([m for m in message_store[chat_id] if m['date'].date() == date.today()])
                cached = chat_id in digest_cache and digest_cache[chat_id]['date'] == date.today()
                status = f"📊 **Статус**\n" \
                         f"• Сообщений сегодня: {today_count}\n" \
                         f"• Всего сохранено: {len(message_store[chat_id])}\n" \
                         f"• Кэш дайджеста: {'✅' if cached else '❌'}\n" \
                         f"• AI: Trinity Large (бесплатно)"
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

                if len(today_msgs) < 3:
                    send_message(chat_id, "⚠️ Слишком мало сообщений за сегодня (меньше 3)")
                    return 'OK', 200

                last_msg_id = today_msgs[-1]['message_id']
                cached = digest_cache.get(chat_id)

                # Собираем все реальные имена
                real_names = {msg['user_name'] for msg in today_msgs}
                names_list = ', '.join(real_names)

                if cached and cached['date'] == today:
                    new_msgs = [m for m in today_msgs if m['message_id'] > cached['last_msg_id']]

                    if not new_msgs:
                        send_message(chat_id, cached['digest'] + "\n\n_⚡ из кэша_")
                        return 'OK', 200

                    new_text = "\n".join([f"{m['user_name']}: {m['text']}" for m in new_msgs])

                    prompt = f"""Обнови существующий дайджест с учетом новых сообщений.

Сегодня писали: {names_list}

Текущий дайджест:
{cached['digest']}

Новые сообщения:
{new_text}

Верни ОБНОВЛЕННЫЙ JSON в том же формате:
{{
    "summary": "общее резюме дня (честно, без прикрас)",
    "topics": [
        {{
            "topic": "тема",
            "participants": ["Имя"],  # Только реальные участники из списка выше
            "key_points": "суть"
        }}
    ]
}}"""

                    system = "Ты аналитик чата. Обновляй дайджест. Только реальные имена."

                else:
                    messages_text = "\n".join([
                        f"{msg['user_name']}: {msg['text']}"
                        for msg in today_msgs[-30:]
                    ])

                    prompt = f"""Проанализируй сообщения за сегодня.

Сегодня писали: {names_list}

Сообщения:
{messages_text}

Верни JSON:
{{
    "summary": "общее резюме дня (одно предложение, честно)",
    "topics": [
        {{
            "topic": "тема 1",
            "participants": ["Имя1"],  # Только из списка {names_list}
            "key_points": "что сказано"
        }},
        {{
            "topic": "тема 2", 
            "participants": ["Имя2"],
            "key_points": "что сказано"
        }}
    ]
}}

Правила:
- Участники ТОЛЬКО из списка: {names_list}
- Если кто-то писал ерунду - отметь это
- Будь честным, но не выдумывай"""

                    system = "Ты аналитик чата. Отвечай JSON. Только реальные имена."

                response_content = call_free_ai(prompt, system)

                if not response_content:
                    send_message(chat_id, "❌ Ошибка при обращении к AI. Попробуй позже.")
                    return 'OK', 200

                result = parse_json_response(response_content)

                if not result:
                    send_message(chat_id, "❌ Ошибка при обработке ответа AI. Попробуй позже.")
                    return 'OK', 200

                # Чистим участников
                if 'topics' in result:
                    for topic in result['topics']:
                        if 'participants' in topic:
                            # Оставляем только реальные имена
                            topic['participants'] = [p for p in topic['participants'] if p in real_names]
                            if not topic['participants']:
                                topic['participants'] = list(real_names) or ["кто-то"]

                digest = f"📅 **Дайджест за {today.strftime('%d.%m.%Y')}**\n\n"
                digest += f"📝 **Резюме:**\n{result.get('summary', 'Нет резюме')}\n\n"
                digest += "🔍 **Темы:**\n"

                for i, topic in enumerate(result.get('topics', []), 1):
                    digest += f"\n{i}. **{topic.get('topic', 'Тема')}**\n"
                    digest += f"   👥 {', '.join(topic.get('participants', ['-']))}\n"
                    digest += f"   💭 {topic.get('key_points', '')}\n"

                digest += f"\n📊 Проанализировано сообщений: {len(today_msgs)}"

                digest_cache[chat_id] = {
                    'last_msg_id': last_msg_id,
                    'digest': digest,
                    'date': today
                }

                send_message(chat_id, digest)

        return 'OK', 200

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return 'OK', 200

@app.route('/health')
def health():
    return 'OK', 200

@app.route('/')
def home():
    return f'Bot is running with {FREE_MODEL}!', 200

def set_webhook():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    webhook_url = "https://monitorchatbot.onrender.com/"
    response = requests.post(url, json={'url': webhook_url})
    if response.json().get('ok'):
        print(f"✅ Вебхук установлен на {webhook_url}")
    else:
        print(f"❌ Ошибка установки вебхука: {response.json()}")

if __name__ == "__main__":
    print(f"🚀 MonitorChatBot с БЕСПЛАТНЫМ AI ({FREE_MODEL}) запускается...")
    print("🤖 Модель: Trinity Large Preview (умная, бесплатная)")

    set_webhook()

    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)