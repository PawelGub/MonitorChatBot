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

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')

# Настройка DeepSeek клиента
if DEEPSEEK_API_KEY:
    deepseek_client = openai.OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com/v1"
    )
else:
    deepseek_client = None

# Хранилище сообщений
message_store = defaultdict(list)

# Функция для отправки ответов в Telegram
def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'Markdown'
    })

# Создаем Flask приложение
app = Flask(__name__)

@app.route('/', methods=['POST'])
def webhook():
    """Принимаем обновления от Telegram"""
    try:
        update = request.get_json()
        logger.info(f"Получено обновление: {update}")

        if 'message' in update:
            msg = update['message']
            chat_id = msg['chat']['id']
            text = msg.get('text', '')
            user = msg['from']
            user_name = user.get('first_name', '')
            username = user.get('username', '')

            # Сохраняем сообщение
            msg_data = {
                'user_name': user_name,
                'username': username,
                'text': text,
                'date': datetime.now()
            }
            message_store[chat_id].append(msg_data)

            # Ограничиваем размер хранилища
            if len(message_store[chat_id]) > 1000:
                message_store[chat_id] = message_store[chat_id][-1000:]

            # Обрабатываем команды
            if text == '/start':
                send_message(chat_id, "👋 Привет! Я MonitorChatBot — анализатор чатов с AI\n\nКоманды:\n/stats - статистика\n/digest - AI-дайджест за сегодня\n/help - помощь")

            elif text == '/help':
                help_text = """
🤖 **MonitorChatBot — Команды**

• `/stats` - статистика по последним 100 сообщениям
• `/digest` - **AI-дайджест** за сегодня (DeepSeek)
• `/status` - статус бота
• `/help` - это сообщение
                """
                send_message(chat_id, help_text)

            elif text == '/status':
                today_count = len([m for m in message_store[chat_id] if m['date'].date() == date.today()])
                status = f"📊 **Статус**\n• Сообщений сегодня: {today_count}\n• Всего сохранено: {len(message_store[chat_id])}\n• AI: {'✅' if deepseek_client else '❌'}"
                send_message(chat_id, status)

            elif text == '/stats':
                recent = message_store[chat_id][-100:]
                if not recent:
                    send_message(chat_id, "❌ Нет сообщений для анализа")
                else:
                    # Считаем статистику по пользователям
                    user_stats = defaultdict(int)
                    for m in recent:
                        user_stats[m['user_name']] += 1

                    sorted_users = sorted(user_stats.items(), key=lambda x: x[1], reverse=True)

                    report = f"📊 **Последние {len(recent)} сообщений**\n\n👥 **Статистика:**\n"
                    for user, count in sorted_users[:10]:
                        report += f"• {user}: {count}\n"

                    send_message(chat_id, report)

            elif text == '/digest':
                if not deepseek_client:
                    send_message(chat_id, "❌ DeepSeek не подключен. Проверь API ключ")
                    return 'OK', 200

                # Отправляем "печатает..."
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendChatAction",
                              json={'chat_id': chat_id, 'action': 'typing'})

                # Получаем сегодняшние сообщения
                today = date.today()
                today_msgs = [m for m in message_store[chat_id] if m['date'].date() == today]

                if len(today_msgs) < 5:
                    send_message(chat_id, "⚠️ Слишком мало сообщений за сегодня (меньше 5)")
                    return 'OK', 200

                # Берем последние 50 сообщений
                recent_msgs = today_msgs[-50:]

                # Формируем текст для анализа
                messages_text = "\n".join([
                    f"[{msg['user_name']}]: {msg['text']}"
                    for msg in recent_msgs
                ])

                # Промпт для DeepSeek
                prompt = f"""Проанализируй сообщения из чата за сегодня и сделай краткое резюме.

                Твои задачи:
                1. Выдели 3-5 ключевых тем обсуждения
                2. Для каждой темы укажи, кто из участников участвовал
                3. Напиши краткое общее резюме дня

                Формат ответа (строго JSON):
                {{
                    "summary": "общее резюме дня (3-5 предложений)",
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
                    digest = f"📅 **Дайджест за {today.strftime('%d.%m.%Y')}**\n\n"
                    digest += f"📝 **Резюме:**\n{result['summary']}\n\n"
                    digest += "🔍 **Ключевые темы:**\n"

                    for i, topic in enumerate(result.get('topics', []), 1):
                        digest += f"\n{i}. **{topic['topic']}**\n"
                        digest += f"   👥 Участники: {', '.join(topic.get('participants', ['-']))}\n"
                        digest += f"   💭 {topic['key_points']}\n"

                    digest += f"\n📊 Проанализировано сообщений: {len(recent_msgs)}"

                    send_message(chat_id, digest)

                except Exception as e:
                    logger.error(f"Ошибка DeepSeek: {e}")
                    send_message(chat_id, "❌ Ошибка при обращении к AI. Попробуй позже.")

            elif text.startswith('/'):
                send_message(chat_id, "❌ Неизвестная команда. Напиши /help")

        return 'OK', 200

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return 'OK', 200

@app.route('/health')
def health():
    """Health check для Render"""
    return 'OK', 200

@app.route('/')
def home():
    return 'Bot is running', 200

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
    print("🚀 MonitorChatBot с DeepSeek запускается...")
    print(f"🤖 DeepSeek: {'✅ подключен' if DEEPSEEK_API_KEY else '❌ НЕТ API КЛЮЧА DEEPSEEK!'}")

    # Устанавливаем вебхук
    set_webhook()

    # Запускаем Flask сервер
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)