#!/bin/bash

# Скрипт автоматического развертывания Telegram бота
# Запускать с правами пользователя botuser

set -e  # Выходим при первой ошибке

echo "🚀 Развертывание Telegram бота..."

# Проверяем, что мы в правильной директории
if [ ! -f "main.py" ]; then
    echo "❌ Ошибка: Запустите скрипт из корневой папки проекта"
    exit 1
fi

# Проверяем наличие .env файла
if [ ! -f ".env" ]; then
    echo "❌ Ошибка: Создайте файл .env с настройками"
    echo "Пример содержимого:"
    echo "BOT_TOKEN=your_token_here"
    echo "ADMIN_PASSWORD=your_password"
    echo "ADMIN_IDS=123456789"
    exit 1
fi

echo "✅ Файл .env найден"

# Активируем виртуальное окружение
if [ ! -d "venv" ]; then
    echo "📦 Создаем виртуальное окружение..."
    python3 -m venv venv
fi

source venv/bin/activate
echo "✅ Виртуальное окружение активировано"

# Устанавливаем зависимости
echo "📦 Устанавливаем зависимости..."
pip install --upgrade pip
pip install -r requirements.txt

# Создаем необходимые папки
echo "📁 Создаем папки..."
mkdir -p data photos exports logs

# Проверяем подключение к Telegram API
echo "🔍 Проверяем подключение к Telegram..."
python3 -c "
import os
from dotenv import load_dotenv
import requests

load_dotenv()
bot_token = os.getenv('BOT_TOKEN')
if not bot_token or bot_token == 'YOUR_BOT_TOKEN_HERE':
    print('❌ Не задан BOT_TOKEN в .env файле')
    exit(1)

try:
    response = requests.get(f'https://api.telegram.org/bot{bot_token}/getMe', timeout=10)
    if response.status_code == 200:
        data = response.json()
        if data['ok']:
            print(f'✅ Бот подключен: @{data[\"result\"][\"username\"]}')
        else:
            print(f'❌ Ошибка API: {data}')
            exit(1)
    else:
        print(f'❌ HTTP ошибка: {response.status_code}')
        exit(1)
except Exception as e:
    print(f'❌ Ошибка подключения: {e}')
    exit(1)
"

# Инициализируем базу данных
echo "🗄️ Инициализируем базу данных..."
python3 -c "
from database.db_manager import init_database
try:
    init_database()
    print('✅ База данных инициализирована')
except Exception as e:
    print(f'❌ Ошибка БД: {e}')
    exit(1)
"

echo "✅ Развертывание завершено!"
echo ""
echo "📝 Следующие шаги:"
echo "1. Скопируйте telegram-bot.service в /etc/systemd/system/"
echo "2. Запустите: sudo systemctl enable telegram-bot"
echo "3. Запустите: sudo systemctl start telegram-bot"
echo "4. Проверьте: sudo systemctl status telegram-bot"
echo ""
echo "🌐 Веб-панель будет доступна на: http://localhost:5000"
