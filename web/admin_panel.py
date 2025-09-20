"""
Веб-админка для управления заявками
"""

import os
import logging
from functools import wraps
from datetime import datetime

from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
import telebot

from config import ADMIN_PASSWORD, PHOTOS_DIR, BOT_TOKEN
from database.db_manager import (
    get_all_applications, get_applications_page, delete_application, get_random_winner,
    get_winner, get_applications_count, get_filtered_applications_count, add_user_manually, 
    update_user, get_user_by_id,
    get_open_support_tickets, get_support_ticket, reply_support_ticket,
    count_duplicate_photo_hash, count_recent_registrations, update_risk, set_status,
    get_active_leaflet_template,
)
from utils.file_handler import export_to_csv, export_to_excel
from utils.randomizer import create_winner_announcement, get_hash_seed
from utils.anti_fraud import AntiFraudSystem
from utils.image_validation import analyze_leaflet

logger = logging.getLogger(__name__)


def create_web_app() -> Flask:
    """Создает и настраивает Flask приложение"""
    app = Flask(__name__)
    app.secret_key = 'tg_bot_admin_secret_key_2024'
    bot_sender = telebot.TeleBot(BOT_TOKEN)
    
    # Добавляем кастомный фильтр для извлечения имени файла
    @app.template_filter('basename')
    def basename_filter(path):
        return os.path.basename(path)
    
    # Добавляем кастомный фильтр для форматирования даты
    @app.template_filter('format_datetime')
    def format_datetime_filter(datetime_str):
        """Форматирует дату в читаемый вид: 20.09.2025 19:03"""
        if not datetime_str:
            return ''
        try:
            # Парсим ISO формат даты
            dt = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
            # Форматируем в удобочитаемый вид
            return dt.strftime('%d.%m.%Y %H:%M')
        except (ValueError, AttributeError):
            return datetime_str
    
    def require_auth(f):
        """Декоратор для проверки авторизации"""
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('authenticated'):
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return decorated_function
    
    
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        """Страница авторизации"""
        if request.method == 'POST':
            password = request.form.get('password')
            if password == ADMIN_PASSWORD:
                session['authenticated'] = True
                return redirect(url_for('admin_panel'))
            else:
                return render_template('login.html', error='Неверный пароль')
        
        return render_template('login.html')
    
    
    @app.route('/logout')
    def logout():
        """Выход из админки"""
        session.pop('authenticated', None)
        return redirect(url_for('login'))
    
    
    @app.route('/')
    @require_auth
    def admin_panel():
        """Главная страница админки"""
        try:
            # pagination + filters
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 100))
            risk = request.args.get('risk')  # low/medium/high/None
            status = request.args.get('status')  # approved/pending/blocked/None
            
            total_count = get_applications_count()
            applications = get_applications_page(page, per_page, risk=risk, status=status)
            list_total = get_filtered_applications_count(risk=risk, status=status) if (risk or status) else total_count
            logger.info("WEB: открыта админ-панель")
            winner = get_winner()
            # compute category stats
            stats = {
                'total': total_count,
                'status': {
                    'approved': get_filtered_applications_count(status='approved'),
                    'pending': get_filtered_applications_count(status='pending'),
                    'blocked': get_filtered_applications_count(status='blocked'),
                },
                'risk': {
                    'low': get_filtered_applications_count(risk='low'),
                    'medium': get_filtered_applications_count(risk='medium'),
                    'high': get_filtered_applications_count(risk='high'),
                }
            }
            
            # active leaflet template info
            tpl = get_active_leaflet_template() or {}
            leaflet_required = int(tpl.get('required_stickers') or 0)

            return render_template(
                'admin_panel.html',
                applications=applications,
                winner=winner,
                total_count=total_count,
                list_total=list_total,
                page=page,
                per_page=per_page,
                total_pages=(list_total + per_page - 1) // per_page,
                filter_risk=(risk or ''),
                filter_status=(status or ''),
                cat_stats=stats,
                leaflet_required=leaflet_required,
            )
            
        except Exception as e:
            logger.error(f"Ошибка в admin_panel: {e}")
            return render_template('error.html', error=str(e))
    
    
    @app.route('/api/applications')
    @require_auth
    def api_get_applications():
        """API для получения списка заявок"""
        try:
            applications = get_all_applications()
            return jsonify({
                'success': True,
                'applications': applications,
                'total': len(applications)
            })
            
        except Exception as e:
            logger.error(f"Ошибка в api_get_applications: {e}")
            return jsonify({'success': False, 'error': str(e)})
    
    
    @app.route('/api/delete_application/<int:application_id>', methods=['DELETE'])
    @require_auth
    def api_delete_application(application_id):
        """API для удаления заявки"""
        try:
            logger.info(f"WEB click: delete application {application_id}")
            success = delete_application(application_id)
            
            if success:
                logger.info(f"Заявка {application_id} удалена через веб-админку")
                return jsonify({'success': True})
            else:
                return jsonify({'success': False, 'error': 'Заявка не найдена'})
                
        except Exception as e:
            logger.error(f"Ошибка в api_delete_application: {e}")
            return jsonify({'success': False, 'error': str(e)})

    # Валидация фото повторно (например после перезаливки вручную через файловую систему)
    @app.route('/api/validate/<int:user_id>', methods=['POST'])
    @require_auth
    def api_validate_leaflet(user_id: int):
        try:
            user = get_user_by_id(user_id)
            if not user:
                return jsonify({'success': False, 'error': 'Пользователь не найден'})
            photo_path = user.get('photo_path') or ''
            if not photo_path or not os.path.exists(photo_path):
                return jsonify({'success': False, 'error': 'Фото не найдено'})
            with open(photo_path, 'rb') as f:
                photo_bytes = f.read()
            res = analyze_leaflet(photo_bytes)
            # Обновим поля в БД
            from database.db_manager import get_db_connection
            import json as _json
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute('''
                    UPDATE applications
                    SET leaflet_status = ?, stickers_count = ?, validation_notes = ?, manual_review_required = ?, photo_phash = ?
                    WHERE id = ?
                ''', (
                    res['leaflet_status'],
                    res['stickers_count'],
                    _json.dumps(res['validation_notes'], ensure_ascii=False),
                    int(res['manual_review_required']),
                    res['photo_phash'],
                    user_id
                ))
                conn.commit()
            return jsonify({'success': True, 'result': res})
        except Exception as e:
            logger.error(f"Ошибка в api_validate_leaflet: {e}")
            return jsonify({'success': False, 'error': str(e)})
    
    
    @app.route('/api/select_winner', methods=['POST'])
    @require_auth
    def api_select_winner():
        """API для выбора победителя"""
        try:
            logger.info("WEB click: select_winner")
            applications = get_all_applications()
            
            if not applications:
                return jsonify({'success': False, 'error': 'Нет заявок для розыгрыша'})
            
            winner = get_random_winner()
            
            if not winner:
                return jsonify({'success': False, 'error': 'Ошибка при выборе победителя'})
            
            # Создаем объявление
            seed = get_hash_seed()
            announcement = create_winner_announcement(winner, len(applications), seed)
            
            logger.info(f"Выбран победитель: {winner['name']} (ID: {winner['id']})")
            
            return jsonify({
                'success': True,
                'winner': winner,
                'announcement': announcement,
                'seed': seed,
                'total_participants': len(applications)
            })
            
        except Exception as e:
            logger.error(f"Ошибка в api_select_winner: {e}")
            return jsonify({'success': False, 'error': str(e)})
    
    
    @app.route('/api/export/<format>')
    @require_auth
    def api_export(format):
        """API для экспорта данных"""
        try:
            logger.info(f"WEB click: export {format}")
            applications = get_all_applications()
            
            if format == 'csv':
                file_path = export_to_csv(applications)
                mimetype = 'text/csv'
                attachment_filename = f'applications_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
            elif format == 'excel':
                file_path = export_to_excel(applications)
                mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                attachment_filename = f'applications_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
            else:
                return jsonify({'success': False, 'error': 'Неподдерживаемый формат'})
            
            # Убеждаемся, что используем правильный путь от корня проекта
            current_dir = os.getcwd()
            full_file_path = os.path.join(current_dir, file_path) if not os.path.isabs(file_path) else file_path
            
            if not os.path.exists(full_file_path):
                logger.error(f"Файл экспорта не найден: {full_file_path}")
                return jsonify({'success': False, 'error': 'Файл экспорта не найден'})
            
            logger.info(f"Создан экспорт {format}: {full_file_path}")
            
            return send_file(
                full_file_path,
                mimetype=mimetype,
                as_attachment=True,
                download_name=attachment_filename
            )
            
        except Exception as e:
            logger.error(f"Ошибка в api_export: {e}")
            return jsonify({'success': False, 'error': str(e)})
    
    
    @app.route('/photo/<filename>')
    @require_auth
    def serve_photo(filename):
        """Обслуживание фотографий"""
        try:
            # Простое решение: используем абсолютный путь от текущей директории
            current_dir = os.getcwd()
            photo_path = os.path.join(current_dir, 'photos', filename)
            
            if not os.path.exists(photo_path):
                logger.error(f"Фото не найдено: {photo_path}")
                return "Фото не найдено", 404
            
            return send_file(photo_path, mimetype='image/jpeg')
            
        except Exception as e:
            logger.error(f"Ошибка в serve_photo: {e}")
            return "Ошибка при загрузке фото", 500
    
    
    @app.route('/api/stats')
    @require_auth
    def api_get_stats():
        """API для получения статистики"""
        try:
            logger.info("WEB: stats requested")
            applications = get_all_applications()
            winner = get_winner()
            
            stats = {
                'total_applications': len(applications),
                'has_winner': winner is not None,
                'winner': winner,
                'latest_application': applications[0] if applications else None
            }
            
            return jsonify({'success': True, 'stats': stats})
            
        except Exception as e:
            logger.error(f"Ошибка в api_get_stats: {e}")
            return jsonify({'success': False, 'error': str(e)})
    
    
    @app.errorhandler(404)
    def not_found(error):
        """Обработчик 404 ошибки"""
        return render_template('error.html', error='Страница не найдена'), 404
    
    
    @app.errorhandler(500)
    def internal_error(error):
        """Обработчик 500 ошибки"""
        logger.error(f"Внутренняя ошибка сервера: {error}")
        return render_template('error.html', error='Внутренняя ошибка сервера'), 500
    
    
    @app.route('/api/add_user', methods=['POST'])
    @require_auth
    def api_add_user():
        """Добавление нового пользователя"""
        try:
            logger.info("WEB click: add_user")
            data = request.get_json()
            name = data.get('name', '').strip()
            phone = data.get('phone', '').strip()
            username = data.get('username', '').strip()
            
            if not name or not phone:
                return jsonify({'success': False, 'error': 'Имя и телефон обязательны'})
            
            # Убираем все не-цифры из телефона
            phone_digits = ''.join(filter(str.isdigit, phone))
            if len(phone_digits) < 10:
                return jsonify({'success': False, 'error': 'Некорректный номер телефона'})
            
            user_id = add_user_manually(name, phone_digits, username)
            
            if user_id:
                logger.info(f"Добавлен пользователь через админку: {name}")
                return jsonify({'success': True, 'user_id': user_id})
            else:
                return jsonify({'success': False, 'error': 'Ошибка при добавлении пользователя'})
                
        except Exception as e:
            logger.error(f"Ошибка при добавлении пользователя: {e}")
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/api/edit_user/<int:user_id>', methods=['POST'])
    @require_auth
    def api_edit_user(user_id):
        """Редактирование пользователя"""
        try:
            logger.info(f"WEB click: edit_user {user_id}")
            data = request.get_json()
            name = data.get('name', '').strip()
            phone = data.get('phone', '').strip()
            username = data.get('username', '').strip()
            
            if not name or not phone:
                return jsonify({'success': False, 'error': 'Имя и телефон обязательны'})
            
            # Убираем все не-цифры из телефона
            phone_digits = ''.join(filter(str.isdigit, phone))
            if len(phone_digits) < 10:
                return jsonify({'success': False, 'error': 'Некорректный номер телефона'})
            
            if update_user(user_id, name, phone_digits, username):
                logger.info(f"Обновлен пользователь через админку ID: {user_id}")
                return jsonify({'success': True})
            else:
                return jsonify({'success': False, 'error': 'Пользователь не найден'})
                
        except Exception as e:
            logger.error(f"Ошибка при обновлении пользователя: {e}")
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/api/get_user/<int:user_id>', methods=['GET'])
    @require_auth
    def api_get_user(user_id):
        """Получение данных пользователя"""
        try:
            logger.info(f"WEB click: get_user {user_id}")
            user = get_user_by_id(user_id)
            if user:
                return jsonify({'success': True, 'user': user})
            else:
                return jsonify({'success': False, 'error': 'Пользователь не найден'})
                
        except Exception as e:
            logger.error(f"Ошибка при получении пользователя: {e}")
            return jsonify({'success': False, 'error': str(e)})

    # Поддержка: список тикетов
    @app.route('/api/support/tickets', methods=['GET'])
    @require_auth
    def api_support_tickets():
        try:
            logger.info("WEB: load support tickets")
            status = request.args.get('status', 'open')
            tickets = get_open_support_tickets()
            # TODO: добавить загрузку закрытых тикетов при необходимости
            return jsonify({'success': True, 'tickets': tickets})
        except Exception as e:
            logger.error(f"Ошибка в api_support_tickets: {e}")
            return jsonify({'success': False, 'error': str(e)})

    # Поддержка: получить один тикет
    @app.route('/api/support/ticket/<int:ticket_id>', methods=['GET'])
    @require_auth
    def api_support_ticket(ticket_id: int):
        try:
            logger.info(f"WEB: get support ticket {ticket_id}")
            ticket = get_support_ticket(ticket_id)
            if not ticket:
                return jsonify({'success': False, 'error': 'Тикет не найден'})
            return jsonify({'success': True, 'ticket': ticket})
        except Exception as e:
            logger.error(f"Ошибка в api_support_ticket: {e}")
            return jsonify({'success': False, 'error': str(e)})

    # Поддержка: ответить на тикет
    @app.route('/api/support/reply', methods=['POST'])
    @require_auth
    def api_support_reply():
        try:
            logger.info("WEB click: support reply")
            data = request.get_json()
            ticket_id = int(data.get('ticket_id', 0))
            reply_text = (data.get('reply_text') or '').strip()
            if not ticket_id or not reply_text:
                return jsonify({'success': False, 'error': 'Неверные данные'})

            ok = reply_support_ticket(ticket_id, reply_text)
            if not ok:
                return jsonify({'success': False, 'error': 'Не удалось обновить тикет'})

            ticket = get_support_ticket(ticket_id)
            if ticket:
                try:
                    bot_sender.send_message(
                        ticket['user_id'],
                        f"💬 Ответ от поддержки:\n\n{reply_text}"
                    )
                except Exception as send_err:
                    logger.warning(f"Не удалось отправить ответ пользователю: {send_err}")

            return jsonify({'success': True})
        except Exception as e:
            logger.error(f"Ошибка в api_support_reply: {e}")
            return jsonify({'success': False, 'error': str(e)})

    # Поддержка: закрыть тикет
    @app.route('/api/support/close', methods=['POST'])
    @require_auth
    def api_support_close():
        try:
            logger.info("WEB click: support close")
            data = request.get_json()
            ticket_id = int(data.get('ticket_id', 0))
            if not ticket_id:
                return jsonify({'success': False, 'error': 'Неверные данные'})

            ok = reply_support_ticket(ticket_id, '')
            if not ok:
                return jsonify({'success': False, 'error': 'Не удалось закрыть тикет'})

            ticket = get_support_ticket(ticket_id)
            if ticket:
                try:
                    bot_sender.send_message(
                        ticket['user_id'],
                        "✅ Ваш тикет поддержки закрыт. Если проблема не решена — создайте новый."
                    )
                except Exception as send_err:
                    logger.warning(f"Не удалось уведомить пользователя о закрытии: {send_err}")

            return jsonify({'success': True})
        except Exception as e:
            logger.error(f"Ошибка в api_support_close: {e}")
            return jsonify({'success': False, 'error': str(e)})

    # Антифрод: получить текущий риск из БД
    @app.route('/api/risk/get/<int:user_id>', methods=['GET'])
    @require_auth
    def api_risk_get(user_id: int):
        try:
            user = get_user_by_id(user_id)
            if not user:
                return jsonify({'success': False, 'error': 'Пользователь не найден'})

            # Возвращаем данные из БД
            risk_score = user.get('risk_score', 0)
            risk_level = user.get('risk_level', 'low')
            risk_details_str = user.get('risk_details', '')
            
            details = []
            if risk_details_str:
                try:
                    import json as _json
                    details = _json.loads(risk_details_str)
                    if not isinstance(details, list):
                        details = []
                except Exception as e:
                    logger.warning(f"Не удалось распарсить risk_details для пользователя {user_id}: {e}")
                    details = []
            
            # Если деталей нет, но есть риск - создаем заглушку
            if not details and risk_score > 0:
                details = [{
                    'name': 'legacy_risk',
                    'passed': False,
                    'impact': risk_score,
                    'message': f'Старые данные (детали недоступны). Общий риск: {risk_score}/100'
                }]

            return jsonify({
                'success': True, 
                'risk_score': risk_score, 
                'risk_level': risk_level, 
                'details': details
            })
        except Exception as e:
            logger.error(f"Ошибка в api_risk_get: {e}")
            return jsonify({'success': False, 'error': str(e)})

    # Антифрод: пересчитать риск
    @app.route('/api/risk/recompute/<int:user_id>', methods=['POST'])
    @require_auth
    def api_risk_recompute(user_id: int):
        try:
            user = get_user_by_id(user_id)
            if not user:
                return jsonify({'success': False, 'error': 'Пользователь не найден'})

            antifraud = AntiFraudSystem()
            participant = {
                'name': user['name'],
                'phone_number': user['phone_number'],
                'telegram_username': user['telegram_username'],
                'telegram_id': user['telegram_id'],
                'photo_hash': user['photo_hash'] or '',
            }
            context = {
                'is_telegram_id_unique': True,  # в админке не проверяем уникальность повторно
                'duplicate_photo_count': count_duplicate_photo_hash(user['photo_hash'] or ''),
                'recent_registrations_60s': count_recent_registrations(60),
            }
            score, level, details = antifraud.calculate_risk_score(participant, context)
            import json as _json
            update_risk(user_id, score, level, _json.dumps(details, ensure_ascii=False))
            return jsonify({'success': True, 'risk_score': score, 'risk_level': level, 'details': details})
        except Exception as e:
            logger.error(f"Ошибка в api_risk_recompute: {e}")
            return jsonify({'success': False, 'error': str(e)})

    # Антифрод: установить статус
    @app.route('/api/risk/status/<int:user_id>', methods=['POST'])
    @require_auth
    def api_risk_set_status(user_id: int):
        try:
            data = request.get_json()
            status = (data.get('status') or '').strip()
            if status not in ('approved', 'blocked', 'pending'):
                return jsonify({'success': False, 'error': 'Неверный статус'})
            ok = set_status(user_id, status)
            return jsonify({'success': ok})
        except Exception as e:
            logger.error(f"Ошибка в api_risk_set_status: {e}")
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/api/clear_database', methods=['POST'])
    @require_auth
    def api_clear_database():
        """API: обычная очистка базы данных"""
        try:
            from database.db_manager import clear_all_data, get_all_applications
            
            # Получаем количество записей до очистки
            apps_before = len(get_all_applications())
            logger.warning(f"НАЧИНАЕМ ОЧИСТКУ БД: найдено {apps_before} заявок")
            
            # Очищаем БД
            success = clear_all_data()
            
            if success:
                # Проверяем после очистки
                apps_after = len(get_all_applications())
                logger.warning(f"ОЧИСТКА ЗАВЕРШЕНА: осталось {apps_after} заявок")
                
                if apps_after == 0:
                    return jsonify({
                        "success": True, 
                        "message": f"База данных успешно очищена. Удалено {apps_before} записей."
                    })
                else:
                    return jsonify({
                        "success": False, 
                        "error": f"Очистка не полная: удалено {apps_before - apps_after} из {apps_before} записей. Попробуйте принудительную очистку."
                    })
            else:
                return jsonify({"success": False, "error": "Ошибка при очистке базы данных"})
                
        except Exception as e:
            logger.error(f"Ошибка очистки базы данных: {e}")
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/force_clear_database', methods=['POST'])
    @require_auth
    def api_force_clear_database():
        """API: принудительная очистка базы данных"""
        try:
            from database.db_manager import force_clear_all_data, get_all_applications
            
            # Получаем количество записей до очистки
            apps_before = len(get_all_applications())
            logger.warning(f"НАЧИНАЕМ ПРИНУДИТЕЛЬНУЮ ОЧИСТКУ БД: найдено {apps_before} заявок")
            
            # Принудительно очищаем БД
            success = force_clear_all_data()
            
            if success:
                return jsonify({
                    "success": True, 
                    "message": f"База данных принудительно очищена. Удалено {apps_before} записей."
                })
            else:
                return jsonify({"success": False, "error": "Ошибка при принудительной очистке базы данных"})
                
        except Exception as e:
            logger.error(f"Ошибка принудительной очистки базы данных: {e}")
            return jsonify({"success": False, "error": str(e)})

    return app
