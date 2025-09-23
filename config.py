"""
Конфигурация приложения
"""

import os
from typing import List
import socket
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()

# Основные настройки
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')
WEB_PORT = int(os.getenv('WEB_PORT', '5000'))
WEB_BASE_URL = os.getenv('WEB_BASE_URL', 'http://127.0.0.1:5000')


def get_local_ip() -> str:
    """Возвращает локальный IP-адрес машины (LAN), с надежным фолбэком на 127.0.0.1"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_web_base_url() -> str:
    """Возвращает базовый URL веб-админки. Если WEB_BASE_URL не задан, вычисляет автоматически."""
    explicit = os.getenv('WEB_BASE_URL')
    if explicit and explicit.strip():
        return explicit.strip()
    return f"http://{get_local_ip()}:{WEB_PORT}"


# Ограничения рассылки / анти-флуд (настраиваемые через .env)
BROADCAST_RATE_PER_SEC = int(os.getenv('BROADCAST_RATE_PER_SEC', '8'))  # безопасно < 30
BROADCAST_MAX_RETRIES = int(os.getenv('BROADCAST_MAX_RETRIES', '3'))
BROADCAST_RETRY_BASE_DELAY = float(os.getenv('BROADCAST_RETRY_BASE_DELAY', '1.0'))

# ID администраторов (можно задать через переменную окружения)
ADMIN_IDS: List[int] = [
    int(id_str) for id_str in os.getenv('ADMIN_IDS', '').split(',') 
    if id_str.strip().isdigit()
]

# Настройки базы данных
DATABASE_TYPE = os.getenv('DATABASE_TYPE', 'duckdb')  # 'duckdb', 'sqlite' или 'postgresql'
DATABASE_PATH = os.getenv('DATABASE_PATH', 'applications.duckdb')

# Настройки для fallback на SQLite (если нужно)
SQLITE_PATH = 'applications.db'

def get_database_path() -> str:
    """Возвращает путь к файлу базы данных"""
    if DATABASE_TYPE == 'duckdb':
        return DATABASE_PATH
    elif DATABASE_TYPE == 'sqlite':
        return SQLITE_PATH
    else:
        return DATABASE_PATH

# Настройки файлов
PHOTOS_DIR = 'photos'
EXPORTS_DIR = 'exports'

# Сообщения бота
MESSAGES = {
    'welcome': (
        "🎉 **ДОБРО ПОЖАЛОВАТЬ В РОЗЫГРЫШ ПРИЗОВ!**\n\n"
        "🎯 Готовы выиграть крутые призы?\n"
        "🚀 Регистрация займет всего 1 минуту!\n\n"
        "👇 Выберите действие в меню ниже"
    ),
    'already_applied': (
        "🎯 **ВЫ УЖЕ УЧАСТВУЕТЕ!**\n\n"
        "✅ Ваша заявка принята и обрабатывается\n"
        "📊 Можете проверить свой статус в меню\n\n"
        "🍀 Удачи в розыгрыше!"
    ),
    'application_start': (
        "📝 **ШАГ 1 из 4 - Ваше имя**\n\n"
        "👤 Как вас зовут?\n"
        "💡 Используйте настоящее имя для получения приза\n\n"
        "Прогресс: [██░░░░░░░░] 25%"
    ),
    'ask_phone': (
        "📱 **ШАГ 2 из 4 - Контакт**\n\n"
        "📞 Поделитесь номером телефона для связи\n\n"
        "Прогресс: [████░░░░░░] 50%\n\n"
        "👇 Выберите способ:"
    ),
    'ask_username': (
        "💬 **ШАГ 3 из 4 - Telegram**\n\n"
        "📱 Укажите ваш ник в Telegram\n"
        "💡 Например: @username\n\n"
        "Прогресс: [██████░░░░] 75%"
    ),
    'ask_photo': (
        "📸 **ШАГ 4 из 4 - Фото лифлета**\n\n"
        "📎 Отправьте четкое фото вашего лифлета\n"
        "💡 Убедитесь, что текст читаем\n\n"
        "Прогресс: [████████░░] 90%"
    ),
    'application_success': (
        "🎉 **ПОЗДРАВЛЯЕМ!**\n\n"
        "✅ Вы успешно зарегистрированы!\n"
        "🎯 Ваш номер участника: #{user_id}\n\n"
        "📅 Розыгрыш состоится скоро\n"
        "👥 Следите за обновлениями\n\n"
        "🍀 Удачи! 🍀"
    ),
    'invalid_photo': (
        "❌ **НЕВЕРНЫЙ ФОРМАТ**\n\n"
        "📸 Пожалуйста, отправьте фото лифлета\n"
        "💡 Поддерживаются: JPG, PNG (до 10 МБ)"
    ),
    'error': (
        "😔 **ПРОИЗОШЛА ОШИБКА**\n\n"
        "🔄 Попробуйте еще раз через несколько секунд\n"
        "❓ Если проблема повторяется - обратитесь к администратору"
    ),
    'admin_not_authorized': "🔒 **ДОСТУП ЗАПРЕЩЕН**\n\n❌ У вас нет прав администратора",
    'no_applications': "📋 **ПУСТО**\n\n🤷‍♂️ Заявок пока нет. Как только появятся - увидите здесь!",
    'winner_selected': "🏆 **ПОБЕДИТЕЛЬ ОПРЕДЕЛЕН!**\n\n🎊 Поздравляем: **{name}** (@{username})",
    'export_ready': "📊 **ЭКСПОРТ ГОТОВ!**\n\n✅ Файл отправлен",
    'status_check': (
        "📋 **ВАШ СТАТУС В РОЗЫГРЫШЕ**\n\n"
        "┌─────────────────────────────┐\n"
        "│ 👤 Участник #{user_id}              │\n"
        "│                             │\n"
        "│ ✅ Регистрация завершена    │\n"
        "│ 📅 Дата: {date}  │\n"
        "│                             │\n"
        "│ 🎯 Статус: Активный участник│\n"
        "└─────────────────────────────┘"
    ),
    'help_message': (
        "❓ **СПРАВКА ПО БОТУ**\n\n"
        "🎯 **Как принять участие:**\n"
        "1️⃣ Нажмите \"🎯 Участвовать\"\n"
        "2️⃣ Заполните все данные\n"
        "3️⃣ Загрузите фото лифлета\n"
        "4️⃣ Дождитесь результатов\n\n"
        "📋 **Правила:**\n"
        "• Одна заявка на человека\n"
        "• Все поля обязательны\n"
        "• Фото должно быть четким\n\n"
        "🏆 **Розыгрыш честный и прозрачный!**\n"
        "Используется криптографический алгоритм"
    ),

    'support_message_prompt': """🆘 **ПОДДЕРЖКА**

