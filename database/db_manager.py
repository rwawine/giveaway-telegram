"""
Менеджер базы данных для Telegram бота
"""

import sqlite3
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from functools import wraps
DATABASE_PATH = 'applications.db'

logger = logging.getLogger(__name__)


def db_retry(max_retries=3, delay=0.1):
    """Декоратор для повтора операций с БД при блокировке"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    if "database is locked" in str(e) and attempt < max_retries - 1:
                        wait_time = delay * (2 ** attempt)  # Экспоненциальная задержка
                        logger.warning(f"БД заблокирована, попытка {attempt + 1}/{max_retries}, ждем {wait_time:.2f}s")
                        time.sleep(wait_time)
                        continue
                    raise
                except Exception as e:
                    logger.error(f"Ошибка в {func.__name__}: {e}")
                    raise
            return None
        return wrapper
    return decorator


def init_wal_mode():
    """Инициализирует WAL режим для базы данных"""
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=10000")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA mmap_size=134217728")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.close()
        logger.info("База данных настроена для высокой производительности")
    except Exception as e:
        logger.error(f"Ошибка настройки WAL режима: {e}")


def init_database():
    """Инициализация базы данных"""
    try:
        # Сначала инициализируем WAL режим для лучшей производительности
        init_wal_mode()
        logger.info("WAL режим инициализирован")
        # Создаем папку для базы данных только если путь содержит папки
        if os.path.dirname(DATABASE_PATH):
            os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
        
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            
            # Создаем таблицу заявок
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    phone_number TEXT NOT NULL UNIQUE,
                    telegram_username TEXT,
                    telegram_id INTEGER NOT NULL UNIQUE,
                    photo_path TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    is_winner INTEGER DEFAULT 0,
                    photo_hash TEXT,
                    risk_score INTEGER DEFAULT 0,
                    risk_level TEXT DEFAULT 'low',
                    risk_details TEXT,
                    status TEXT DEFAULT 'pending', -- pending/approved/blocked
                    -- Новые поля для системы валидации лифлетов
                    participant_number INTEGER UNIQUE,
                    leaflet_status TEXT DEFAULT 'pending', -- pending/incomplete/duplicate/approved/rejected
                    stickers_count INTEGER DEFAULT 0,
                    validation_notes TEXT,
                    manual_review_required INTEGER DEFAULT 0,
                    photo_phash TEXT
                )
            ''')
            
            # Создаем таблицу для тикетов поддержки
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS support_tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    user_name TEXT NOT NULL,
                    username TEXT,
                    message TEXT NOT NULL,
                    admin_reply TEXT,
                    status TEXT DEFAULT 'open',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    replied_at TEXT
                )
            ''')

            # Таблица шаблонов лифлетов
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS leaflet_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    required_stickers INTEGER DEFAULT 5,
                    template_image_path TEXT,
                    active_from TEXT,
                    active_until TEXT,
                    validation_zones TEXT -- JSON массив с зонами [{x,y,w,h}] в относительных долях (0..1)
                )
            ''')
            
            # Миграция: добавляем недостающие колонки для антифрода и валидации
            cursor.execute("PRAGMA table_info(applications)")
            existing_cols = {row[1] for row in cursor.fetchall()}
            migrations = []
            if 'photo_hash' not in existing_cols:
                migrations.append("ALTER TABLE applications ADD COLUMN photo_hash TEXT")
            if 'risk_score' not in existing_cols:
                migrations.append("ALTER TABLE applications ADD COLUMN risk_score INTEGER DEFAULT 0")
            if 'risk_level' not in existing_cols:
                migrations.append("ALTER TABLE applications ADD COLUMN risk_level TEXT DEFAULT 'low'")
            if 'risk_details' not in existing_cols:
                migrations.append("ALTER TABLE applications ADD COLUMN risk_details TEXT")
            if 'status' not in existing_cols:
                migrations.append("ALTER TABLE applications ADD COLUMN status TEXT DEFAULT 'pending'")
            if 'participant_number' not in existing_cols:
                # Нельзя добавить UNIQUE колонку через ALTER TABLE в SQLite → добавляем без ограничения
                migrations.append("ALTER TABLE applications ADD COLUMN participant_number INTEGER")
            if 'leaflet_status' not in existing_cols:
                migrations.append("ALTER TABLE applications ADD COLUMN leaflet_status TEXT DEFAULT 'pending'")
            if 'stickers_count' not in existing_cols:
                migrations.append("ALTER TABLE applications ADD COLUMN stickers_count INTEGER DEFAULT 0")
            if 'validation_notes' not in existing_cols:
                migrations.append("ALTER TABLE applications ADD COLUMN validation_notes TEXT")
            if 'manual_review_required' not in existing_cols:
                migrations.append("ALTER TABLE applications ADD COLUMN manual_review_required INTEGER DEFAULT 0")
            if 'photo_phash' not in existing_cols:
                migrations.append("ALTER TABLE applications ADD COLUMN photo_phash TEXT")
            for sql in migrations:
                cursor.execute(sql)
            if migrations:
                conn.commit()
            logger.info("База данных инициализирована успешно")
            
            # Гарантируем наличие дефолтного шаблона лифлета
            try:
                cursor.execute('SELECT COUNT(1) FROM leaflet_templates')
                cnt = cursor.fetchone()[0] or 0
                if cnt == 0:
                    cursor.execute('''
                        INSERT INTO leaflet_templates (name, required_stickers, template_image_path, active_from, active_until, validation_zones)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (
                        'Стандартный', 5, '', None, None,
                        '[{"x":0.10,"y":0.15,"w":0.18,"h":0.18},{"x":0.41,"y":0.15,"w":0.18,"h":0.18},{"x":0.72,"y":0.15,"w":0.18,"h":0.18},{"x":0.25,"y":0.52,"w":0.18,"h":0.18},{"x":0.56,"y":0.52,"w":0.18,"h":0.18}]'
                    ))
                    conn.commit()
            except Exception:
                # Не критично, просто логируем
                logger.warning("Не удалось создать дефолтный шаблон лифлетов")

            # Создаем индексы для быстрого поиска (после миграций, чтобы все колонки уже существовали)
            try:
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_telegram_id ON applications(telegram_id)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_phone_number ON applications(phone_number)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_is_winner ON applications(is_winner)')
                # Уникальность participant_number обеспечиваем через уникальный индекс
                cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_participant_number ON applications(participant_number)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_status ON applications(status)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_leaflet_status ON applications(leaflet_status)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_photo_phash ON applications(photo_phash)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_support_user_id ON support_tickets(user_id)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_support_status ON support_tickets(status)')
                conn.commit()
            except Exception as idx_err:
                logger.warning(f"Не удалось создать некоторые индексы: {idx_err}")

    except Exception as e:
        logger.error(f"Ошибка при инициализации базы данных: {e}")
        raise


@contextmanager
def get_db_connection():
    """Контекстный менеджер для работы с базой данных с оптимизацией для конкурентного доступа"""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=30.0)  # Увеличиваем таймаут до 30 секунд
        conn.row_factory = sqlite3.Row  # Для работы с результатами как со словарями
        
        # Настройки для высокой производительности и конкурентного доступа
        conn.execute("PRAGMA journal_mode=WAL")  # WAL режим для лучшей конкурентности
        conn.execute("PRAGMA synchronous=NORMAL")  # Балансируем между скоростью и надежностью  
        conn.execute("PRAGMA cache_size=10000")  # Увеличиваем кэш до 10MB
        conn.execute("PRAGMA temp_store=MEMORY")  # Временные данные в памяти
        conn.execute("PRAGMA mmap_size=134217728")  # 128MB memory mapping
        conn.execute("PRAGMA busy_timeout=30000")  # 30 секунд на попытки доступа
        
        yield conn
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Ошибка при работе с базой данных: {e}")
        raise
    finally:
        if conn:
            conn.close()


def count_duplicate_photo_hash(photo_hash: str) -> int:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(1) FROM applications WHERE photo_hash = ?', (photo_hash,))
            cnt = cursor.fetchone()[0]
            return max(0, (cnt or 0) - 1)  # исключаем текущего, если уже есть
    except Exception as e:
        logger.error(f"Ошибка при подсчете дубликатов фото: {e}")
        return 0


def get_application_by_telegram_id(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает заявку по telegram_id"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, name, phone_number, telegram_username, telegram_id,
                       photo_path, timestamp, is_winner, photo_hash, risk_score, risk_level, risk_details, status,
                       participant_number, leaflet_status, stickers_count, validation_notes, manual_review_required, photo_phash
                FROM applications
                WHERE telegram_id = ?
            ''', (telegram_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return {
                'id': row[0],
                'name': row[1],
                'phone_number': row[2],
                'telegram_username': row[3],
                'telegram_id': row[4],
                'photo_path': row[5],
                'timestamp': row[6],
                'is_winner': bool(row[7]),
                'photo_hash': row[8],
                'risk_score': row[9] or 0,
                'risk_level': row[10] or 'low',
                'risk_details': row[11] or '',
                'status': row[12] or 'pending',
                'participant_number': row[13],
                'leaflet_status': row[14] or 'pending',
                'stickers_count': row[15] or 0,
                'validation_notes': row[16] or '',
                'manual_review_required': bool(row[17] or 0),
                'photo_phash': row[18] or ''
            }
    except Exception as e:
        logger.error(f"Ошибка при получении заявки по telegram_id: {e}")
        return None


@db_retry(max_retries=5, delay=0.2)
def assign_next_participant_number(application_id: int) -> Optional[int]:
    """Присваивает следующий уникальный participant_number, начиная с 1001"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT MAX(participant_number) FROM applications')
            max_num = cursor.fetchone()[0]
            next_num = 1001 if not max_num else int(max_num) + 1
            cursor.execute(
                'UPDATE applications SET participant_number = ? WHERE id = ? AND participant_number IS NULL',
                (next_num, application_id)
            )
            if cursor.rowcount > 0:
                conn.commit()
                return next_num
            return None
    except Exception as e:
        logger.error(f"Ошибка при присвоении номера участника: {e}")
        return None


