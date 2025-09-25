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
    echo "Смотрите пример: .env.example"
    exit 1
fi

echo "✅ Файл .env найден"

# Проверяем новые переменные окружения
REQUIRED_VARS=(LOYALTY_CARD_LENGTH CAMPAIGN_1_NAME CAMPAIGN_2_NAME MANUAL_REVIEW_REQUIRED)
for VAR in "${REQUIRED_VARS[@]}"; do
  if ! grep -q "^$VAR=" .env; then
    echo "⚠️ Предупреждение: $VAR не найден в .env (будут использованы значения по умолчанию)"
  fi
done

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
mkdir -p data photos exports logs backups

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

# Резервная копия базы данных
echo "🗄️ Бэкап базы данных..."
python3 - << 'PY'
import os, shutil, datetime
from dotenv import load_dotenv
from config import get_database_path
load_dotenv()
path = get_database_path()
if os.path.exists(path):
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    dst = os.path.join('backups', f'db_backup_{ts}{os.path.splitext(path)[1]}')
    shutil.copy2(path, dst)
    print(f'✅ Резервная копия: {dst}')
else:
    print('ℹ️ Файл базы не найден — пропускаем бэкап')
PY

# Инициализируем/мигрируем базу данных
echo "🗄️ Инициализируем/мигрируем базу данных..."
python3 - << 'PY'
import sys
from database.db_manager import init_database, get_db_connection
from config import DATABASE_TYPE

try:
    init_database()
    # Миграции для существующей схемы
    with get_db_connection() as conn:
        c = conn.cursor()
        # Добавление столбцов при необходимости
        def safe_exec(sql):
            try:
                c.execute(sql)
            except Exception:
                pass
        # DuckDB/SQLite совместимые операции
        # Добавить столбцы
        safe_exec("ALTER TABLE applications ADD COLUMN loyalty_card_number TEXT")
        safe_exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_applications_loyalty_card ON applications(loyalty_card_number)")
        safe_exec("ALTER TABLE applications ADD COLUMN campaign_type TEXT")
        safe_exec("ALTER TABLE applications ADD COLUMN admin_notes TEXT")
        safe_exec("ALTER TABLE applications ADD COLUMN manual_review_status TEXT")
        # Обновить значения по умолчанию (в рамках возможностей)
        try:
            c.execute("UPDATE applications SET campaign_type = COALESCE(campaign_type,'pending')")
            c.execute("UPDATE applications SET manual_review_status = COALESCE(manual_review_status,'pending')")
        except Exception:
            pass
        # Удаление старого столбца (только для DuckDB)
        if DATABASE_TYPE == 'duckdb':
            try:
                c.execute("ALTER TABLE applications DROP COLUMN telegram_username")
            except Exception:
                pass
        conn.commit()
    print('✅ База данных готова')
except Exception as e:
    print(f'❌ Ошибка БД: {e}')
    sys.exit(1)
PY

echo "✅ Развертывание завершено!"
echo ""
echo "📝 Следующие шаги:"
echo "1. Скопируйте telegram-bot.service в /etc/systemd/system/"
echo "2. Запустите: sudo systemctl enable telegram-bot"
echo "3. Запустите: sudo systemctl start telegram-bot"
echo "4. Проверьте: sudo systemctl status telegram-bot"
echo ""
echo "🌐 Веб-панель будет доступна на: http://localhost:5000"
