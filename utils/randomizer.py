"""
Честный рандомайзер для выбора победителя
"""

import hashlib
import random
import logging
from datetime import datetime
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)


def get_hash_seed() -> str:
    """
    Получает hash-seed для обеспечения прозрачности рандомайзера
    Использует хеш последнего блока Bitcoin как источник энтропии
    В случае недоступности - использует текущую дату
    
    Returns:
        str: Хеш-строка для использования как seed
    """
    try:
        # Пытаемся получить хеш последнего блока Bitcoin
        response = requests.get(
            'https://api.blockchain.info/q/latesthash',
            timeout=5
        )
        
        if response.status_code == 200:
            block_hash = response.text.strip()
            logger.info(f"Используется хеш Bitcoin блока: {block_hash}")
            return block_hash
            
    except Exception as e:
        logger.warning(f"Не удалось получить хеш блока Bitcoin: {e}")
    
    # Fallback: используем текущую дату и время
    fallback_seed = datetime.now().strftime("%Y-%m-%d-%H")
    logger.info(f"Используется fallback seed: {fallback_seed}")
    return fallback_seed


def generate_random_number(max_value: int, seed: str = None) -> int:
    """
    Генерирует случайное число от 1 до max_value
    
    Args:
        max_value: Максимальное значение (включительно)
        seed: Seed для рандомайзера (если не указан, получается автоматически)
    
    Returns:
        int: Случайное число от 1 до max_value
    """
    if seed is None:
        seed = get_hash_seed()
    
    # Создаем детерминированный seed из строки
    hash_object = hashlib.sha256(seed.encode())
    numeric_seed = int(hash_object.hexdigest(), 16) % (2**32)
    
    # Используем seed для рандомайзера
    rng = random.Random(numeric_seed)
    return rng.randint(1, max_value)


def create_winner_announcement(winner: Dict, total_participants: int, seed: str = None) -> str:
    """
    Создает сообщение об объявлении победителя с данными о прозрачности
    
    Args:
        winner: Данные победителя
        total_participants: Общее количество участников
        seed: Использованный seed
    
    Returns:
        str: Текст сообщения
    """
    if seed is None:
        seed = get_hash_seed()
    
    winner_number = generate_random_number(total_participants, seed)
    
    message = f"""
🎊 **РЕЗУЛЬТАТЫ РОЗЫГРЫША** 🎊

👑 **Победитель:** {winner['name']}
🧾 **Карта:** ****{(winner.get('loyalty_card_number') or '')[-4:]}
📞 **Телефон:** {winner['phone_number']}

📊 **Статистика розыгрыша:**
• Всего участников: {total_participants}
• Номер победителя: {winner_number}

🔍 **Данные для проверки честности:**
• Hash-seed: `{seed}`
• Время розыгрыша: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

*Результат можно проверить, используя указанный hash-seed*
"""
    
    return message


def verify_randomizer(seed: str, total_participants: int, expected_winner_number: int) -> bool:
    """
    Проверяет результат рандомайзера
    
    Args:
        seed: Использованный seed
        total_participants: Количество участников
        expected_winner_number: Ожидаемый номер победителя
    
    Returns:
        bool: True если результат корректен
    """
    try:
        actual_number = generate_random_number(total_participants, seed)
        return actual_number == expected_winner_number
    except Exception as e:
        logger.error(f"Ошибка при проверке рандомайзера: {e}")
        return False