def count_similar_photo_phash(photo_phash: str, max_hamming_distance: int = 5) -> int:
    """Подсчитывает количество похожих фото по perceptual hash (aHash), исключая точное совпадение по id
    Примечание: простая реализация — загружает все pHash и считает в Python.
    """
    try:
        if not photo_phash:
            return 0
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT photo_phash FROM applications WHERE photo_phash IS NOT NULL AND photo_phash != ""')
            rows = cursor.fetchall()
            def hamming(a: str, b: str) -> int:
                try:
                    return bin(int(a, 16) ^ int(b, 16)).count('1')
                except Exception:
                    return 64
            cnt = 0
            for (phash,) in rows:
                if not phash:
                    continue
                if hamming(photo_phash, phash) <= max_hamming_distance:
                    cnt += 1
            return cnt
    except Exception as e:
        logger.error(f"Ошибка при подсчете похожих pHash: {e}")
        return 0


def get_active_leaflet_template() -> Optional[Dict[str, Any]]:
    """Возвращает активный шаблон лифлета (первый попавшийся в интервале дат или любой)."""
    try:
        now = datetime.now().isoformat(timespec='seconds')
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, name, required_stickers, template_image_path, active_from, active_until, validation_zones
                FROM leaflet_templates
                WHERE (active_from IS NULL OR active_from <= ?)
                  AND (active_until IS NULL OR active_until >= ?)
                ORDER BY id DESC
                LIMIT 1
            ''', (now, now))
            row = cursor.fetchone()
            if not row:
                cursor.execute('SELECT id, name, required_stickers, template_image_path, active_from, active_until, validation_zones FROM leaflet_templates ORDER BY id DESC LIMIT 1')
                row = cursor.fetchone()
                if not row:
                    return None
            return {
                'id': row[0],
                'name': row[1],
                'required_stickers': row[2] or 0,
                'template_image_path': row[3] or '',
                'active_from': row[4],
                'active_until': row[5],
                'validation_zones': row[6] or '[]'
            }
    except Exception as e:
        logger.error(f"Ошибка получения активного шаблона: {e}")
        return None


def count_recent_registrations(seconds: int = 60) -> int:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(1)
                FROM applications
                WHERE datetime(timestamp) >= datetime('now', ?)
            """, (f'-{seconds} seconds',))
            return cursor.fetchone()[0] or 0
    except Exception as e:
        logger.error(f"Ошибка при подсчете недавних регистраций: {e}")
        return 0


@db_retry(max_retries=3, delay=0.1)
def update_risk(application_id: int, risk_score: int, risk_level: str, risk_details: str) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE applications
                SET risk_score = ?, risk_level = ?, risk_details = ?
                WHERE id = ?
            ''', (risk_score, risk_level, risk_details, application_id))
            conn.commit()
            return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Ошибка обновления риска: {e}")
        return False


