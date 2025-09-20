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
    """Запуск Telegram бота с обработкой конфликтов"""
    import time
    from telebot.apihelper import ApiTelegramException
    
    max_retries = 3
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            bot = create_bot()
            logger.info("Telegram бот создан, начинаем polling...")
            
            # Проверяем подключение к Telegram API
            bot_info = bot.get_me()
            logger.info(f"Бот подключен: @{bot_info.username} ({bot_info.first_name})")
            
            # Очищаем pending updates для избежания конфликтов
            try:
                bot.get_updates(offset=-1, timeout=1)
                logger.info("Очищены предыдущие обновления")
            except:
                pass
            
            # Используем polling с обработкой ошибок
            bot.infinity_polling(
                timeout=10, 
                long_polling_timeout=10, 
                allowed_updates=['message', 'callback_query', 'edited_message'],
                restart_on_change=True
            )
            break  # Если дошли сюда - все ОК
            
        except ApiTelegramException as e:
            if e.error_code == 409:  # Conflict: другой бот polling
                logger.warning(f"Конфликт polling (попытка {attempt + 1}/{max_retries}). Ожидание {retry_delay}с...")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Увеличиваем задержку
                    continue
                else:
                    logger.error("Превышено максимальное количество попыток запуска polling")
                    raise
            else:
                logger.error(f"Ошибка Telegram API: {e}")
                raise
        except Exception as e:
            logger.error(f"Ошибка при запуске бота: {e}")
            if attempt < max_retries - 1:
                logger.info(f"Повторная попытка через {retry_delay}с...")
                time.sleep(retry_delay)
                retry_delay *= 2
                continue
            else:
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