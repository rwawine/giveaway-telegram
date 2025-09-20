"""
Главный файл для запуска Telegram бота с веб-админкой
"""

import asyncio
import logging
import threading
from typing import Dict, Any

from database.db_manager import init_database
from bot.telegram_bot import create_bot
from web.admin_panel import create_web_app

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


def start_bot():
    """Запуск Telegram бота"""
    try:
        bot = create_bot()
        logger.info("Telegram бот создан, начинаем polling...")
        
        # Проверяем подключение к Telegram API
        bot_info = bot.get_me()
        logger.info(f"Бот подключен: @{bot_info.username} ({bot_info.first_name})")
        
        # Используем infinity_polling для стабильной доставки callback_query
        bot.infinity_polling(timeout=20, long_polling_timeout=20, allowed_updates=[
            'message', 'callback_query', 'edited_message'
        ])
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        raise


def start_web_app():
    """Запуск веб-админки"""
    try:
        app = create_web_app()
        logger.info("Веб-админка запущена на порту 5000")
        app.run(host='0.0.0.0', port=5000, debug=False)
    except Exception as e:
        logger.error(f"Ошибка при запуске веб-админки: {e}")
        raise


def main():
    """Главная функция"""
    try:
        logger.info("Инициализация приложения...")
        
        # Инициализируем базу данных
        init_database()
        
        # Запускаем веб-приложение в отдельном потоке
        web_thread = threading.Thread(target=start_web_app)
        web_thread.daemon = True
        web_thread.start()
        
        # Запускаем бота в главном потоке
        start_bot()
        
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")


if __name__ == "__main__":
    main()