@db_retry(max_retries=3, delay=0.1)
def set_status(application_id: int, status: str) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE applications SET status = ? WHERE id = ?', (status, application_id))
            if cursor.rowcount > 0 and status == 'approved':
                # Автоприсваиваем номер участника при одобрении
                try:
                    assign_next_participant_number(application_id)
                except Exception:
                    pass
            conn.commit()
            return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Ошибка обновления статуса: {e}")
        return False


@db_retry(max_retries=5, delay=0.2)
def save_application(name: str, phone_number: str, telegram_username: str, 
                    telegram_id: int, photo_path: str, photo_hash: str = "",
                    risk_score: int = 0, risk_level: str = "low", risk_details: str = "",
                    status: str = "pending",
                    participant_number: Optional[int] = None,
                    leaflet_status: str = "pending",
                    stickers_count: int = 0,
                    validation_notes: str = "",
                    manual_review_required: int = 0,
                    photo_phash: str = "") -> bool:
    """
    Сохраняет заявку в базу данных
    
    Args:
        name: Имя участника
        phone_number: Номер телефона
        telegram_username: Telegram username
        telegram_id: Telegram ID
        photo_path: Путь к фото
        
    Returns:
        bool: True если заявка сохранена успешно
    """
    try:
        timestamp = datetime.now().isoformat()
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO applications (
                    name, phone_number, telegram_username, telegram_id, photo_path, timestamp,
                    photo_hash, risk_score, risk_level, risk_details, status,
                    participant_number, leaflet_status, stickers_count, validation_notes, manual_review_required, photo_phash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                name, phone_number, telegram_username, telegram_id, photo_path, timestamp,
                photo_hash, risk_score, risk_level, risk_details, status,
                participant_number, leaflet_status, stickers_count, validation_notes, manual_review_required, photo_phash
            ))

            app_id = cursor.lastrowid
            # Если статус сразу approved — присвоим участнику номер, если ещё не задан
            if status == 'approved' and participant_number is None:
                try:
                    assign_next_participant_number(app_id)
                except Exception:
                    pass

            conn.commit()
            logger.info(f"Создана заявка для пользователя {name} (ID: {telegram_id}, app_id: {app_id})")
            return True
            
    except sqlite3.IntegrityError as e:
        if "phone_number" in str(e):
            logger.warning(f"Попытка создать заявку с существующим номером телефона: {phone_number}")
        elif "telegram_id" in str(e):
            logger.warning(f"Попытка создать заявку с существующим Telegram ID: {telegram_id}")
        else:
            logger.error(f"Ошибка целостности при сохранении заявки: {e}")
        return False
    except Exception as e:
        logger.error(f"Ошибка при сохранении заявки: {e}")
        return False