Опишите вашу проблему или вопрос, и мы обязательно вам поможем!

✍️ Напишите ваше сообщение:"""

}


# Клавиатуры
KEYBOARD_BUTTONS = {
    # Главное меню
    'apply': "🎯 Участвовать",
    'status': "📋 Мой статус",
    'about': "📖 О розыгрыше",
    'help': "❓ Поддержка",
    
    # Процесс регистрации
    'send_phone': "📱 Поделиться контактом",
    'enter_manual': "✍️ Ввести вручную",
    'back': "⬅️ Назад",
    'cancel': "❌ Отменить",
    'confirm': "✅ Всё верно, отправить",
    'edit': "✏️ Изменить данные",
    
    # Админ
    'admin_panel': "🔐 Админ-панель",
    'admin_users': "👥 Участники",
    'admin_stats': "📊 Статистика",
    'admin_export': "📥 Экспорт",
    'admin_broadcast': "📨 Рассылка",
    'admin_winner': "🎰 Розыгрыш",
}

# Дополнительные сообщения для поддержки
SUPPORT_MESSAGES = {
    'support_start': """🆘 **ПОДДЕРЖКА**

Опишите свою проблему или вопрос, и мы обязательно вам поможем!

✍️ Напишите ваше сообщение:""",
    
    'support_sent': """✅ **Сообщение отправлено!**

Ваше обращение передано администраторам. 
Ответ придет в течение 24 часов.

📞 Номер обращения: #{ticket_id}""",
    
    'support_reply_prompt': """💬 **ОТВЕТ НА ОБРАЩЕНИЕ #{ticket_id}**

От: {user_name} (@{username})
Сообщение: "{message}"

✍️ Введите ваш ответ:""",
    
    'admin_support_new': """🆘 **НОВОЕ ОБРАЩЕНИЕ #{ticket_id}**

👤 От: {user_name} (@{username})
🆔 ID: {user_id}
📅 Время: {timestamp}

💬 **Сообщение:**
"{message}"

📝 Ответить: /reply_{ticket_id}""",
}
