"""
Основной модуль Telegram бота
"""

import logging
import os
import threading
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

import telebot
from telebot.types import Message, CallbackQuery, Contact

from config import (
    BOT_TOKEN, ADMIN_IDS, MESSAGES, KEYBOARD_BUTTONS, SUPPORT_MESSAGES, get_web_base_url,
    BROADCAST_RATE_PER_SEC, BROADCAST_MAX_RETRIES, BROADCAST_RETRY_BASE_DELAY
)
from database.db_manager import (
    save_application, application_exists, get_all_applications,
    get_random_winner, get_winner, get_applications_stats, get_applications_count,
    create_support_ticket, get_support_ticket, reply_support_ticket,
    get_open_support_tickets
)
from bot.keyboards import (
    get_main_keyboard, get_phone_keyboard, get_back_keyboard,
    get_admin_keyboard, get_winner_confirmation_keyboard, get_export_format_keyboard
)
from bot.states import (
    UserState, set_user_state, get_user_state, clear_user_state,
    set_user_data, get_user_data
)
from utils.file_handler import save_photo, export_to_csv, export_to_excel
from utils.image_validation import analyze_leaflet
from utils.anti_fraud import AntiFraudSystem, sha256_hex
from utils.randomizer import create_winner_announcement, get_hash_seed

logger = logging.getLogger(__name__)

# Пул потоков для обработки регистраций при высокой нагрузке
registration_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="RegWorker")
RUNTIME_ADMINS = set()  # Админы, подтвержденные в текущем рантайме


def validate_phone_number(phone: str) -> tuple[bool, str]:
    """Валидирует номер телефона для стран: Беларусь (+375), Россия (+7), Казахстан (+77)"""
    if not phone:
        return False, "Номер телефона не может быть пустым"
    
    # Очищаем номер от пробелов и дефисов
    clean_phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    
    # Если номер не начинается с +, добавляем
    if not clean_phone.startswith('+'):
        clean_phone = '+' + clean_phone
    
    # Проверяем на соответствие форматам стран
    if clean_phone.startswith('+375'):
        # Беларусь: +375 XX XXX-XX-XX (всего 13 символов)
        if len(clean_phone) != 13 or not clean_phone[4:].isdigit():
            return False, "Некорректный номер телефона Беларуси. Формат: +375XXXXXXXXX"
        return True, clean_phone
        
    elif clean_phone.startswith('+77'):
        # Казахстан: +77 XXX XXX XX XX (всего 12 символов)
        if len(clean_phone) != 12 or not clean_phone[3:].isdigit():
            return False, "Некорректный номер телефона Казахстана. Формат: +77XXXXXXXXX"
        return True, clean_phone
        
    elif clean_phone.startswith('+7'):
        # Россия: +7 XXX XXX-XX-XX (всего 12 символов)
        if len(clean_phone) != 12 or not clean_phone[2:].isdigit():
            return False, "Некорректный номер телефона России. Формат: +7XXXXXXXXXX"
        return True, clean_phone
    
    else:
        return False, "Поддерживаются только номера:\n🇧🇾 Беларуси (+375)\n🇷🇺 России (+7)\n🇰🇿 Казахстана (+77)"


def validate_username(username: str) -> tuple[bool, str]:
    """Валидирует username - только английские символы"""
    if not username:
        return False, "Логин не может быть пустым"
    
    # Убираем @ если есть
    clean_username = username.lstrip('@')
    
    # Проверяем длину
    if len(clean_username) < 3:
        return False, "Логин должен содержать минимум 3 символа"
    
    if len(clean_username) > 32:
        return False, "Логин не может быть длиннее 32 символов"
    
    # Проверяем, что содержит только разрешенные символы
    import re
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', clean_username):
        return False, "Логин должен:\n• Начинаться с английской буквы\n• Содержать только английские буквы, цифры и _\n• Без пробелов и специальных символов"
    
    return True, clean_username