def delete_application(application_id: int) -> bool:
    """
    Удаляет заявку по ID
    
    Args:
        application_id: ID заявки для удаления
        
    Returns:
        bool: True если заявка удалена успешно
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM applications WHERE id = ?', (application_id,))
            
            if cursor.rowcount > 0:
                conn.commit()
                logger.info(f"Удалена заявка с ID: {application_id}")
                return True
            else:
                logger.warning(f"Заявка с ID {application_id} не найдена")
                return False
                
    except Exception as e:
        logger.error(f"Ошибка при удалении заявки: {e}")
        return False


def application_exists(telegram_id: int, phone_number: str = None) -> bool:
    """
    Проверяет существование заявки по Telegram ID или номеру телефона
    
    Args:
        telegram_id: Telegram ID пользователя
        phone_number: Номер телефона (опционально)
    
    Returns:
        bool: True если заявка существует
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            if phone_number:
                cursor.execute(
                    'SELECT id FROM applications WHERE telegram_id = ? OR phone_number = ?',
                    (telegram_id, phone_number)
                )
            else:
                cursor.execute(
                    'SELECT id FROM applications WHERE telegram_id = ?',
                    (telegram_id,)
                )
            
            return cursor.fetchone() is not None
            
    except Exception as e:
        logger.error(f"Ошибка при проверке существования заявки: {e}")
        return False


