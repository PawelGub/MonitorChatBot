import os
import json
import logging
from collections import defaultdict
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
import openai
from flask import Flask, request
import requests
import traceback

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Загружаем переменные окружения
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')

if not BOT_TOKEN:
    logger.error("BOT_TOKEN не найден!")
if not OPENROUTER_API_KEY:
    logger.error("OPENROUTER_API_KEY не найден!")

# Настройка OpenRouter клиента
try:
    openrouter_client = openai.OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": "https://monitorchatbot.onrender.com",
            "X-Title": "MonitorChatBot"
        }
    )
    logger.info("OpenRouter клиент создан")
except Exception as e:
    logger.error(f"Ошибка создания OpenRouter клиента: {e}")
    openrouter_client = None

# Хранилище сообщений и кэша
message_store = defaultdict(list)
digest_cache = {}

FREE_MODEL = "arcee-ai/trinity-large-preview:free"
MAX_MESSAGES = 5000
KEEP_DAYS = 2

def clean_old_messages():
    """Удаляем сообщения старше KEEP_DAYS дней"""
    try:
        cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
        for chat_id in list(message_store.keys()):
            message_store[chat_id] = [msg for msg in message_store[chat_id] if msg['date'] > cutoff]
            if not message_store[chat_id]:
                del message_store[chat_id]
    except Exception as e:
        logger.error(f"Ошибка очистки сообщений: {e}")

def send_message(chat_id, text, reply_to=None):
    """Отправка сообщения в Telegram"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'Markdown'
        }
        if reply_to:
            payload['reply_to_message_id'] = reply_to
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            logger.error(f"Ошибка отправки сообщения: {response.text}")
        return response
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения: {e}")
        return None

def get_message_link(chat_id, message_id):
    """Создает ссылку на сообщение в Telegram"""
    try:
        chat_id_str = str(chat_id)
        if chat_id_str.startswith('-100'):
            chat_id_str = chat_id_str[4:]
        return f"https://t.me/c/{chat_id_str}/{message_id}"
    except Exception as e:
        logger.error(f"Ошибка создания ссылки: {e}")
        return ""

def call_free_ai(prompt, system_prompt="Ты полезный ассистент."):
    """Вызов бесплатной AI модели"""
    if not openrouter_client:
        logger.error("OpenRouter клиент не инициализирован")
        return None

    try:
        response = openrouter_client.chat.completions.create(
            model=FREE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=1500,
            timeout=30
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
        logger.error(f"Контент: {content[:500]}")
        return None

# Flask приложение
app = Flask(__name__)

@app.route('/', methods=['POST'])
def webhook():
    """Обработчик вебхука"""
    try:
        update = request.get_json()
        logger.info(f"Получен вебхук: {update.get('message', {}).get('text', '')[:50] if update else 'empty'}")

        if not update or 'message' not in update:
            return 'OK', 200

        msg = update['message']
        chat_id = msg['chat']['id']
        text = msg.get('text', '')
        user = msg.get('from', {})
        user_name = user.get('first_name', 'Unknown')
        username = user.get('username', '')
        message_id = msg.get('message_id', 0)

        # Сохраняем сообщение (только текстовые)
        if text:
            msg_data = {
                'message_id': message_id,
                'user_name': user_name,
                'username': username,
                'text': text,
                'date': datetime.now()
            }
            message_store[chat_id].append(msg_data)
            logger.info(f"[{chat_id}] {user_name}: {text[:50]}")

        # Очищаем старые сообщения
        clean_old_messages()

        if len(message_store[chat_id]) > MAX_MESSAGES:
            message_store[chat_id] = message_store[chat_id][-MAX_MESSAGES:]

        # Обработка команд
        if text.startswith('/'):
            logger.info(f"Команда: {text}")

            if text == '/start':
                send_message(chat_id, "👋 Привет! Я **MonitorChatBot** — анализатор чатов с AI\n\n"
                                      "**Команды:**\n"
                                      "/stats - статистика\n"
                                      "/digest - расширенный дайджест с ссылками\n"
                                      "/clearcache - сбросить кэш\n"
                                      "/help - помощь")

            elif text == '/help':
                help_text = """
🤖 **MonitorChatBot — Команды**

• `/stats` - статистика по последним 100 сообщениям
• `/digest` - **расширенный дайджест** за последние 2 дня с ссылками
• `/clearcache` - сбросить кэш дайджеста
• `/status` - статус бота

**AI модель:** Trinity Large Preview (бесплатно)
**Хранение:** 2 дня сообщений
                """
                send_message(chat_id, help_text)

            elif text == '/status':
                today_count = len([m for m in message_store[chat_id] if m['date'].date() == date.today()])
                two_days_ago = date.today() - timedelta(days=KEEP_DAYS)
                two_day_count = len([m for m in message_store[chat_id] if m['date'].date() >= two_days_ago])
                cached = chat_id in digest_cache and digest_cache[chat_id].get('date') == date.today()
                status = f"📊 **Статус**\n" \
                         f"• Сообщений сегодня: {today_count}\n" \
                         f"• За {KEEP_DAYS} дня: {two_day_count}\n" \
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
                try:
                    # Отправляем "печатает..."
                    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendChatAction",
                                  json={'chat_id': chat_id, 'action': 'typing'}, timeout=5)

                    # Берем сообщения за последние 2 дня
                    cutoff_date = date.today() - timedelta(days=KEEP_DAYS)
                    relevant_msgs = [m for m in message_store[chat_id] if m['date'].date() >= cutoff_date]

                    if len(relevant_msgs) < 3:
                        send_message(chat_id, f"⚠️ Слишком мало сообщений за последние {KEEP_DAYS} дня (меньше 3)")
                        return 'OK', 200

                    last_msg_id = relevant_msgs[-1]['message_id']

                    # Формируем сообщения для AI
                    messages_with_ids = []
                    for msg in relevant_msgs[-60:]:  # Последние 60 сообщений
                        link = get_message_link(chat_id, msg['message_id'])
                        messages_with_ids.append({
                            'id': msg['message_id'],
                            'user': msg['user_name'],
                            'text': msg['text'],
                            'time': msg['date'].strftime("%H:%M"),
                            'link': link
                        })

                    real_names = {msg['user_name'] for msg in relevant_msgs}
                    names_list = ', '.join(real_names)

                    # Формируем текст для AI
                    messages_text = "\n".join([
                        f"[{msg['user']}]({msg['link']}) [{msg['time']}]: {msg['text'][:200]}"
                        for msg in messages_with_ids
                    ])

                    prompt = f"""Проанализируй сообщения в чате за последние {KEEP_DAYS} дня.