def create_bot() -> telebot.TeleBot:
    """Создает и настраивает Telegram бота"""
    bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=8)  # Увеличиваем количество потоков
    
    @bot.message_handler(commands=['start'])
    def handle_start(message: Message):
        """Обработчик команды /start"""
        try:
            user_id = message.from_user.id
            
            # Проверяем, подавал ли пользователь уже заявку
            if application_exists(user_id):
                bot.send_message(
                    message.chat.id,
                    MESSAGES['already_applied'],
                    reply_markup=get_main_keyboard()
                )
                return
            
            # Приветственное сообщение с проверкой прав админа
            is_admin_user = is_admin(user_id)
            bot.send_message(
                message.chat.id,
                MESSAGES['welcome'],
                reply_markup=get_main_keyboard(is_admin_user),
                parse_mode='Markdown'
            )
            
            logger.info(f"Пользователь {user_id} запустил бота")
            
        except Exception as e:
            logger.error(f"Ошибка в handle_start: {e}")
            bot.send_message(message.chat.id, MESSAGES['error'])
    
    @bot.message_handler(commands=['support'])
    def handle_support_command(message: Message):
        """Запускает диалог поддержки через команду /support"""
        try:
            user_id = message.from_user.id
            set_user_state(user_id, UserState.WAITING_SUPPORT_MESSAGE)
            logger.info(f"Поддержка: пользователь {user_id} запустил /support")
            bot.send_message(
                message.chat.id,
                SUPPORT_MESSAGES['support_start'],
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Ошибка в handle_support_command: {e}")
            bot.send_message(message.chat.id, MESSAGES['error'])
    
    
    @bot.message_handler(commands=['admin'])
    def handle_admin(message: Message):
        """Обработчик команды /admin"""
        try:
            user_id = message.from_user.id
            logger.info(f"Получена команда /admin от пользователя {user_id}")
            
            if not is_admin(user_id):
                logger.warning(f"Пользователь {user_id} попытался получить доступ к админке")
                bot.send_message(message.chat.id, MESSAGES['admin_not_authorized'])
                return
            
            # Фиксируем этого пользователя как runtime-админа (для колбеков/нотификаций)
            RUNTIME_ADMINS.add(user_id)
            
            stats = get_applications_stats()
            admin_text = f"👑 **АДМИН-ПАНЕЛЬ**\n\nВсего заявок: {stats['total_applications']}"
            
            logger.info(f"Отправляем админ-панель пользователю {user_id}")
            bot.send_message(
                message.chat.id,
                admin_text,
                reply_markup=get_admin_keyboard(),
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Ошибка в handle_admin: {e}")
            bot.send_message(message.chat.id, MESSAGES['error'])
    
    
    
    
    @bot.message_handler(func=lambda message: message.text and message.text.startswith('/reply_'))
    def handle_reply_command(message: Message):
        """Обработчик команды /reply_X для ответа на тикеты поддержки"""
        try:
            user_id = message.from_user.id
            
            if not is_admin(user_id):
                bot.send_message(message.chat.id, MESSAGES['admin_not_authorized'])
                return
            
            # Извлекаем ID тикета из команды
            command_text = message.text
            ticket_id = int(command_text.replace('/reply_', ''))
            
            # Получаем информацию о тикете
            ticket = get_support_ticket(ticket_id)
            if not ticket:
                bot.send_message(message.chat.id, "❌ Тикет не найден")
                return
            
            # Устанавливаем состояние ожидания ответа от админа
            set_user_state(user_id, UserState.WAITING_ADMIN_REPLY)
            set_user_data(user_id, 'reply_ticket_id', ticket_id)
            
            # Отправляем промпт для ответа
            reply_prompt = SUPPORT_MESSAGES['support_reply_prompt'].format(
                ticket_id=ticket_id,
                user_name=ticket['user_name'],
                username=ticket['username'] or 'не указан',
                message=ticket['message']
            )
            
            bot.send_message(
                message.chat.id,
                reply_prompt,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Ошибка в handle_reply_command: {e}")
            bot.send_message(message.chat.id, MESSAGES['error'])
    
    
    @bot.message_handler(commands=['список'])
    def handle_list_applications(message: Message):
        """Обработчик команды /список"""
        try:
            user_id = message.from_user.id
            
            if not is_admin(user_id):
                bot.send_message(message.chat.id, MESSAGES['admin_not_authorized'])
                return
            
            applications = get_all_applications()
            
            if not applications:
                bot.send_message(message.chat.id, MESSAGES['no_applications'])
                return
            
            # Формируем список заявок
            text = "📋 **Список заявок:**\n\n"
            for i, app in enumerate(applications, 1):
                winner_mark = "👑 " if app['is_winner'] else ""
                text += f"{i}. {winner_mark}{app['name']} (@{app['telegram_username']})\n"
                text += f"   📞 {app['phone_number']}\n"
                text += f"   🕐 {app['timestamp']}\n\n"
            
            # Отправляем список частями если он слишком длинный
            if len(text) > 4000:
                parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
                for part in parts:
                    bot.send_message(message.chat.id, part, parse_mode='Markdown')
            else:
                bot.send_message(message.chat.id, text, parse_mode='Markdown')
                
        except Exception as e:
            logger.error(f"Ошибка в handle_list_applications: {e}")
            bot.send_message(message.chat.id, MESSAGES['error'])
    
    
    @bot.message_handler(commands=['выбрать_победителя'])
    def handle_select_winner(message: Message):
        """Обработчик команды /выбрать_победителя"""
        try:
            user_id = message.from_user.id
            
            if not is_admin(user_id):
                bot.send_message(message.chat.id, MESSAGES['admin_not_authorized'])
                return
            
            applications = get_all_applications()
            
            if not applications:
                bot.send_message(message.chat.id, MESSAGES['no_applications'])
                return
            
            # Выбираем победителя
            winner = get_random_winner()
            if not winner:
                bot.send_message(message.chat.id, "❌ Ошибка при выборе победителя")
                return
            
            # Создаем объявление о победителе
            announcement = create_winner_announcement(
                winner, len(applications), get_hash_seed()
            )
            
            bot.send_message(
                message.chat.id,
                announcement,
                reply_markup=get_winner_confirmation_keyboard(),
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Ошибка в handle_select_winner: {e}")
            bot.send_message(message.chat.id, MESSAGES['error'])
    
    
    @bot.message_handler(commands=['экспорт'])
    def handle_export(message: Message):
        """Обработчик команды /экспорт"""
        try:
            user_id = message.from_user.id
            
            if not is_admin(user_id):
                bot.send_message(message.chat.id, MESSAGES['admin_not_authorized'])
                return
            
            bot.send_message(
                message.chat.id,
                "📊 Выберите формат экспорта:",
                reply_markup=get_export_format_keyboard()
            )
            
        except Exception as e:
            logger.error(f"Ошибка в handle_export: {e}")
            bot.send_message(message.chat.id, MESSAGES['error'])
    
    
    @bot.message_handler(content_types=['text'])
    def handle_text_messages(message: Message):
        """Обработчик текстовых сообщений"""
        try:
            user_id = message.from_user.id
            text = message.text
            state = get_user_state(user_id)
            logger.info(f"TEXT msg from {user_id}: state={state}, text={text}")
            
            # Главное меню
            if text == KEYBOARD_BUTTONS['apply']:
                start_application_process(bot, message)
            elif text == KEYBOARD_BUTTONS['status']:
                handle_status_check(bot, message)
            elif text == KEYBOARD_BUTTONS['about']:
                handle_about_contest(bot, message)
            elif text == KEYBOARD_BUTTONS['help']:
                # Начинаем процесс поддержки
                set_user_state(user_id, UserState.WAITING_SUPPORT_MESSAGE)
                logger.info(f"Клик: {user_id} нажал '{KEYBOARD_BUTTONS['help']}'")
                bot.send_message(
                    message.chat.id,
                    MESSAGES['support_message_prompt'],
                    parse_mode='Markdown'
                )
            elif text == KEYBOARD_BUTTONS['back']:
                handle_back_button(bot, message)
            elif text == KEYBOARD_BUTTONS['cancel']:
                handle_cancel_button(bot, message)
            elif text == KEYBOARD_BUTTONS['admin_panel'] and is_admin(user_id):
                logger.info(f"Клик: {user_id} нажал '{KEYBOARD_BUTTONS['admin_panel']}'")
                handle_admin(message)
            
            # Процесс подачи заявки
            elif state == UserState.WAITING_NAME:
                handle_name_input(bot, message)
            elif state == UserState.WAITING_PHONE:
                handle_phone_input(bot, message)
            elif state == UserState.WAITING_USERNAME:
                handle_username_input(bot, message)
            elif state == UserState.WAITING_SUPPORT_MESSAGE:
                logger.info(f"Поддержка: прием сообщения от {user_id}")
                handle_support_message_input(bot, message)
            elif state == UserState.WAITING_ADMIN_REPLY:
                logger.info(f"Поддержка: админ {user_id} отвечает на тикет")
                handle_admin_reply_input(bot, message)
            elif state == UserState.WAITING_BROADCAST_MESSAGE:
                # Админ ввел текст рассылки
                target = get_user_data(user_id, 'broadcast_target') or 'all'
                text_to_send = (message.text or '').strip()
                if not text_to_send:
                    bot.send_message(message.chat.id, "❌ Пустой текст. Введите сообщение для рассылки.")
                    return
                
                try:
                    if target == 'all':
                        applications = get_all_applications()
                        success_count = 0
                        import time
                        interval = max(0.04, 1.0 / max(1, BROADCAST_RATE_PER_SEC))
                        for app in applications:
                            retries = 0
                            while retries <= BROADCAST_MAX_RETRIES:
                                try:
                                    bot.send_message(app['telegram_id'], text_to_send)
                                    success_count += 1
                                    break
                                except telebot.apihelper.ApiTelegramException as e:
                                    # Flood/Too Many Requests/backoff
                                    if 'Too Many Requests' in str(e) or e.error_code == 429:
                                        delay = BROADCAST_RETRY_BASE_DELAY * (2 ** retries)
                                        time.sleep(delay)
                                        retries += 1
                                        continue
                                    elif e.error_code in (403, 400):
                                        # Blocked by user / bad request — пропускаем
                                        break
                                    else:
                                        break
                                except Exception:
                                    break
                            time.sleep(interval)
                        bot.send_message(message.chat.id, f"✅ Рассылка выполнена: {success_count}/{len(applications)}")
                    elif target == 'winner':
                        winner = get_winner()
                        if not winner:
                            bot.send_message(message.chat.id, "❌ Победитель еще не определен")
                        else:
                            try:
                                bot.send_message(winner['telegram_id'], text_to_send)
                                bot.send_message(message.chat.id, "✅ Сообщение победителю отправлено")
                            except Exception:
                                bot.send_message(message.chat.id, "❌ Не удалось отправить сообщение победителю")
                finally:
                    clear_user_state(user_id)
            
            else:
                # Неизвестная команда
                bot.send_message(
                    message.chat.id,
                    "🤔 Используйте кнопки для навигации",
                    reply_markup=get_main_keyboard()
                )
                
        except Exception as e:
            logger.error(f"Ошибка в handle_text_messages: {e}")
            bot.send_message(message.chat.id, MESSAGES['error'])
    
    
    @bot.message_handler(content_types=['contact'])
    def handle_contact(message: Message):
        """Обработчик отправки контакта"""
        try:
            user_id = message.from_user.id
            state = get_user_state(user_id)
            
            if state == UserState.WAITING_PHONE:
                contact: Contact = message.contact
                logger.info(f"📱 ПОЛУЧЕН КОНТАКТ от TG_ID {user_id}")
                
                if contact.user_id == user_id:
                    # Пользователь отправил свой контакт
                    phone_number = contact.phone_number
                    if not phone_number.startswith('+'):
                        phone_number = '+' + phone_number
                    
                    # Валидируем номер телефона
                    is_valid, result_or_error = validate_phone_number(phone_number)
                    
                    if not is_valid:
                        logger.warning(f"⚠️ Некорректный номер из контакта: {phone_number}")
                        bot.send_message(
                            message.chat.id,
                            f"❌ {result_or_error}"
                        )
                        return
                    
                    # Используем очищенный номер
                    phone_number = result_or_error
                    logger.info(f"✅ Контакт валиден: {phone_number}")
                    
                    # Проверяем, не используется ли уже этот номер
                    if application_exists(user_id, phone_number):
                        logger.warning(f"⚠️ Номер {phone_number} уже используется")
                        bot.send_message(
                            message.chat.id,
                            "❌ Заявка с этим номером телефона уже существует."
                        )
                        return
                    
                    set_user_data(user_id, 'phone_number', phone_number)
                    
                    # Переходим к следующему шагу
                    set_user_state(user_id, UserState.WAITING_USERNAME)
                    bot.send_message(
                        message.chat.id,
                        MESSAGES['ask_username'],
                        reply_markup=get_back_keyboard()
                    )
                    logger.info(f"📝 Переход к вводу username для TG_ID {user_id}")
                else:
                    logger.warning(f"⚠️ Чужой контакт от TG_ID {user_id}")
                    bot.send_message(
                        message.chat.id,
                        "❌ Отправьте свой собственный контакт"
                    )
            else:
                bot.send_message(
                    message.chat.id,
                    "🤔 Используйте кнопки для навигации",
                    reply_markup=get_main_keyboard()
                )
                
        except Exception as e:
            logger.error(f"Ошибка в handle_contact: {e}")
            bot.send_message(message.chat.id, MESSAGES['error'])
    
    
    @bot.message_handler(content_types=['photo'])
    def handle_photo(message: Message):
        """Обработчик фотографий"""
        try:
            user_id = message.from_user.id
            state = get_user_state(user_id)
            
            if state == UserState.WAITING_PHOTO:
                process_photo_submission(bot, message)
            else:
                # Определяем, на каком шаге находится пользователь и даем соответствующую инструкцию
                if state == UserState.WAITING_NAME:
                    error_msg = ("❌ **ФОТО НА НЕПРАВИЛЬНОМ ШАГЕ!**\n\n"
                               "📝 **ШАГ 1:** Сначала введите ваше имя\n"
                               "📱 Шаг 2: Номер телефона\n"
                               "👤 Шаг 3: Логин\n"
                               "📸 Шаг 4: Фото лифлета\n\n"
                               "💡 Введите ваше имя, чтобы продолжить!")
                elif state == UserState.WAITING_PHONE:
                    error_msg = ("❌ **ФОТО НА НЕПРАВИЛЬНОМ ШАГЕ!**\n\n"
                               "✅ Шаг 1: Имя ✓\n"
                               "📱 **ШАГ 2:** Сейчас нужен номер телефона\n"
                               "👤 Шаг 3: Логин\n"
                               "📸 Шаг 4: Фото лифлета\n\n"
                               "💡 Введите или отправьте ваш номер телефона!")
                elif state == UserState.WAITING_USERNAME:
                    error_msg = ("❌ **ФОТО НА НЕПРАВИЛЬНОМ ШАГЕ!**\n\n"
                               "✅ Шаг 1: Имя ✓\n"
                               "✅ Шаг 2: Телефон ✓\n"
                               "👤 **ШАГ 3:** Сейчас нужен ваш логин\n"
                               "📸 Шаг 4: Фото лифлета\n\n"
                               "💡 Введите ваш Telegram username!")
                else:
                    error_msg = ("❌ **НЕОЖИДАННАЯ ФОТОГРАФИЯ!**\n\n"
                               "🤔 Фото принимается только на 4-м шаге регистрации.\n"
                               "Нажмите \"🎯 Участвовать\" для начала регистрации.")
                
                bot.send_message(
                    message.chat.id,
                    error_msg,
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logger.error(f"Ошибка в handle_photo: {e}")
            bot.send_message(message.chat.id, MESSAGES['error'])
    
    
    @bot.callback_query_handler(func=lambda call: True)
    def handle_callback_queries(call: CallbackQuery):
        """Обработчик inline-кнопок"""
        try:
            user_id = call.from_user.id
            data = call.data
            logger.info(f"Callback from {user_id}: {data}")
            # Немедленно подтверждаем клик, чтобы убрать индикатор загрузки на стороне клиента
            try:
                bot.answer_callback_query(call.id)
            except Exception:
                pass
            
            if not is_admin(user_id):
                logger.warning(f"Неавторизованный callback от пользователя {user_id}")
                try:
                    bot.answer_callback_query(call.id, "❌ Нет прав доступа")
                except Exception:
                    pass
                return
            
            # Админ функции
            if data == "admin_stats":
                logger.info(f"Клик: {user_id} -> admin_stats")
                handle_admin_stats_callback(bot, call)
            elif data == "admin_export":
                logger.info(f"Клик: {user_id} -> admin_export")
                handle_admin_export_callback(bot, call)
            elif data == "admin_broadcast":
                logger.info(f"Клик: {user_id} -> admin_broadcast")
                handle_admin_broadcast_callback(bot, call)
            elif data == "admin_support":
                logger.info(f"Клик: {user_id} -> admin_support")
                handle_admin_support_callback(bot, call)
            elif data == "admin_web_info":
                logger.info(f"Клик: {user_id} -> admin_web_info")
                handle_admin_web_info_callback(bot, call)
            
            # Обработчики розыгрыша
            elif data == "confirm_winner":
                logger.info(f"Клик: {user_id} -> confirm_winner")
                handle_confirm_winner_callback(bot, call)
            elif data == "select_new_winner":
                logger.info(f"Клик: {user_id} -> select_new_winner")
                handle_select_new_winner_callback(bot, call)
            elif data == "cancel_winner":
                logger.info(f"Клик: {user_id} -> cancel_winner")
                handle_cancel_winner_callback(bot, call)
            
            # Экспорт
            elif data == "export_csv":
                logger.info(f"Клик: {user_id} -> export_csv")
                handle_export_csv_callback(bot, call)
            elif data == "export_excel":
                logger.info(f"Клик: {user_id} -> export_excel")
                handle_export_excel_callback(bot, call)
            
            # Рассылка
            elif data.startswith("broadcast_"):
                logger.info(f"Клик: {user_id} -> {data}")
                handle_broadcast_action(bot, call)
            
            # Поддержка: действия
            elif data.startswith("support_reply_"):
                handle_admin_support_reply_action(bot, call)
            elif data.startswith("support_close_"):
                handle_admin_support_close_action(bot, call)
            
            # Настройки
            elif data == "settings_clear_apps":
                logger.info(f"Клик: {user_id} -> settings_clear_apps")
                handle_settings_clear_apps(bot, call)
            elif data == "settings_system_info":
                logger.info(f"Клик: {user_id} -> settings_system_info")
                handle_settings_system_info(bot, call)
            elif data == "settings_open_tickets":
                logger.info(f"Клик: {user_id} -> settings_open_tickets")
                handle_settings_open_tickets(bot, call)
            elif data == "settings_close_tickets":
                logger.info(f"Клик: {user_id} -> settings_close_tickets")
                handle_settings_close_tickets(bot, call)
            elif data == "settings_confirm_clear":
                logger.info(f"Клик: {user_id} -> settings_confirm_clear")
                handle_settings_confirm_clear(bot, call)
            elif data == "settings_confirm_close_tickets":
                logger.info(f"Клик: {user_id} -> settings_confirm_close_tickets")
                handle_settings_confirm_close_tickets(bot, call)
            elif data == "settings_back":
                logger.info(f"Клик: {user_id} -> settings_back")
                # Возврат в корень админ-панели без изменения текста, если он совпадает — избегаем 400
                try:
                    stats = get_applications_stats()
                    admin_text = f"👑 **АДМИН-ПАНЕЛЬ**\n\nВсего заявок: {stats['total_applications']}"
                    if call.message.text != admin_text:
                        bot.edit_message_text(
                            admin_text,
                            call.message.chat.id,
                            call.message.message_id,
                            reply_markup=get_admin_keyboard(),
                            parse_mode='Markdown'
                        )
                except Exception as e:
                    logger.error(f"Ошибка возврата назад: {e}")
            
            
            # Старые callback'и для совместимости
            elif data == "admin_list":
                handle_admin_users_callback(bot, call)
            
            bot.answer_callback_query(call.id)
            
        except Exception as e:
            logger.error(f"Ошибка в handle_callback_queries: {e}")
            bot.answer_callback_query(call.id, "❌ Произошла ошибка")
    
    
    return bot


def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором"""
    return (user_id in ADMIN_IDS) or (user_id in RUNTIME_ADMINS)


def start_application_process(bot: telebot.TeleBot, message: Message):
    """Начинает процесс подачи заявки"""
    user_id = message.from_user.id
    logger.info(f"🎯 КЛИК УЧАСТВОВАТЬ от пользователя TG_ID: {user_id}")
    
    # Проверяем, не подавал ли пользователь уже заявку
    logger.info(f"🔍 Проверяем существующие заявки для TG_ID: {user_id}")
    if application_exists(user_id):
        logger.warning(f"⚠️ Заявка уже существует для TG_ID: {user_id}")
        bot.send_message(
            message.chat.id,
            MESSAGES['already_applied'],
            reply_markup=get_main_keyboard()
        )
        return
    
    # Начинаем процесс
    logger.info(f"✅ Заявки не найдено, начинаем регистрацию для TG_ID: {user_id}")
    set_user_state(user_id, UserState.WAITING_NAME)
    bot.send_message(
        message.chat.id,
        MESSAGES['application_start'],
        reply_markup=get_back_keyboard()
    )
    logger.info(f"📝 Отправлено приглашение ввести имя для TG_ID: {user_id}")


def handle_name_input(bot: telebot.TeleBot, message: Message):
    """Обрабатывает ввод имени"""
    user_id = message.from_user.id
    name = message.text.strip()
    
    if len(name) < 2:
        bot.send_message(message.chat.id, "❌ Имя слишком короткое. Попробуйте еще раз.")
        return
    
    set_user_data(user_id, 'name', name)
    set_user_state(user_id, UserState.WAITING_PHONE)
    
    bot.send_message(
        message.chat.id,
        MESSAGES['ask_phone'],
        reply_markup=get_phone_keyboard()
    )


def handle_phone_input(bot: telebot.TeleBot, message: Message):
    """Обрабатывает ввод номера телефона"""
    user_id = message.from_user.id
    text = message.text.strip()
    
    # Обрабатываем кнопку "Ввести вручную"
    if text == KEYBOARD_BUTTONS['enter_manual']:
        bot.send_message(
            message.chat.id,
            "📱 Введите ваш номер телефона:\n\n💡 Поддерживаются номера:\n🇧🇾 Беларуси: +375291234567\n🇷🇺 России: +79001234567\n🇰🇿 Казахстана: +77001234567",
            reply_markup=get_back_keyboard()
        )
        return
    
    # Валидируем номер телефона
    is_valid, result_or_error = validate_phone_number(text)
    
    if not is_valid:
        bot.send_message(message.chat.id, f"❌ {result_or_error}")
        return
    
    # Если валидация прошла, используем очищенный номер
    phone = result_or_error
    
    # Проверяем, не используется ли уже этот номер
    if application_exists(user_id, phone):
        bot.send_message(
            message.chat.id,
            "❌ Заявка с этим номером телефона уже существует."
        )
        return
    
    set_user_data(user_id, 'phone_number', phone)
    set_user_state(user_id, UserState.WAITING_USERNAME)
    
    bot.send_message(
        message.chat.id,
        MESSAGES['ask_username'],
        reply_markup=get_back_keyboard()
    )


def handle_username_input(bot: telebot.TeleBot, message: Message):
    """Обрабатывает ввод username"""
    user_id = message.from_user.id
    username = message.text.strip()
    
    # Если пользователь не указал username, берем из профиля
    if not username and message.from_user.username:
        username = message.from_user.username
    
    # Если username все еще пустой, требуем ввести
    if not username:
        bot.send_message(
            message.chat.id,
            "❌ Логин не может быть пустым. Введите ваш Telegram username:"
        )
        return
    
    # Валидируем username
    is_valid, result_or_error = validate_username(username)
    
    if not is_valid:
        bot.send_message(
            message.chat.id,
            f"❌ {result_or_error}\n\n💡 Пример правильного логина: john_doe123"
        )
        return
    
    # Используем очищенный username
    username = result_or_error
    
    set_user_data(user_id, 'telegram_username', username)
    set_user_state(user_id, UserState.WAITING_PHOTO)
    
    bot.send_message(
        message.chat.id,
        MESSAGES['ask_photo'],
        reply_markup=get_back_keyboard()
    )


def save_application_in_background(user_data: dict, user_id: int, photo_path: str, photo_hash: str):
    """Быстрое сохранение заявки в БД в фоновом режиме"""
    try:
        success = save_application(
            name=user_data['name'],
            phone_number=user_data['phone_number'],
            telegram_username=user_data.get('telegram_username', ''),
            telegram_id=user_id,
            photo_path=photo_path,
            photo_hash=photo_hash,
            risk_score=0,
            risk_level='low',
            risk_details='{}',
            status='approved',
            leaflet_status='approved',
            stickers_count=0,
            validation_notes='{}',
            manual_review_required=0,
            photo_phash=''
        )
        
        if success:
            logger.info(f"Заявка сохранена в БД для пользователя {user_id}")
        else:
            logger.warning(f"Заявка уже существовала для пользователя {user_id}")
            
    except Exception as e:
        logger.error(f"Ошибка при сохранении заявки в фоне: {e}")


def process_photo_submission_async(bot: telebot.TeleBot, message: Message, user_id: int, photo_file: bytes, photo_path: str, photo_hash: str):
    """Асинхронная обработка регистрации"""
    try:
        # Получаем данные пользователя
        user_data = get_user_data(user_id)
        if not user_data:
            logger.error(f"Не найдены данные пользователя {user_id}")
            return
        
        # Пропускаем антифрод проверки для максимальной скорости
        
        # Создаем заявку быстро - минимум проверок
        import json as _json
        success = save_application(
            name=user_data['name'],
            phone_number=user_data['phone_number'],
            telegram_username=user_data.get('telegram_username', ''),
            telegram_id=user_id,
            photo_path=photo_path,
            photo_hash=photo_hash,
            risk_score=0,  # Минимальный риск для скорости
            risk_level='low',
            risk_details='{}',  # Пустые детали для скорости
            status='approved',  # Всегда одобряем для скорости
            leaflet_status='approved',  # Всегда одобряем
            stickers_count=0,
            validation_notes='{}',
            manual_review_required=0,
            photo_phash=''
        )
        
        if success:
            is_admin_user = is_admin(user_id)
            success_message = MESSAGES['application_success'].format(user_id=user_id)
            bot.send_message(
                message.chat.id,
                success_message,
                reply_markup=get_main_keyboard(is_admin_user),
                parse_mode='Markdown'
            )
            logger.info(f"Заявка создана для пользователя {user_id}")
        else:
            is_admin_user = is_admin(user_id)
            bot.send_message(
                message.chat.id,
                "❌ **ЗАЯВКА УЖЕ СУЩЕСТВУЕТ**\n\n✅ Вы уже зарегистрированы",
                reply_markup=get_main_keyboard(is_admin_user),
                parse_mode='Markdown'
            )
        
        # Очищаем состояние
        clear_user_state(user_id)
        
    except Exception as e:
        logger.error(f"Ошибка при асинхронной обработке регистрации: {e}")
        try:
            bot.send_message(message.chat.id, MESSAGES['error'])
        except:
            pass


def process_photo_submission(bot: telebot.TeleBot, message: Message):
    """Обрабатывает отправку фото и завершает заявку быстро"""
    user_id = message.from_user.id
    
    try:
        # Получаем фото (берем меньшее разрешение для скорости)
        photo = message.photo[0] if len(message.photo) > 0 else message.photo[-1]
        file_info = bot.get_file(photo.file_id)
        photo_file = bot.download_file(file_info.file_path)
        
        # Быстрое сохранение фото
        photo_path = save_photo(photo_file, user_id)
        
        # Быстрый хеш (берем первые 1000 байт для скорости)
        photo_hash = sha256_hex(photo_file[:1000] if len(photo_file) > 1000 else photo_file)
        
        # Сразу показываем успех пользователю
        user_data = get_user_data(user_id)
        if user_data:
            is_admin_user = is_admin(user_id)
            success_message = MESSAGES['application_success'].format(user_id=user_id)
            bot.send_message(
                message.chat.id,
                success_message,
                reply_markup=get_main_keyboard(is_admin_user),
                parse_mode='Markdown'
            )
            
            # Очищаем состояние сразу
            clear_user_state(user_id)
            
            # Сохранение в БД в фоне (не блокирует пользователя)
            registration_executor.submit(
                save_application_in_background,
                user_data, user_id, photo_path, photo_hash
            )
            
            logger.info(f"Пользователь {user_id} получил быстрый ответ, сохранение в фоне")
        
    except Exception as e:
        logger.error(f"Ошибка при обработке фото: {e}")
        try:
            bot.send_message(message.chat.id, MESSAGES['error'])
        except:
            pass


def handle_back_button(bot: telebot.TeleBot, message: Message):
    """Обрабатывает кнопку "Назад" """
    user_id = message.from_user.id
    clear_user_state(user_id)
    
    bot.send_message(
        message.chat.id,
        "↩️ Возврат в главное меню",
        reply_markup=get_main_keyboard()
    )


def send_help_message(bot: telebot.TeleBot, message: Message):
    """Отправляет справочное сообщение"""
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)
    
    bot.send_message(
        message.chat.id, 
        MESSAGES['help_message'], 
        parse_mode='Markdown',
        reply_markup=get_main_keyboard(is_admin_user)
    )


def handle_status_check(bot: telebot.TeleBot, message: Message):
    """Обрабатывает проверку статуса участника"""
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)
    
    try:
        # Проверяем, есть ли заявка
        if not application_exists(user_id):
            bot.send_message(
                message.chat.id,
                "📋 **СТАТУС НЕ НАЙДЕН**\n\n❌ Вы еще не подали заявку\n🎯 Нажмите \"Участвовать\" для регистрации",
                reply_markup=get_main_keyboard(is_admin_user),
                parse_mode='Markdown'
            )
            return
        
        # Получаем данные пользователя
        applications = get_all_applications()
        user_app = next((app for app in applications if app['telegram_id'] == user_id), None)
        
        if user_app:
            # Форматируем дату
            from datetime import datetime
            try:
                # Если timestamp уже объект datetime (для DuckDB)
                if isinstance(user_app['timestamp'], datetime):
                    date_obj = user_app['timestamp']
                else:
                    # Если строка (для SQLite)
                    date_obj = datetime.fromisoformat(user_app['timestamp'].replace('Z', '+00:00'))
                
                # Используем полный формат даты: DD.MM.YYYY HH:MM
                formatted_date = date_obj.strftime("%d.%m.%Y %H:%M")
            except Exception as e:
                logger.warning(f"Ошибка форматирования даты {user_app['timestamp']}: {e}")
                # Fallback - берем первые 16 символов и пытаемся распарсить
                try:
                    fallback_str = str(user_app['timestamp'])[:16]
                    if len(fallback_str) >= 16:
                        formatted_date = fallback_str.replace('T', ' ')
                    else:
                        formatted_date = str(user_app['timestamp'])
                except:
                    formatted_date = "дата неизвестна"
            
            # Используем participant_number если есть, иначе id записи в БД
            participant_number = user_app.get('participant_number') or user_app['id']

            status_text = MESSAGES['status_check'].format(
                user_id=participant_number,
                date=formatted_date
            )
            
            bot.send_message(
                message.chat.id,
                status_text,
                reply_markup=get_main_keyboard(is_admin_user),
                parse_mode='Markdown'
            )
        else:
            bot.send_message(
                message.chat.id,
                "❌ Ошибка получения данных",
                reply_markup=get_main_keyboard(is_admin_user)
            )
            
    except Exception as e:
        logger.error(f"Ошибка в handle_status_check: {e}")
        bot.send_message(message.chat.id, MESSAGES['error'])


def handle_about_contest(bot: telebot.TeleBot, message: Message):
    """Информация о розыгрыше"""
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)
    
    about_text = """📖 **О РОЗЫГРЫШЕ**

🎯 **Что разыгрываем:**
Крутые призы для наших участников!

🎲 **Как проходит розыгрыш:**
• Честный криптографический алгоритм
• Прозрачность результатов
• Равные шансы для всех участников

📋 **Правила участия:**
• Одна заявка на человека
• Заполнить все данные
• Загрузить фото лифлета
• Быть активным участником

🏆 **Определение победителя:**
Используется генератор случайных чисел на основе криптографических хешей для максимальной честности.

✨ Удачи всем участникам! ✨"""
    
    bot.send_message(
        message.chat.id,
        about_text,
        reply_markup=get_main_keyboard(is_admin_user),
        parse_mode='Markdown'
    )



def handle_cancel_button(bot: telebot.TeleBot, message: Message):
    """Обрабатывает кнопку отмены"""
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)
    
    # Очищаем состояние пользователя
    clear_user_state(user_id)
    
    bot.send_message(
        message.chat.id,
        "❌ **ОПЕРАЦИЯ ОТМЕНЕНА**\n\n🏠 Возврат в главное меню",
        reply_markup=get_main_keyboard(is_admin_user),
        parse_mode='Markdown'
    )


# Новые callback handlers для админки
def handle_admin_users_callback(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик кнопки списка участников в админке"""
    applications = get_all_applications()
    
    if not applications:
        bot.edit_message_text(
            "📋 **УЧАСТНИКИ**\n\n🤷‍♂️ Заявок пока нет",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard(),
            parse_mode='Markdown'
        )
        return
    
    text = f"👥 **УЧАСТНИКИ ({len(applications)})**\n\n"
    for i, app in enumerate(applications[:8], 1):  # Показываем первые 8
        winner_mark = "👑 " if app['is_winner'] else ""
        text += f"{i}. {winner_mark}**{app['name']}**\n"
        text += f"   💬 @{app['telegram_username'] or 'не указан'}\n"
        text += f"   📱 {app['phone_number']}\n\n"
    
    if len(applications) > 8:
        text += f"... и еще {len(applications) - 8} участников\n\n"
    
    text += "🌐 Полный список в веб-админке"
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=get_admin_keyboard(),
        parse_mode='Markdown'
    )


def handle_admin_stats_callback(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик статистики"""
    try:
        # Получаем количество заявок
        total_count = get_applications_count()

        text = f"📊 **СТАТИСТИКА**\n\n"
        text += f"👥 Всего участников: {total_count}"

        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard(),
            parse_mode='Markdown'
        )

    except Exception as e:
        logger.error(f"Ошибка в статистике: {e}")
        bot.edit_message_text(
            "❌ Ошибка получения статистики",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard()
        )


def handle_admin_broadcast_callback(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик рассылки"""
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    broadcast_keyboard = InlineKeyboardMarkup()
    broadcast_keyboard.add(
        InlineKeyboardButton("📢 Всем участникам", callback_data="broadcast_all"),
        InlineKeyboardButton("🏆 Только победителю", callback_data="broadcast_winner")
    )
    broadcast_keyboard.add(
        InlineKeyboardButton("📋 Тестовая рассылка", callback_data="broadcast_test")
    )
    broadcast_keyboard.add(
        InlineKeyboardButton("🔙 Назад", callback_data="broadcast_back")
    )
    
    bot.edit_message_text(
        "📨 **РАССЫЛКА СООБЩЕНИЙ**\n\n"
        "Выберите тип рассылки:\n\n"
        "📢 **Всем участникам** - отправить всем зарегистрированным\n"
        "🏆 **Только победителю** - персональное сообщение\n"
        "📋 **Тестовая** - отправить только вам для проверки\n\n"
        "✍️ Отправьте ваше сообщение текстом после выбора типа",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=broadcast_keyboard,
        parse_mode='Markdown'
    )



def handle_admin_support_callback(bot: telebot.TeleBot, call: CallbackQuery):
    """Список открытых тикетов с действиями"""
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    tickets = get_open_support_tickets()

    if not tickets:
        bot.edit_message_text(
            "🆘 **ПОДДЕРЖКА**\n\nОткрытых тикетов нет",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard(),
            parse_mode='Markdown'
        )
        return

    # Берем первые 5 тикетов
    text = "🆘 **ПОДДЕРЖКА: ОТКРЫТЫЕ ТИКЕТЫ**\n\nВыберите тикет для действия:\n\n"
    kb = InlineKeyboardMarkup(row_width=2)
    for ticket in tickets[:5]:
        text += f"#{ticket['id']} • {ticket['user_name']} • {ticket['created_at']}\n"
        text += f"💬 {ticket['message'][:60]}{'...' if len(ticket['message'])>60 else ''}\n\n"
        kb.add(
            InlineKeyboardButton(f"✉️ Ответ #{ticket['id']}", callback_data=f"support_reply_{ticket['id']}"),
            InlineKeyboardButton(f"✅ Закрыть #{ticket['id']}", callback_data=f"support_close_{ticket['id']}")
        )

    kb.add(InlineKeyboardButton("🔙 Назад", callback_data="admin_back"))

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb,
        parse_mode='Markdown'
    )


def handle_admin_support_reply_action(bot: telebot.TeleBot, call: CallbackQuery):
    """Устанавливает состояние ожидания текста ответа для указанного тикета"""
    try:
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Нет прав")
            return

        ticket_id = int(call.data.replace('support_reply_', ''))
        ticket = get_support_ticket(ticket_id)
        if not ticket:
            bot.answer_callback_query(call.id, "Тикет не найден")
            return

        set_user_state(user_id, UserState.WAITING_ADMIN_REPLY)
        set_user_data(user_id, 'reply_ticket_id', ticket_id)

        reply_prompt = SUPPORT_MESSAGES['support_reply_prompt'].format(
            ticket_id=ticket_id,
            user_name=ticket['user_name'],
            username=ticket['username'] or 'не указан',
            message=ticket['message']
        )

        bot.edit_message_text(
            reply_prompt,
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown'
        )
        bot.answer_callback_query(call.id)

    except Exception as e:
        logger.error(f"Ошибка в support reply action: {e}")
        bot.answer_callback_query(call.id, "Ошибка")


def handle_admin_support_close_action(bot: telebot.TeleBot, call: CallbackQuery):
    """Закрывает тикет без ответа"""
    try:
        user_id = call.from_user.id
        if not is_admin(user_id):
            bot.answer_callback_query(call.id, "Нет прав")
            return

        ticket_id = int(call.data.replace('support_close_', ''))
        ok = reply_support_ticket(ticket_id, admin_reply='')
        if not ok:
            bot.answer_callback_query(call.id, "Не удалось")
            return

        bot.answer_callback_query(call.id, f"Тикет #{ticket_id} закрыт")
        # Обновим список
        handle_admin_support_callback(bot, call)

    except Exception as e:
        logger.error(f"Ошибка в support close action: {e}")
        bot.answer_callback_query(call.id, "Ошибка")

def handle_broadcast_action(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик действий рассылки"""
    action = call.data.replace("broadcast_", "")
    
    if action == "back":
        # Возвращаемся в главное админ меню
        count = get_applications_count()
        admin_text = f"👑 **АДМИН-ПАНЕЛЬ**\n\nВсего заявок: {count}"
        
        bot.edit_message_text(
            admin_text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard(),
            parse_mode='Markdown'
        )
        return
    
    # Выполняем рассылку
    try:
        applications = get_all_applications()
        
        if action == "all":
            # Переводим админа в режим ввода текста рассылки
            set_user_state(call.from_user.id, UserState.WAITING_BROADCAST_MESSAGE)
            set_user_data(call.from_user.id, 'broadcast_target', 'all')
            bot.edit_message_text(
                "✍️ Введите текст рассылки, который получат все участники:",
                call.message.chat.id,
                call.message.message_id
            )
            return
            
        elif action == "winner":
            # Сообщение победителю — попросим ввести текст
            set_user_state(call.from_user.id, UserState.WAITING_BROADCAST_MESSAGE)
            set_user_data(call.from_user.id, 'broadcast_target', 'winner')
            bot.edit_message_text(
                "✍️ Введите текст сообщения победителю:",
                call.message.chat.id,
                call.message.message_id
            )
            return
        
        elif action == "test":
            # Тестовая рассылка админу
            test_message = """📋 **ТЕСТОВОЕ СООБЩЕНИЕ**

Это тестовая рассылка для проверки системы.
Если вы видите это сообщение - рассылка работает корректно.

✅ Система рассылки активна"""
            
            bot.send_message(
                call.from_user.id,
                test_message,
                parse_mode='Markdown'
            )
            result_text = "✅ **ТЕСТ ЗАВЕРШЕН**\n\nТестовое сообщение отправлено вам в ЛС"

        elif action == "winner":
            # Сообщение победителю — попросим ввести текст
            set_user_state(call.from_user.id, UserState.WAITING_BROADCAST_MESSAGE)
            set_user_data(call.from_user.id, 'broadcast_target', 'winner')
            bot.edit_message_text(
                "✍️ Введите текст сообщения победителю:",
                call.message.chat.id,
                call.message.message_id
            )
            return
        
        bot.edit_message_text(
            result_text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard(),
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Ошибка в рассылке: {e}")
        bot.edit_message_text(
            "❌ **ОШИБКА РАССЫЛКИ**\n\nПроизошла ошибка при отправке сообщений",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard(),
            parse_mode='Markdown'
        )


def handle_settings_action(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик действий настроек"""
    action = call.data.replace("settings_", "")
    
    if action == "back":
        # Возвращаемся в главное админ меню
        count = get_applications_count()
        admin_text = f"👑 **АДМИН-ПАНЕЛЬ**\n\nВсего заявок: {count}"
        
        bot.edit_message_text(
            admin_text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard(),
            parse_mode='Markdown'
        )
        return
    
    elif action == "reset_winner":
        # Сбрасываем победителя
        try:
            from database.db_manager import get_db_connection
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('UPDATE applications SET is_winner = 0')
                conn.commit()
            
            bot.edit_message_text(
                "✅ **ПОБЕДИТЕЛЬ СБРОШЕН**\n\nРезультат розыгрыша отменен.\nМожно провести новый розыгрыш.",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=get_admin_keyboard(),
                parse_mode='Markdown'
            )
            logger.info(f"Администратор {call.from_user.id} сбросил результат розыгрыша")
            
        except Exception as e:
            logger.error(f"Ошибка при сбросе победителя: {e}")
            bot.edit_message_text(
                "❌ **ОШИБКА**\n\nНе удалось сбросить результат розыгрыша",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=get_admin_keyboard(),
                parse_mode='Markdown'
            )
    elif action == "back":
        # Возврат к админ-меню
        count = get_applications_count()
        admin_text = f"👑 **АДМИН-ПАНЕЛЬ**\n\nВсего заявок: {count}"
        bot.edit_message_text(
            admin_text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard(),
            parse_mode='Markdown'
        )
    
    elif action == "clear_apps":
        # Подтверждение очистки заявок
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        
        confirm_keyboard = InlineKeyboardMarkup()
        confirm_keyboard.add(
            InlineKeyboardButton("⚠️ ДА, УДАЛИТЬ ВСЕ", callback_data="settings_confirm_clear"),
            InlineKeyboardButton("❌ Отмена", callback_data="settings_back")
        )
        
        applications_count = get_applications_count()
        bot.edit_message_text(
            f"⚠️ **ПОДТВЕРДИТЕ УДАЛЕНИЕ**\n\n"
            f"Будут удалены ВСЕ заявки ({applications_count} шт.)\n"
            f"Это действие НЕОБРАТИМО!\n\n"
            f"Вы уверены?",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=confirm_keyboard,
            parse_mode='Markdown'
        )
    
    elif action == "confirm_clear":
        # Подтвержденная очистка заявок
        try:
            from database.db_manager import get_db_connection
            import os
            import glob
            
            # Удаляем все записи из БД
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM applications')
                conn.commit()
            
            # Удаляем все фотографии
            photo_files = glob.glob('photos/user_*.jpg')
            deleted_photos = 0
            for photo_file in photo_files:
                try:
                    os.remove(photo_file)
                    deleted_photos += 1
                except:
                    continue
            
            bot.edit_message_text(
                f"✅ **ОЧИСТКА ЗАВЕРШЕНА**\n\n"
                f"• Удалено заявок: {get_applications_count() if False else 'все'}\n"
                f"• Удалено фотографий: {deleted_photos}\n\n"
                f"Система готова к новому розыгрышу.",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=get_admin_keyboard(),
                parse_mode='Markdown'
            )
            logger.info(f"Администратор {call.from_user.id} очистил все заявки")
        
        except Exception as e:
            logger.error(f"Ошибка при очистке заявок: {e}")
            bot.edit_message_text(
                "❌ **ОШИБКА**\n\nНе удалось очистить заявки",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=get_admin_keyboard(),
                parse_mode='Markdown'
            )
    elif action == "cancel_clear":
        # Отмена очистки
        count = get_applications_count()
        admin_text = f"👑 **АДМИН-ПАНЕЛЬ**\n\nВсего заявок: {count}"
        bot.edit_message_text(
            admin_text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard(),
            parse_mode='Markdown'
        )
    
    elif action == "export_logs":
        # Экспорт логов
        try:
            import zipfile
            from datetime import datetime
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            zip_filename = f"exports/logs_{timestamp}.zip"
            
            # Создаем архив с логами
            with zipfile.ZipFile(zip_filename, 'w') as zipf:
                if os.path.exists('bot.log'):
                    zipf.write('bot.log', 'bot.log')
                
                # Добавляем конфигурацию (без токенов)
                config_info = f"""Система: Telegram Bot для розыгрышей
Время экспорта: {datetime.now().isoformat()}
Версия: 2.0
Участников: {get_applications_count()}
Победитель: {'Определен' if get_winner() else 'Не определен'}
"""
                zipf.writestr('system_info.txt', config_info)
            
            # Отправляем архив
            with open(zip_filename, 'rb') as zip_file:
                bot.send_document(
                    call.message.chat.id,
                    zip_file,
                    caption="📊 **ЭКСПОРТ ЛОГОВ**\n\nАрхив с системными логами и информацией",
                    parse_mode='Markdown'
                )
            
            # Удаляем временный файл
            os.remove(zip_filename)
            
            bot.edit_message_text(
                "✅ **ЛОГИ ЭКСПОРТИРОВАНЫ**\n\nФайл с логами отправлен",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=get_admin_keyboard(),
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Ошибка при экспорте логов: {e}")
            bot.edit_message_text(
                "❌ **ОШИБКА**\n\nНе удалось экспортировать логи",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=get_admin_keyboard(),
                parse_mode='Markdown'
            )
    else:
        # Неизвестное действие настроек
        count = get_applications_count()
        admin_text = f"👑 **АДМИН-ПАНЕЛЬ**\n\nВсего заявок: {count}"
        bot.edit_message_text(
            admin_text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard(),
            parse_mode='Markdown'
        )


    # Конец handle_settings_action

    
def handle_support_message_input(bot: telebot.TeleBot, message: Message):
    """Принимает сообщение пользователя для поддержки и создает тикет"""
    try:
        user_id = message.from_user.id
        text = (message.text or '').strip()
        if not text:
            bot.send_message(message.chat.id, "❌ Пустое сообщение. Опишите проблему текстом.")
            return

        ticket_id = create_support_ticket(
            user_id=user_id,
            user_name=f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip(),
            username=message.from_user.username or '',
            message=text
        )

        if not ticket_id:
            bot.send_message(message.chat.id, MESSAGES['error'])
            return

        # Сообщение пользователю
        bot.send_message(
            message.chat.id,
            SUPPORT_MESSAGES['support_sent'].format(ticket_id=ticket_id),
            parse_mode='Markdown'
        )

        # Уведомление админам
        from datetime import datetime as _dt
        admin_text = SUPPORT_MESSAGES['admin_support_new'].format(
            ticket_id=ticket_id,
            user_name=f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip(),
            username=message.from_user.username or 'не указан',
            user_id=user_id,
            timestamp=_dt.now().strftime('%d.%m.%Y %H:%M'),
            message=text
        )
        # Рассылаем всем известным админам
        all_admins = set(ADMIN_IDS) | set(RUNTIME_ADMINS)
        for admin_id in all_admins:
            try:
                bot.send_message(admin_id, admin_text, parse_mode='Markdown')
            except Exception:
                continue

        # Сбрасываем состояние
        clear_user_state(user_id)
    
    except Exception as e:
        logger.error(f"Ошибка в handle_support_message_input: {e}")
        bot.send_message(message.chat.id, MESSAGES['error'])


def handle_admin_reply_input(bot: telebot.TeleBot, message: Message):
    """Принимает текст ответа админа на тикет, отправляет пользователю"""
    try:
        user_id = message.from_user.id
        if not is_admin(user_id):
            bot.send_message(message.chat.id, MESSAGES['admin_not_authorized'])
            clear_user_state(user_id)
            return

        reply_text = (message.text or '').strip()
        if not reply_text:
            bot.send_message(message.chat.id, "❌ Пустой ответ. Введите текст ответа.")
            return

        ticket_id = get_user_data(user_id, 'reply_ticket_id')
        if not ticket_id:
            bot.send_message(message.chat.id, MESSAGES['error'])
            clear_user_state(user_id)
            return

        # Сохраняем ответ
        ok = reply_support_ticket(ticket_id, reply_text)
        if not ok:
            bot.send_message(message.chat.id, MESSAGES['error'])
            clear_user_state(user_id)
            return

        # Находим пользователя, чтобы отправить ответ
        ticket = get_support_ticket(ticket_id)
        if not ticket:
            bot.send_message(message.chat.id, "❌ Тикет не найден после обновления")
            clear_user_state(user_id)
            return

        try:
            bot.send_message(
                ticket['user_id'],
                f"💬 **ОТВЕТ ОТ АДМИНИСТРАЦИИ**\n\n{reply_text}",
                parse_mode='Markdown'
            )
        except Exception:
            pass

        bot.send_message(message.chat.id, f"✅ Ответ отправлен пользователю (тикет #{ticket_id})")
        clear_user_state(user_id)
    except Exception as e:
        logger.error(f"Ошибка в handle_admin_reply_input: {e}")
        bot.send_message(message.chat.id, MESSAGES['error'])

def handle_admin_web_info_callback(bot: telebot.TeleBot, call: CallbackQuery):
    """Информация о веб-админке"""
    base_url = get_web_base_url()
    bot.edit_message_text(
        "🌐 **ВЕБ-АДМИНКА**\n\n"
        "Откройте в браузере:\n"
        f"🔗 `{base_url}`\n\n"
        "🔐 **Возможности:**\n"
        "• Просмотр всех участников\n"
        "• Управление заявками\n"
        "• Экспорт данных\n"
        "• Просмотр фотографий\n"
        "• Статистика в реальном времени\n\n"
        "💡 Скопируйте ссылку в браузер",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=get_admin_keyboard(),
        parse_mode='Markdown'
    )


def handle_settings_clear_apps(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик очистки заявок"""
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    confirm_keyboard = InlineKeyboardMarkup()
    confirm_keyboard.add(
        InlineKeyboardButton("✅ Подтвердить", callback_data="settings_confirm_clear"),
        InlineKeyboardButton("❌ Отмена", callback_data="settings_cancel_clear")
    )

    bot.edit_message_text(
        "⚠️ **ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ**\n\n"
        "Вы уверены, что хотите удалить ВСЕ заявки?\n\n"
        "Это действие НЕОБРАТИМО!",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=confirm_keyboard,
        parse_mode='Markdown'
    )


def handle_settings_system_info(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик системной информации"""
    import os
    import sys
    from datetime import datetime

    total_count = get_applications_count()
    bot_info = bot.get_me()

    text = f"📊 **СИСТЕМНАЯ ИНФОРМАЦИЯ**\n\n"
    text += f"🤖 **Бот:** @{bot_info.username}\n"
    text += f"👥 **Участников:** {total_count}\n"
    text += f"🕒 **Время:** {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
    text += f"📁 **Папка:** {os.getcwd()}\n"

    # Отправляем как обычный текст без Markdown, чтобы избежать ошибок парсинга
    try:
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке системной информации: {e}")
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard(),
        )


def handle_settings_open_tickets(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик открытых тикетов"""
    tickets = get_open_support_tickets()

    if not tickets:
        text = "📋 **ОТКРЫТЫЕ ТИКЕТЫ**\n\n✅ Все тикеты закрыты"
    else:
        text = f"📋 **ОТКРЫТЫЕ ТИКЕТЫ ({len(tickets)})**\n\n"
        for i, ticket in enumerate(tickets[:5], 1):
            text += f"{i}. #{ticket['id']} от {ticket['user_name']}\n"
            text += f"   💬 {ticket['message'][:50]}{'...' if len(ticket['message']) > 50 else ''}\n\n"

        if len(tickets) > 5:
            text += f"... и еще {len(tickets) - 5} тикетов\n"

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=get_admin_keyboard(),
        parse_mode='Markdown'
    )


def handle_settings_close_tickets(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик закрытия всех тикетов"""
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    confirm_keyboard = InlineKeyboardMarkup()
    confirm_keyboard.add(
        InlineKeyboardButton("✅ Закрыть все", callback_data="settings_confirm_close_tickets"),
        InlineKeyboardButton("❌ Отмена", callback_data="settings_cancel_close_tickets")
    )

    tickets = get_open_support_tickets()
    count = len(tickets)

    bot.edit_message_text(
        f"🆘 **ЗАКРЫТИЕ ТИКЕТОВ**\n\n"
        f"Найдено {count} открытых тикетов.\n\n"
        "Закрыть все тикеты поддержки?",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=confirm_keyboard,
        parse_mode='Markdown'
    )


def handle_settings_confirm_clear(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик подтверждения очистки заявок"""
    from database.db_manager import DatabaseManager

    try:
        # Очищаем все заявки
        db = DatabaseManager()
        db.cursor.execute("DELETE FROM applications")
        db.connection.commit()
        db.close()

        logger.warning(f"Админ {call.from_user.id} очистил все заявки")

        bot.edit_message_text(
            "🗑 **ЗАЯВКИ УДАЛЕНЫ**\n\n"
            "Все заявки успешно удалены.\n"
            "Система готова к новому розыгрышу.",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard(),
            parse_mode='Markdown'
        )

    except Exception as e:
        logger.error(f"Ошибка при очистке заявок: {e}")
        bot.edit_message_text(
            "❌ Ошибка при удалении заявок",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard()
        )


def handle_settings_confirm_close_tickets(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик подтверждения закрытия всех тикетов"""
    from database.db_manager import DatabaseManager

    try:
        # Закрываем все открытые тикеты
        db = DatabaseManager()
        db.cursor.execute("""
            UPDATE support_tickets
            SET status = 'closed', replied_at = datetime('now')
            WHERE status = 'open'
        """)
        db.connection.commit()
        db.close()

        logger.info(f"Админ {call.from_user.id} закрыл все тикеты поддержки")

        bot.edit_message_text(
            "✅ **ТИКЕТЫ ЗАКРЫТЫ**\n\n"
            "Все открытые тикеты поддержки закрыты.",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard(),
            parse_mode='Markdown'
        )

    except Exception as e:
        logger.error(f"Ошибка при закрытии тикетов: {e}")
        bot.edit_message_text(
            "❌ Ошибка при закрытии тикетов",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard()
        )


# Старые callback handlers для совместимости
def handle_admin_list_callback(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик кнопки списка заявок в админке"""
    # Используем существующую логику из команды /список
    applications = get_all_applications()
    
    if not applications:
        bot.edit_message_text(
            MESSAGES['no_applications'],
            call.message.chat.id,
            call.message.message_id
        )
        return
    
    text = "📋 **Список заявок:**\n\n"
    for i, app in enumerate(applications[:10], 1):  # Показываем первые 10
        winner_mark = "👑 " if app['is_winner'] else ""
        text += f"{i}. {winner_mark}{app['name']} (@{app['telegram_username']})\n"
    
    if len(applications) > 10:
        text += f"\n... и еще {len(applications) - 10} заявок"
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=get_admin_keyboard(),
        parse_mode='Markdown'
    )


def handle_admin_winner_callback(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик кнопки выбора победителя в админке"""
    applications = get_all_applications()
    
    if not applications:
        bot.edit_message_text(
            MESSAGES['no_applications'],
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard()
        )
        return
    winner = get_random_winner()
    if not winner:
        bot.edit_message_text(
            "❌ Ошибка при выборе победителя",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard()
        )
        return
    
    announcement = create_winner_announcement(
        winner, len(applications), get_hash_seed()
    )
    
    bot.edit_message_text(
        announcement,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=get_winner_confirmation_keyboard(),
        parse_mode='Markdown'
    )


def handle_admin_export_callback(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик экспорта"""
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    export_keyboard = InlineKeyboardMarkup()
    export_keyboard.add(
        InlineKeyboardButton("📄 CSV", callback_data="export_csv"),
        InlineKeyboardButton("📊 Excel", callback_data="export_excel")
    )

    bot.edit_message_text(
        "📥 **ЭКСПОРТ ДАННЫХ**\n\n"
        "Выберите формат для скачивания:\n\n"
        "📄 **CSV** - универсальный формат\n"
        "📊 **Excel** - готовый файл",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=export_keyboard,
        parse_mode='Markdown'
    )


def handle_confirm_winner_callback(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик подтверждения победителя"""
    winner = get_winner()
    if winner:
        text = f"✅ Победитель подтвержден: {winner['name']} (@{winner['telegram_username']})"
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard()
        )
    else:
        bot.edit_message_text(
            "❌ Ошибка: победитель не найден",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard()
        )


def handle_select_new_winner_callback(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик повторного выбора победителя"""
    handle_admin_winner_callback(bot, call)


def handle_cancel_winner_callback(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик отмены выбора победителя"""
    count = get_applications_count()
    admin_text = f"👑 **Админ-панель**\n\nВсего заявок: {count}"
    
    bot.edit_message_text(
        admin_text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=get_admin_keyboard(),
        parse_mode='Markdown'
    )


def handle_export_csv_callback(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик экспорта в CSV"""
    try:
        applications = get_all_applications()
        file_path = export_to_csv(applications)
        
        with open(file_path, 'rb') as file:
            bot.send_document(call.message.chat.id, file)
        
        bot.edit_message_text(
            MESSAGES['export_ready'],
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard()
        )
        
        # Удаляем временный файл
        os.remove(file_path)
        
    except Exception as e:
        logger.error(f"Ошибка при экспорте CSV: {e}")
        bot.edit_message_text(
            "❌ Ошибка при создании экспорта",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard()
        )


def handle_export_excel_callback(bot: telebot.TeleBot, call: CallbackQuery):
    """Обработчик экспорта в Excel"""
    try:
        applications = get_all_applications()
        file_path = export_to_excel(applications)
        
        with open(file_path, 'rb') as file:
            bot.send_document(call.message.chat.id, file)
        
        bot.edit_message_text(
            MESSAGES['export_ready'],
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard()
        )
        
        # Удаляем временный файл
        os.remove(file_path)
        
    except Exception as e:
        logger.error(f"Ошибка при экспорте Excel: {e}")
        bot.edit_message_text(
            "❌ Ошибка при создании экспорта",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_admin_keyboard()
        )