def get_all_applications():
    """Получает все заявки из базы данных"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, name, phone_number, telegram_username, telegram_id,
                       photo_path, timestamp, is_winner, photo_hash, risk_score, risk_level, risk_details, status,
                       participant_number, leaflet_status, stickers_count, validation_notes, manual_review_required, photo_phash
                FROM applications
                ORDER BY datetime(timestamp) DESC
            ''')
            
            applications = []
            for row in cursor.fetchall():
                applications.append({
                    'id': row[0],
                    'name': row[1],
                    'phone_number': row[2],
                    'telegram_username': row[3],
                    'telegram_id': row[4],
                    'photo_path': row[5],
                    'timestamp': row[6],
                    'is_winner': bool(row[7]),
                    'photo_hash': row[8],
                    'risk_score': row[9] or 0,
                    'risk_level': row[10] or 'low',
                    'risk_details': row[11] or '',
                    'status': row[12] or 'pending',
                    'participant_number': row[13],
                    'leaflet_status': row[14] or 'pending',
                    'stickers_count': row[15] or 0,
                    'validation_notes': row[16] or '',
                    'manual_review_required': bool(row[17] or 0),
                    'photo_phash': row[18] or ''
                })
            
            return applications
            
    except Exception as e:
        logger.error(f"Ошибка при получении заявок: {e}")
        return []


def get_applications_page(page: int, per_page: int, risk: str = None, status: str = None):
    """Возвращает страницу заявок (пагинация) с необязательными фильтрами"""
    try:
        page = max(1, int(page or 1))
        per_page = max(1, int(per_page or 100))
        offset = (page - 1) * per_page

        with get_db_connection() as conn:
            cursor = conn.cursor()
            where = []
            params = []
            if status and status in ("approved", "pending", "blocked"):
                where.append("COALESCE(status, 'pending') = ?")
                params.append(status)
            if risk and risk in ("low", "medium", "high"):
                if risk == "low":
                    where.append("(COALESCE(risk_score, 0) <= 30)")
                elif risk == "medium":
                    where.append("(COALESCE(risk_score, 0) > 30 AND COALESCE(risk_score, 0) <= 70)")
                else:
                    where.append("(COALESCE(risk_score, 0) > 70)")

            sql = (
                "SELECT id, name, phone_number, telegram_username, telegram_id, "
                "photo_path, timestamp, is_winner, photo_hash, risk_score, risk_level, risk_details, status, "
                "participant_number, leaflet_status, stickers_count, validation_notes, manual_review_required, photo_phash "
                "FROM applications "
            )
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY datetime(timestamp) DESC LIMIT ? OFFSET ?"
            params.extend([per_page, offset])
            cursor.execute(sql, tuple(params))

            applications = []
            for row in cursor.fetchall():
                applications.append({
                    'id': row[0],
                    'name': row[1],
                    'phone_number': row[2],
                    'telegram_username': row[3],
                    'telegram_id': row[4],
                    'photo_path': row[5],
                    'timestamp': row[6],
                    'is_winner': bool(row[7]),
                    'photo_hash': row[8],
                    'risk_score': row[9] or 0,
                    'risk_level': row[10] or 'low',
                    'risk_details': row[11] or '',
                    'status': row[12] or 'pending',
                    'participant_number': row[13],
                    'leaflet_status': row[14] or 'pending',
                    'stickers_count': row[15] or 0,
                    'validation_notes': row[16] or '',
                    'manual_review_required': bool(row[17] or 0),
                    'photo_phash': row[18] or ''
                })

            return applications
    except Exception as e:
        logger.error(f"Ошибка при получении страницы заявок: {e}")
        return []

def get_random_winner():
    """Выбирает случайного победителя из всех заявок"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Сначала сбрасываем всех предыдущих победителей
            cursor.execute('UPDATE applications SET is_winner = 0')
            
            # Выбираем случайного участника (исключая заблокированных)
            cursor.execute('''
                SELECT id FROM applications 
                WHERE COALESCE(status, 'pending') != 'blocked'
                ORDER BY RANDOM() 
                LIMIT 1
            ''')
            
            result = cursor.fetchone()
            if not result:
                return None
                
            winner_id = result[0]
            
            # Устанавливаем его как победителя
            cursor.execute('UPDATE applications SET is_winner = 1 WHERE id = ?', (winner_id,))
            
            # Получаем информацию о победителе
            cursor.execute('''
                SELECT name, phone_number, telegram_username, telegram_id, photo_path
                FROM applications 
                WHERE id = ?
            ''', (winner_id,))
            
            winner_row = cursor.fetchone()
            conn.commit()
            
            if winner_row:
                winner_info = {
                    'id': winner_id,
                    'name': winner_row[0],
                    'phone_number': winner_row[1],
                    'telegram_username': winner_row[2],
                    'telegram_id': winner_row[3],
                    'photo_path': winner_row[4]
                }
                logger.info(f"Выбран победитель: {winner_info['name']} (ID: {winner_info['telegram_id']})")
                return winner_info
            
            return None
            
    except Exception as e:
        logger.error(f"Ошибка при выборе победителя: {e}")
        return None