Участники: {names_list}

Сообщения (с ссылками):
{messages_text}

Верни JSON:
{{
    "summary": "общее резюме за период (что обсуждали, ключевые темы)",
    "timeline": [
        {{
            "topic": "название темы/разговора",
            "start_message_link": "ссылка на первое сообщение темы",
            "participants": ["Имя1", "Имя2"],
            "summary": "содержание обсуждения",
            "key_messages": [
                {{
                    "text": "ключевая цитата",
                    "author": "Имя",
                    "link": "ссылка на сообщение"
                }}
            ]
        }}
    ]
}}

Правила:
- start_message_link — скопируй ссылку из первого сообщения темы
- Участники ТОЛЬКО из списка: {names_list}
- Не выдумывай участников, только реальные"""

                    system = "Ты аналитик чата. Отвечай JSON. Только реальные имена."

                    # Вызываем AI
                    response_content = call_free_ai(prompt, system)

                    if not response_content:
                        send_message(chat_id, "❌ Ошибка при обращении к AI. Попробуй позже.")
                        return 'OK', 200

                    result = parse_json_response(response_content)

                    if not result:
                        send_message(chat_id, "❌ Ошибка при обработке ответа AI. Попробуй позже.")
                        return 'OK', 200

                    # Форматируем дайджест
                    start_date = cutoff_date.strftime('%d.%m.%Y')
                    end_date = date.today().strftime('%d.%m.%Y')
                    date_range = f"{start_date} — {end_date}" if start_date != end_date else start_date

                    digest = f"📅 **Дайджест за {date_range}**\n\n"
                    digest += f"📝 **Резюме:**\n{result.get('summary', 'Нет резюме')[:500]}\n\n"
                    digest += "📌 **Обсуждения:**\n"

                    for i, topic in enumerate(result.get('timeline', [])[:5], 1):
                        digest += f"\n**{i}. {topic.get('topic', 'Тема')[:50]}**\n"

                        start_link = topic.get('start_message_link', '')
                        if start_link:
                            digest += f"   🔗 [Начало]({start_link})\n"

                        participants = topic.get('participants', [])
                        if participants:
                            digest += f"   👥 {', '.join(participants[:5])}\n"

                        summary = topic.get('summary', '')[:150]
                        if summary:
                            digest += f"   📖 {summary}\n"

                        key_msgs = topic.get('key_messages', [])
                        if key_msgs:
                            digest += f"   💬 **Цитаты:**\n"
                            for km in key_msgs[:2]:
                                text = km.get('text', '')[:80]
                                author = km.get('author', '')
                                link = km.get('link', '')
                                if link:
                                    digest += f"      • [{author}]({link}): {text}\n"
                                else:
                                    digest += f"      • {author}: {text}\n"

                    digest += f"\n📊 Сообщений: {len(relevant_msgs)}"

                    # Сохраняем кэш
                    digest_cache[chat_id] = {
                        'last_msg_id': last_msg_id,
                        'digest': digest,
                        'date': date.today()
                    }

                    send_message(chat_id, digest)

                except Exception as e:
                    logger.error(f"Ошибка в /digest: {traceback.format_exc()}")
                    send_message(chat_id, f"❌ Ошибка: {str(e)[:100]}")

            elif text.startswith('/'):
                send_message(chat_id, "❌ Неизвестная команда. Напиши /help")

        return 'OK', 200

    except Exception as e:
        logger.error(f"Общая ошибка: {traceback.format_exc()}")
        return 'OK', 200

@app.route('/health')
def health():
    return 'OK', 200

@app.route('/')
def home():
    return f'Bot is running with {FREE_MODEL}! Stores {KEEP_DAYS} days.', 200

def set_webhook():
    """Установка вебхука"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
        webhook_url = "https://monitorchatbot.onrender.com/"
        response = requests.post(url, json={'url': webhook_url}, timeout=10)
        if response.status_code == 200 and response.json().get('ok'):
            print(f"✅ Вебхук установлен на {webhook_url}")
        else:
            print(f"❌ Ошибка установки вебхука: {response.text}")
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    print(f"🚀 MonitorChatBot с {FREE_MODEL} запускается...")
    print(f"📆 Храним сообщения: {KEEP_DAYS} дня")
    print(f"🔗 Дайджест включает ссылки на сообщения")
    print(f"🤖 DeepSeek API ключ: {'✅' if OPENROUTER_API_KEY else '❌'}")
    print(f"🤖 BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")

    set_webhook()

    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)