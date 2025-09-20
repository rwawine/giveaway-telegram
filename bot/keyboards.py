"""
Клавиатуры для Telegram бота
"""

from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from config import get_web_base_url

from config import KEYBOARD_BUTTONS


def get_main_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Главная клавиатура для пользователя с улучшенным дизайном"""
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    
    # Первый ряд
    keyboard.row(
        KeyboardButton(KEYBOARD_BUTTONS['apply']),
        KeyboardButton(KEYBOARD_BUTTONS['status'])
    )
    
    # Второй ряд
    keyboard.row(
        KeyboardButton(KEYBOARD_BUTTONS['about']),
        KeyboardButton(KEYBOARD_BUTTONS['help'])
    )
    
    # Админ-кнопка для администраторов
    if is_admin:
        keyboard.add(KeyboardButton(KEYBOARD_BUTTONS['admin_panel']))
    
    return keyboard


def get_phone_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура для запроса номера телефона с улучшенным дизайном"""
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    
    # Большая кнопка для отправки контакта
    keyboard.add(KeyboardButton(KEYBOARD_BUTTONS['send_phone'], request_contact=True))
    
    # Альтернативный способ ввода
    keyboard.add(KeyboardButton(KEYBOARD_BUTTONS['enter_manual']))
    
    # Навигация
    keyboard.row(
        KeyboardButton(KEYBOARD_BUTTONS['back']),
        KeyboardButton(KEYBOARD_BUTTONS['cancel'])
    )
    
    return keyboard


def get_back_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура с кнопками навигации"""
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    keyboard.row(
        KeyboardButton(KEYBOARD_BUTTONS['back']),
        KeyboardButton(KEYBOARD_BUTTONS['cancel'])
    )
    return keyboard


def get_confirmation_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура подтверждения данных"""
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    
    # Подтверждение
    keyboard.add(KeyboardButton(KEYBOARD_BUTTONS['confirm']))
    
    # Редактирование
    keyboard.add(KeyboardButton(KEYBOARD_BUTTONS['edit']))
    
    # Отмена
    keyboard.add(KeyboardButton(KEYBOARD_BUTTONS['cancel']))
    
    return keyboard


def get_admin_keyboard() -> InlineKeyboardMarkup:
    """Упрощенная клавиатура для администратора"""
    keyboard = InlineKeyboardMarkup(row_width=2)

    keyboard.add(
        InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
        InlineKeyboardButton("📥 Экспорт", callback_data="admin_export")
    )

    keyboard.add(
        InlineKeyboardButton("📨 Рассылка", callback_data="admin_broadcast"),
        InlineKeyboardButton("🆘 Поддержка", callback_data="admin_support")
    )

    keyboard.add(
        InlineKeyboardButton("🌐 Веб-админка", url=get_web_base_url())
    )

    return keyboard


def get_winner_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для подтверждения выбора победителя"""
    keyboard = InlineKeyboardMarkup()
    keyboard.add(
        InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_winner"),
        InlineKeyboardButton("🔄 Выбрать заново", callback_data="select_new_winner")
    )
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_winner"))
    return keyboard


def get_export_format_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для выбора формата экспорта"""
    keyboard = InlineKeyboardMarkup()
    keyboard.add(
        InlineKeyboardButton("📄 CSV", callback_data="export_csv"),
        InlineKeyboardButton("📊 Excel", callback_data="export_excel")
    )
    return keyboard