def get_winner():
    """Получает текущего победителя"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, name, phone_number, telegram_username, telegram_id, photo_path, timestamp
                FROM applications 
                WHERE is_winner = 1
                LIMIT 1
            ''')
            
            row = cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'name': row[1],
                    'phone_number': row[2],
                    'telegram_username': row[3],
                    'telegram_id': row[4],
                    'photo_path': row[5],
                    'timestamp': row[6]
                }
            return None
            
    except Exception as e:
        logger.error(f"Ошибка при получении победителя: {e}")
        return None


def reset_winner():
    """Сбрасывает текущего победителя"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE applications SET is_winner = 0')
            conn.commit()
            logger.info("Победитель сброшен")
            return True
            
    except Exception as e:
        logger.error(f"Ошибка при сбросе победителя: {e}")
        return False


def get_applications_stats():
    """Получает статистику заявок"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Общее количество заявок
            cursor.execute('SELECT COUNT(*) FROM applications')
            total_applications = cursor.fetchone()[0]
            
            # Заявки за сегодня
            today = datetime.now().date().isoformat()
            cursor.execute('SELECT COUNT(*) FROM applications WHERE date(timestamp) = ?', (today,))
            today_count = cursor.fetchone()[0]
            
            # Заявки за эту неделю
            week_start = (datetime.now().date() - timedelta(days=datetime.now().weekday())).isoformat()
            cursor.execute('SELECT COUNT(*) FROM applications WHERE date(timestamp) >= ?', (week_start,))
            week_count = cursor.fetchone()[0]
            
            # Заявки за этот месяц
            month_start = datetime.now().replace(day=1).date().isoformat()
            cursor.execute('SELECT COUNT(*) FROM applications WHERE date(timestamp) >= ?', (month_start,))
            month_count = cursor.fetchone()[0]
            
            # Есть ли победитель
            cursor.execute('SELECT COUNT(*) FROM applications WHERE is_winner = 1')
            winner_selected = cursor.fetchone()[0] > 0
            
            return {
                'total_applications': total_applications,
                'today': today_count,
                'this_week': week_count,
                'this_month': month_count,
                'winner_selected': winner_selected
            }
            
    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {e}")
        return {'total_applications': 0, 'today': 0, 'this_week': 0, 'this_month': 0, 'winner_selected': False}


def get_applications_count():
    """Получает общее количество заявок"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM applications')
            return cursor.fetchone()[0]
    except Exception as e:
        logger.error(f"Ошибка при получении количества заявок: {e}")
        return 0


