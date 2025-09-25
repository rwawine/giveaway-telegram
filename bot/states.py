"""
Состояния пользователя для пошагового процесса подачи заявки
"""

from enum import Enum


class UserState(Enum):
    """Состояния пользователя в процессе подачи заявки"""
    IDLE = "idle"
    WAITING_NAME = "waiting_name"
    WAITING_PHONE = "waiting_phone"
    WAITING_LOYALTY_CARD = "waiting_loyalty_card"
    WAITING_PHOTO = "waiting_photo"
    WAITING_SUPPORT_MESSAGE = "waiting_support_message"
    WAITING_ADMIN_REPLY = "waiting_admin_reply"
    WAITING_BROADCAST_MESSAGE = "waiting_broadcast_message"


# Временное хранилище состояний пользователей
user_states = {}
user_data = {}


def set_user_state(user_id: int, state: UserState):
    """Устанавливает состояние пользователя"""
    user_states[user_id] = state


def get_user_state(user_id: int) -> UserState:
    """Получает состояние пользователя"""
    return user_states.get(user_id, UserState.IDLE)


def clear_user_state(user_id: int):
    """Очищает состояние пользователя"""
    user_states.pop(user_id, None)
    user_data.pop(user_id, None)


def set_user_data(user_id: int, key: str, value):
    """Сохраняет данные пользователя"""
    if user_id not in user_data:
        user_data[user_id] = {}
    user_data[user_id][key] = value


def get_user_data(user_id: int, key: str = None):
    """Получает данные пользователя"""
    if key is None:
        return user_data.get(user_id, {})
    return user_data.get(user_id, {}).get(key)
