import os
import json
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, date
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
import openai
from flask import Flask, request
import threading

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

# Создаем Flask приложение
app = Flask(__name__)

@app.route('/', methods=['POST'])
def webhook():
    """Принимаем обновления от Telegram"""
    update = request.get_json()

    # Здесь будем обрабатывать сообщения
    if 'message' in update:
        chat_id = update['message']['chat']['id']
        text = update['message'].get('text', '')
        user = update['message']['from']
        user_name = user.get('first_name', '')

        # Сохраняем сообщение
        msg_data = {
            'user_name': user_name,
            'username': user.get('username', ''),
            'text': text,
            'date': datetime.now()
        }
        message_store[chat_id].append(msg_data)

        # Отвечаем на команды
        if text == '/start':
            return {'text': '👋 Привет! Я бот-анализатор чатов'}
        elif text == '/help':
            return {'text': 'Доступные команды: /stats, /digest, /last, /status'}
        elif text == '/status':
            return {'text': f'Сообщений сегодня: {len([m for m in message_store[chat_id] if m["date"].date() == date.today()])}'}

    return 'OK', 200

@app.route('/')
def health():
    return 'Bot is running', 200

# Функция для установки вебхука
def set_webhook():
    import requests
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    webhook_url = "https://monitorchatbot.onrender.com/"
    requests.post(url, json={'url': webhook_url})
    print(f"✅ Вебхук установлен на {webhook_url}")

if __name__ == "__main__":
    print("🚀 MonitorChatBot запускается...")

    # Устанавливаем вебхук
    set_webhook()

    # Запускаем Flask сервер
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)