def get_filtered_applications_count(risk: str = None, status: str = None) -> int:
    """Количество заявок с учетом фильтров по риску и статусу"""
    try:
        where = []
        params = []
        # status filter
        if status and status in ("approved", "pending", "blocked"):
            where.append("COALESCE(status, 'pending') = ?")
            params.append(status)
        # risk filter
        if risk and risk in ("low", "medium", "high"):
            if risk == "low":
                where.append("(COALESCE(risk_score, 0) <= 30)")
            elif risk == "medium":
                where.append("(COALESCE(risk_score, 0) > 30 AND COALESCE(risk_score, 0) <= 70)")
            else:
                where.append("(COALESCE(risk_score, 0) > 70)")

        sql = "SELECT COUNT(*) FROM applications"
        if where:
            sql += " WHERE " + " AND ".join(where)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, tuple(params))
            return cursor.fetchone()[0] or 0
    except Exception as e:
        logger.error(f"Ошибка при получении количества (фильтр): {e}")
        return 0


def create_support_ticket(user_id: int, user_name: str, username: str, message: str) -> int:
    """Создает тикет поддержки"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO support_tickets (user_id, user_name, username, message)
                VALUES (?, ?, ?, ?)
            """, (user_id, user_name, username, message))
            
            ticket_id = cursor.lastrowid
            conn.commit()
            
            logger.info(f"Создан тикет поддержки #{ticket_id} от пользователя {user_id}")
            return ticket_id
            
    except Exception as e:
        logger.error(f"Ошибка создания тикета: {e}")
        return None


def get_support_ticket(ticket_id: int):
    """Получает тикет поддержки по ID"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, user_id, user_name, username, message, admin_reply, status, created_at
                FROM support_tickets 
                WHERE id = ?
            """, (ticket_id,))
            
            row = cursor.fetchone()
            
            if row:
                return {
                    'id': row[0],
                    'user_id': row[1],
                    'user_name': row[2],
                    'username': row[3],
                    'message': row[4],
                    'admin_reply': row[5],
                    'status': row[6],
                    'created_at': row[7]
                }
            return None
            
    except Exception as e:
        logger.error(f"Ошибка получения тикета: {e}")
        return None


def reply_support_ticket(ticket_id: int, admin_reply: str):
    """Отвечает на тикет поддержки"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE support_tickets 
                SET admin_reply = ?, status = 'closed', replied_at = datetime('now')
                WHERE id = ?
            """, (admin_reply, ticket_id))
            
            conn.commit()
            
            logger.info(f"Ответ на тикет #{ticket_id} отправлен")
            return True
            
    except Exception as e:
        logger.error(f"Ошибка ответа на тикет: {e}")
        return False


def get_open_support_tickets():
    """Получает все открытые тикеты поддержки"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, user_id, user_name, username, message, created_at
                FROM support_tickets 
                WHERE status = 'open'
                ORDER BY created_at DESC
            """)
            
            rows = cursor.fetchall()
            
            return [{
                'id': row[0],
                'user_id': row[1],
                'user_name': row[2],
                'username': row[3],
                'message': row[4],
                'created_at': row[5]
            } for row in rows]
            
    except Exception as e:
        logger.error(f"Ошибка получения тикетов: {e}")
        return []


def add_user_manually(name: str, phone_number: str, telegram_username: str = "", telegram_id: int = 0):
    """Добавляет пользователя вручную через админку"""
    try:
        timestamp = datetime.now().isoformat()
        photo_path = "manual_entry.jpg"  # Заглушка для фото
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO applications (name, phone_number, telegram_username, telegram_id, photo_path, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (name, phone_number, telegram_username, telegram_id, photo_path, timestamp))
            
            conn.commit()
            logger.info(f"Добавлен пользователь вручную: {name}")
            return cursor.lastrowid
            
    except Exception as e:
        logger.error(f"Ошибка при добавлении пользователя вручную: {e}")
        return None


def update_user(user_id: int, name: str, phone_number: str, telegram_username: str = ""):
    """Обновляет данные пользователя"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE applications 
                SET name = ?, phone_number = ?, telegram_username = ?
                WHERE id = ?
            ''', (name, phone_number, telegram_username, user_id))
            
            conn.commit()
            
            if cursor.rowcount > 0:
                logger.info(f"Обновлены данные пользователя ID: {user_id}")
                return True
            else:
                logger.warning(f"Пользователь с ID {user_id} не найден")
                return False
                
    except Exception as e:
        logger.error(f"Ошибка при обновлении пользователя: {e}")
        return False


def get_user_by_id(user_id: int):
    """Получает пользователя по ID"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, name, phone_number, telegram_username, telegram_id, 
                       photo_path, timestamp, is_winner, photo_hash, risk_score, risk_level, risk_details, status
                FROM applications 
                WHERE id = ?
            ''', (user_id,))
            
            row = cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'name': row[1],
                    'phone_number': row[2],
                    'telegram_username': row[3],
                    'telegram_id': row[4],
                    'photo_path': row[5],
                    'timestamp': row[6],
                    'is_winner': bool(row[7]),
                    'photo_hash': row[8],
                    'risk_score': row[9] or 0,
                    'risk_level': row[10] or 'low',
                    'risk_details': row[11] or '',
                    'status': row[12] or 'pending'
                }
            return None
            
    except Exception as e:
        logger.error(f"Ошибка при получении пользователя: {e}")
        return None


def clear_all_data():
    """Очищает все данные из базы данных"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Получаем количество записей до удаления
            cursor.execute("SELECT COUNT(*) FROM applications")
            apps_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM support_tickets")
            tickets_count = cursor.fetchone()[0]
            
            logger.info(f"Начинаем очистку БД: applications={apps_count}, support_tickets={tickets_count}")
            
            # Удаляем все данные из таблиц
            cursor.execute("DELETE FROM applications")
            apps_deleted = cursor.rowcount
            
            cursor.execute("DELETE FROM support_tickets")
            tickets_deleted = cursor.rowcount
            
            cursor.execute("DELETE FROM leaflet_templates")
            templates_deleted = cursor.rowcount
            
            # Сбрасываем автоинкременты
            cursor.execute("DELETE FROM sqlite_sequence WHERE name IN ('applications', 'support_tickets', 'leaflet_templates')")
            
            conn.commit()
            
            logger.info(f"Данные удалены: applications={apps_deleted}, support_tickets={tickets_deleted}, leaflet_templates={templates_deleted}")
            
            # Проверяем, что таблицы действительно пусты
            cursor.execute("SELECT COUNT(*) FROM applications")
            remaining_apps = cursor.fetchone()[0]
            
            if remaining_apps > 0:
                logger.error(f"ОШИБКА: После очистки осталось {remaining_apps} записей в applications!")
                return False
            
            logger.info("Все данные успешно удалены из базы данных")
            return True
        
    except Exception as e:
        logger.error(f"Ошибка при очистке базы данных: {e}")
        return False


def force_clear_all_data():
    """Принудительная очистка всех данных из базы данных"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            logger.warning("ПРИНУДИТЕЛЬНАЯ ОЧИСТКА БД")
            
            # Отключаем внешние ключи для принудительного удаления
            cursor.execute("PRAGMA foreign_keys = OFF")
            
            # Получаем список всех таблиц
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = cursor.fetchall()
            
            for table in tables:
                table_name = table[0]
                try:
                    cursor.execute(f"DELETE FROM {table_name}")
                    deleted = cursor.rowcount
                    logger.info(f"Очищена таблица {table_name}: удалено {deleted} записей")
                except Exception as e:
                    logger.error(f"Ошибка очистки таблицы {table_name}: {e}")
            
            # Очищаем последовательности
            cursor.execute("DELETE FROM sqlite_sequence")
            
            # Включаем внешние ключи обратно
            cursor.execute("PRAGMA foreign_keys = ON")
            
            conn.commit()
            
            # Проверяем результат
            cursor.execute("SELECT COUNT(*) FROM applications")
            remaining = cursor.fetchone()[0]
            
            logger.warning(f"ПРИНУДИТЕЛЬНАЯ ОЧИСТКА ЗАВЕРШЕНА: осталось {remaining} записей в applications")
            return remaining == 0
        
    except Exception as e:
        logger.error(f"Ошибка принудительной очистки БД: {e}")
        return False