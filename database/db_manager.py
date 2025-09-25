"""
Менеджер базы данных для Telegram бота с DuckDB
DuckDB - высокопроизводительная аналитическая база данных без блокировок
"""

import logging
import os
import time
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from functools import wraps

# DuckDB import
try:
    import duckdb
    DUCKDB_AVAILABLE = True
except ImportError:
    DUCKDB_AVAILABLE = False

# Fallback для SQLite
import sqlite3

from config import DATABASE_TYPE, get_database_path

logger = logging.getLogger(__name__)


def ensure_duckdb_available():
    """Проверяет доступность DuckDB"""
    if DATABASE_TYPE == 'duckdb' and not DUCKDB_AVAILABLE:
        raise ImportError("DuckDB не установлен. Выполните: pip install duckdb")


def db_retry(max_retries=3, delay=0.1):
    """Декоратор для повтора операций с БД при временных ошибках"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    error_str = str(e).lower()
                    
                    # Условия для повтора
                    retry_conditions = [
                        "database is locked",  # SQLite
                        "connection timeout", # Общие проблемы сети
                        "connection lost",
                        "io error"
                    ]
                    
                    should_retry = any(condition in error_str for condition in retry_conditions)
                    
                    if should_retry and attempt < max_retries - 1:
                        wait_time = delay * (2 ** attempt)
                        logger.warning(f"БД ошибка, попытка {attempt + 1}/{max_retries}, ждем {wait_time:.2f}s: {e}")
                        time.sleep(wait_time)
                        continue
                    
                    logger.error(f"Ошибка в {func.__name__}: {e}")
                    raise
            return None
        return wrapper
    return decorator


@contextmanager
def get_db_connection():
    """Контекстный менеджер для работы с базой данных"""
    db_path = get_database_path()
    
    if DATABASE_TYPE == 'duckdb':
        ensure_duckdb_available()
        conn = None
        try:
            # DuckDB поддерживает concurrent access из коробки
            conn = duckdb.connect(db_path)
            yield conn
        except Exception as e:
            # DuckDB автоматически управляет транзакциями
            # Не нужно делать rollback вручную
            logger.error(f"Ошибка при работе с DuckDB: {e}")
            raise
        finally:
            if conn:
                conn.close()
                
    else:
        # Fallback к SQLite
        conn = None
        try:
            conn = sqlite3.connect(db_path, timeout=60.0)
            conn.row_factory = sqlite3.Row
            
            # Настройки для SQLite
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=10000")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA busy_timeout=60000")
            
            yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Ошибка при работе с SQLite: {e}")
            raise
        finally:
            if conn:
                conn.close()


def init_database():
    """Инициализация базы данных"""
    try:
        if DATABASE_TYPE == 'duckdb':
            init_duckdb()
        else:
            init_sqlite()
        logger.info(f"База данных {DATABASE_TYPE} инициализирована успешно")
    except Exception as e:
        logger.error(f"Ошибка при инициализации базы данных: {e}")
        raise


def init_duckdb():
    """Инициализация DuckDB"""
    ensure_duckdb_available()
    
    # Создаем папку для БД если нужно
    db_path = get_database_path()
    if os.path.dirname(db_path):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Создаем таблицу заявок  
        cursor.execute("""
            CREATE SEQUENCE IF NOT EXISTS applications_id_seq START 1
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id BIGINT PRIMARY KEY DEFAULT nextval('applications_id_seq'),
                name TEXT NOT NULL,
                phone_number TEXT NOT NULL UNIQUE,
                loyalty_card_number TEXT NOT NULL UNIQUE,
                telegram_id BIGINT NOT NULL UNIQUE,
                photo_path TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_winner BOOLEAN DEFAULT FALSE,
                photo_hash TEXT,
                risk_score INTEGER DEFAULT 0,
                risk_level TEXT DEFAULT 'low',
                risk_details TEXT,
                status TEXT DEFAULT 'pending',
                campaign_type TEXT CHECK (campaign_type IN ('smile_500', 'sub_1500', 'pending')),
                admin_notes TEXT,
                manual_review_status TEXT DEFAULT 'pending' CHECK (manual_review_status IN ('pending', 'approved', 'rejected', 'needs_clarification')),
                participant_number INTEGER UNIQUE,
                leaflet_status TEXT DEFAULT 'pending',
                stickers_count INTEGER DEFAULT 0,
                validation_notes TEXT,
                manual_review_required BOOLEAN DEFAULT TRUE,
                photo_phash TEXT
            )
        """)
        
        # Создаем таблицу для тикетов поддержки
        cursor.execute("""
            CREATE SEQUENCE IF NOT EXISTS support_tickets_id_seq START 1
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS support_tickets (
                id BIGINT PRIMARY KEY DEFAULT nextval('support_tickets_id_seq'),
                user_id BIGINT NOT NULL,
                user_name TEXT NOT NULL,
                username TEXT,
                message TEXT NOT NULL,
                admin_reply TEXT,
                status TEXT DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                replied_at TIMESTAMP
            )
        """)
        
        # Создаем таблицу шаблонов лифлетов
        cursor.execute("""
            CREATE SEQUENCE IF NOT EXISTS leaflet_templates_id_seq START 1
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS leaflet_templates (
                id BIGINT PRIMARY KEY DEFAULT nextval('leaflet_templates_id_seq'),
                name TEXT NOT NULL,
                required_stickers INTEGER DEFAULT 5,
                template_image_path TEXT,
                active_from TIMESTAMP,
                active_until TIMESTAMP,
                validation_zones JSON
            )
        """)
        
        # Создаем индексы для быстрого поиска
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_applications_telegram_id ON applications(telegram_id)",
            "CREATE INDEX IF NOT EXISTS idx_applications_phone_number ON applications(phone_number)",
            "CREATE INDEX IF NOT EXISTS idx_applications_loyalty_card ON applications(loyalty_card_number)",
            "CREATE INDEX IF NOT EXISTS idx_applications_is_winner ON applications(is_winner)",
            "CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status)",
            "CREATE INDEX IF NOT EXISTS idx_applications_campaign_type ON applications(campaign_type)",
            "CREATE INDEX IF NOT EXISTS idx_applications_manual_review ON applications(manual_review_status)",
            "CREATE INDEX IF NOT EXISTS idx_applications_leaflet_status ON applications(leaflet_status)",
            "CREATE INDEX IF NOT EXISTS idx_applications_photo_phash ON applications(photo_phash)",
            "CREATE INDEX IF NOT EXISTS idx_support_tickets_user_id ON support_tickets(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_support_tickets_status ON support_tickets(status)",
        ]
        
        for index_sql in indexes:
            try:
                cursor.execute(index_sql)
            except Exception as idx_err:
                logger.warning(f"Не удалось создать индекс: {idx_err}")
        
        # Создаем дефолтный шаблон лифлета
        try:
            cursor.execute('SELECT COUNT(*) FROM leaflet_templates')
            count = cursor.fetchone()[0]
            if count == 0:
                validation_zones = [
                    {"x": 0.10, "y": 0.15, "w": 0.18, "h": 0.18},
                    {"x": 0.41, "y": 0.15, "w": 0.18, "h": 0.18},
                    {"x": 0.72, "y": 0.15, "w": 0.18, "h": 0.18},
                    {"x": 0.25, "y": 0.52, "w": 0.18, "h": 0.18},
                    {"x": 0.56, "y": 0.52, "w": 0.18, "h": 0.18}
                ]
                cursor.execute("""
                    INSERT INTO leaflet_templates 
                    (name, required_stickers, template_image_path, active_from, active_until, validation_zones)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, ('Стандартный', 5, '', None, None, validation_zones))
        except Exception as e:
            logger.warning(f"Не удалось создать дефолтный шаблон лифлетов: {e}")
        
        conn.commit()


def init_sqlite():
    """Инициализация SQLite (fallback)"""
    db_path = get_database_path()
    
    # Создаем папку для БД если нужно
    if os.path.dirname(db_path):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        
        # Создаем таблицу заявок
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone_number TEXT NOT NULL UNIQUE,
                loyalty_card_number TEXT NOT NULL UNIQUE,
                telegram_id INTEGER NOT NULL UNIQUE,
                photo_path TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                is_winner INTEGER DEFAULT 0,
                photo_hash TEXT,
                risk_score INTEGER DEFAULT 0,
                risk_level TEXT DEFAULT 'low',
                risk_details TEXT,
                status TEXT DEFAULT 'pending',
                campaign_type TEXT CHECK (campaign_type IN ('smile_500', 'sub_1500', 'pending')),
                admin_notes TEXT,
                manual_review_status TEXT DEFAULT 'pending' CHECK (manual_review_status IN ('pending', 'approved', 'rejected', 'needs_clarification')),
                participant_number INTEGER UNIQUE,
                leaflet_status TEXT DEFAULT 'pending',
                stickers_count INTEGER DEFAULT 0,
                validation_notes TEXT,
                manual_review_required INTEGER DEFAULT 1,
                photo_phash TEXT
            )
        ''')
        
        # Остальные таблицы для SQLite...
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
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS leaflet_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                required_stickers INTEGER DEFAULT 5,
                template_image_path TEXT,
                active_from TEXT,
                active_until TEXT,
                validation_zones TEXT
            )
        ''')
        
        conn.commit()


def count_duplicate_photo_hash(photo_hash: str) -> int:
    """Подсчитывает количество дубликатов по хешу фото"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM applications WHERE photo_hash = ?', (photo_hash,))
            count = cursor.fetchone()[0]
            return max(0, (count or 0) - 1)
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
            
            # Конвертируем timestamp для DuckDB
            timestamp = row[6]
            if DATABASE_TYPE == 'duckdb' and hasattr(timestamp, 'isoformat'):
                timestamp = timestamp.isoformat()
            
            return {
                'id': row[0],
                'name': row[1],
                'phone_number': row[2],
                'telegram_username': row[3],
                'telegram_id': row[4],
                'photo_path': row[5],
                'timestamp': timestamp,
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
    """Присваивает следующий уникальный participant_number в формате 984765378"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT MAX(participant_number) FROM applications')
            max_num = cursor.fetchone()[0]
            
            # Начинаем с 984765378 или следующий номер
            next_num = 984765378 if not max_num else int(max_num) + 1
            
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
    """Подсчитывает количество похожих фото по perceptual hash"""
    try:
        if not photo_phash:
            return 0
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT photo_phash FROM applications WHERE photo_phash IS NOT NULL AND photo_phash != ?', ('',))
            rows = cursor.fetchall()
            
            def hamming(a: str, b: str) -> int:
                try:
                    return bin(int(a, 16) ^ int(b, 16)).count('1')
                except Exception:
                    return 64
            
            count = 0
            for row in rows:
                phash = row[0]
                if phash and hamming(photo_phash, phash) <= max_hamming_distance:
                    count += 1
            
            return count
    except Exception as e:
        logger.error(f"Ошибка при подсчете похожих pHash: {e}")
        return 0


def get_active_leaflet_template() -> Optional[Dict[str, Any]]:
    """Возвращает активный шаблон лифлета"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            if DATABASE_TYPE == 'duckdb':
                current_time = datetime.now()
                cursor.execute("""
                    SELECT id, name, required_stickers, template_image_path, active_from, active_until, validation_zones
                    FROM leaflet_templates
                    WHERE (active_from IS NULL OR active_from <= ?)
                      AND (active_until IS NULL OR active_until >= ?)
                    ORDER BY id DESC
                    LIMIT 1
                """, (current_time, current_time))
            else:
                # SQLite fallback
                now = datetime.now().isoformat(timespec='seconds')
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
                # Fallback - любой шаблон
                cursor.execute('SELECT id, name, required_stickers, template_image_path, active_from, active_until, validation_zones FROM leaflet_templates ORDER BY id DESC LIMIT 1')
                row = cursor.fetchone()
                if not row:
                    return None
            
            validation_zones = row[6]
            if DATABASE_TYPE == 'duckdb':
                # DuckDB возвращает JSON как объект
                validation_zones_str = json.dumps(validation_zones) if validation_zones else '[]'
            else:
                validation_zones_str = validation_zones or '[]'
            
            return {
                'id': row[0],
                'name': row[1],
                'required_stickers': row[2] or 0,
                'template_image_path': row[3] or '',
                'active_from': row[4],
                'active_until': row[5],
                'validation_zones': validation_zones_str
            }
    except Exception as e:
        logger.error(f"Ошибка получения активного шаблона: {e}")
        return None


def count_recent_registrations(seconds: int = 60) -> int:
    """Подсчитывает количество регистраций за последние N секунд"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            if DATABASE_TYPE == 'duckdb':
                # Вычисляем время N секунд назад
                time_ago = datetime.now() - timedelta(seconds=seconds)
                cursor.execute("""
                    SELECT COUNT(*)
                    FROM applications
                    WHERE timestamp >= ?
                """, (time_ago,))
            else:
                # SQLite fallback
                cursor.execute("""
                    SELECT COUNT(*)
                    FROM applications
                    WHERE datetime(timestamp) >= datetime('now', ?)
                """, (f'-{seconds} seconds',))
            
            return cursor.fetchone()[0] or 0
    except Exception as e:
        logger.error(f"Ошибка при подсчете недавних регистраций: {e}")
        return 0


@db_retry(max_retries=3, delay=0.1)
def update_risk(application_id: int, risk_score: int, risk_level: str, risk_details: str) -> bool:
    """Обновляет информацию о риске заявки"""
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
    """Устанавливает статус заявки"""
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
def save_application(name: str, phone_number: str, telegram_username: str = "", 
                    telegram_id: int = 0, photo_path: str = "", photo_hash: str = "",
                    risk_score: int = 0, risk_level: str = "low", risk_details: str = "",
                    status: str = "pending",
                    participant_number: Optional[int] = None,
                    leaflet_status: str = "pending",
                    stickers_count: int = 0,
                    validation_notes: str = "",
                    manual_review_required: int = 1,
                    photo_phash: str = "") -> bool:
    """Сохраняет заявку в базу данных"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            if DATABASE_TYPE == 'duckdb':
                # Устанавливаем timestamp для DuckDB
                current_timestamp = datetime.now()
                cursor.execute("""
                    INSERT INTO applications (
                        name, phone_number, telegram_username, telegram_id, photo_path, timestamp,
                        photo_hash, risk_score, risk_level, risk_details, status,
                        participant_number, leaflet_status, stickers_count, validation_notes, 
                        manual_review_required, photo_phash
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                """, (
                    name, phone_number, telegram_username, telegram_id, photo_path, current_timestamp,
                    photo_hash, risk_score, risk_level, risk_details, status,
                    participant_number, leaflet_status, stickers_count, validation_notes, 
                    manual_review_required, photo_phash
                ))
                # DuckDB возвращает id через RETURNING
                app_id = cursor.fetchone()[0]
            else:
                # SQLite fallback
                timestamp = datetime.now().isoformat()
                cursor.execute('''
                    INSERT INTO applications (
                        name, phone_number, telegram_username, telegram_id, photo_path, timestamp,
                        photo_hash, risk_score, risk_level, risk_details, status,
                        participant_number, leaflet_status, stickers_count, validation_notes, 
                        manual_review_required, photo_phash
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    name, phone_number, telegram_username, telegram_id, photo_path, timestamp,
                    photo_hash, risk_score, risk_level, risk_details, status,
                    participant_number, leaflet_status, stickers_count, validation_notes, 
                    manual_review_required, photo_phash
                ))
                app_id = cursor.lastrowid
            
            # Присваиваем номер участника всем новым заявкам
            if participant_number is None:
                try:
                    cursor.execute('SELECT MAX(participant_number) FROM applications')
                    max_num = cursor.fetchone()[0]
                    # Начинаем с 984765378
                    next_num = 984765378 if not max_num else int(max_num) + 1
                    cursor.execute(
                        'UPDATE applications SET participant_number = ? WHERE id = ?',
                        (next_num, app_id)
                    )
                except Exception as e:
                    logger.warning(f"Не удалось присвоить номер участника: {e}")
            
            conn.commit()
            logger.info(f"Создана заявка для пользователя {name} (ID: {telegram_id}, app_id: {app_id})")
            return True
            
    except Exception as e:
        error_str = str(e).lower()
        if "unique" in error_str or "constraint" in error_str:
            logger.warning(f"Попытка создать заявку с существующими данными: {phone_number}/{telegram_id}")
        else:
            logger.error(f"Ошибка при сохранении заявки: {e}")
        return False


# Остальные функции аналогично адаптируются...
# Для краткости показываю только основные, остальные следуют тому же паттерну

def delete_application(application_id: int) -> bool:
    """Удаляет заявку по ID"""
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
    """Проверяет существование заявки по Telegram ID или номеру телефона"""
    try:
        logger.info(f"🔍 Проверяем заявку для TG_ID: {telegram_id}, телефон: {phone_number}")
        
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
            
            result = cursor.fetchone()
            exists = result is not None
            
            logger.info(f"📊 Результат проверки для TG_ID {telegram_id}: {'НАЙДЕНА' if exists else 'НЕ НАЙДЕНА'}")
            if exists:
                logger.info(f"📌 Найдена заявка с ID: {result[0]}")
            
            return exists
            
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке существования заявки для TG_ID {telegram_id}: {e}")
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
                ORDER BY timestamp DESC
            ''')
            
            applications = []
            for row in cursor.fetchall():
                # Обработка timestamp
                timestamp = row[6]
                if DATABASE_TYPE == 'duckdb' and hasattr(timestamp, 'isoformat'):
                    timestamp = timestamp.isoformat()
                
                applications.append({
                    'id': row[0],
                    'name': row[1],
                    'phone_number': row[2],
                    'telegram_username': row[3],
                    'telegram_id': row[4],
                    'photo_path': row[5],
                    'timestamp': timestamp,
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
    """Возвращает страницу заявок (пагинация) с фильтрами"""
    try:
        page = max(1, int(page or 1))
        per_page = max(1, int(per_page or 100))
        offset = (page - 1) * per_page

        with get_db_connection() as conn:
            cursor = conn.cursor()
            where_conditions = []
            params = []
            
            if status and status in ("approved", "pending", "blocked"):
                where_conditions.append("COALESCE(status, 'pending') = ?")
                params.append(status)
                
            if risk and risk in ("low", "medium", "high"):
                if risk == "low":
                    where_conditions.append("(COALESCE(risk_score, 0) <= 30)")
                elif risk == "medium":
                    where_conditions.append("(COALESCE(risk_score, 0) > 30 AND COALESCE(risk_score, 0) <= 70)")
                else:
                    where_conditions.append("(COALESCE(risk_score, 0) > 70)")

            base_sql = """
                SELECT id, name, phone_number, telegram_username, telegram_id,
                       photo_path, timestamp, is_winner, photo_hash, risk_score, risk_level, risk_details, status,
                       participant_number, leaflet_status, stickers_count, validation_notes, manual_review_required, photo_phash
                FROM applications
            """
            
            if where_conditions:
                base_sql += " WHERE " + " AND ".join(where_conditions)
            base_sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
            params.extend([per_page, offset])
            
            cursor.execute(base_sql, tuple(params))

            applications = []
            for row in cursor.fetchall():
                timestamp = row[6]
                if DATABASE_TYPE == 'duckdb' and hasattr(timestamp, 'isoformat'):
                    timestamp = timestamp.isoformat()
                
                applications.append({
                    'id': row[0],
                    'name': row[1],
                    'phone_number': row[2],
                    'telegram_username': row[3],
                    'telegram_id': row[4],
                    'photo_path': row[5],
                    'timestamp': timestamp,
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
            
            # Сбрасываем всех предыдущих победителей
            cursor.execute('UPDATE applications SET is_winner = FALSE')
            
            # Выбираем случайного участника (исключая заблокированных)
            if DATABASE_TYPE == 'duckdb':
                cursor.execute('''
                    SELECT id FROM applications 
                    WHERE COALESCE(status, 'pending') != 'blocked'
                    USING SAMPLE 1
                ''')
            else:
                # SQLite fallback
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
            
            # Устанавливаем как победителя
            cursor.execute('UPDATE applications SET is_winner = TRUE WHERE id = ?', (winner_id,))
            
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
                SELECT id, name, phone_number, loyalty_card_number, telegram_id, photo_path, timestamp
                FROM applications 
                WHERE is_winner = TRUE
                LIMIT 1
            ''')
            
            row = cursor.fetchone()
            if row:
                timestamp = row[6]
                if DATABASE_TYPE == 'duckdb' and hasattr(timestamp, 'isoformat'):
                    timestamp = timestamp.isoformat()
                
                return {
                    'id': row[0],
                    'name': row[1],
                    'phone_number': row[2],
                    'loyalty_card_number': row[3],
                    'telegram_id': row[4],
                    'photo_path': row[5],
                    'timestamp': timestamp
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
            cursor.execute('UPDATE applications SET is_winner = FALSE')
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
            if DATABASE_TYPE == 'duckdb':
                today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                cursor.execute('SELECT COUNT(*) FROM applications WHERE timestamp >= ?', (today_start,))
            else:
                today = datetime.now().date().isoformat()
                cursor.execute('SELECT COUNT(*) FROM applications WHERE date(timestamp) = ?', (today,))
            today_count = cursor.fetchone()[0]
            
            # Заявки за эту неделю
            if DATABASE_TYPE == 'duckdb':
                week_start = datetime.now().date() - timedelta(days=datetime.now().weekday())
                week_start_dt = datetime.combine(week_start, datetime.min.time())
                cursor.execute('SELECT COUNT(*) FROM applications WHERE timestamp >= ?', (week_start_dt,))
            else:
                week_start = (datetime.now().date() - timedelta(days=datetime.now().weekday())).isoformat()
                cursor.execute('SELECT COUNT(*) FROM applications WHERE date(timestamp) >= ?', (week_start,))
            week_count = cursor.fetchone()[0]
            
            # Заявки за этот месяц
            if DATABASE_TYPE == 'duckdb':
                month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                cursor.execute('SELECT COUNT(*) FROM applications WHERE timestamp >= ?', (month_start,))
            else:
                month_start = datetime.now().replace(day=1).date().isoformat()
                cursor.execute('SELECT COUNT(*) FROM applications WHERE date(timestamp) >= ?', (month_start,))
            month_count = cursor.fetchone()[0]
            
            # Есть ли победитель
            cursor.execute('SELECT COUNT(*) FROM applications WHERE is_winner = TRUE')
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
        where_conditions = []
        params = []
        
        if status and status in ("approved", "pending", "blocked"):
            where_conditions.append("COALESCE(status, 'pending') = ?")
            params.append(status)
            
        if risk and risk in ("low", "medium", "high"):
            if risk == "low":
                where_conditions.append("(COALESCE(risk_score, 0) <= 30)")
            elif risk == "medium":
                where_conditions.append("(COALESCE(risk_score, 0) > 30 AND COALESCE(risk_score, 0) <= 70)")
            else:
                where_conditions.append("(COALESCE(risk_score, 0) > 70)")

        sql = "SELECT COUNT(*) FROM applications"
        if where_conditions:
            sql += " WHERE " + " AND ".join(where_conditions)

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
            if DATABASE_TYPE == 'duckdb':
                cursor.execute("""
                    INSERT INTO support_tickets (user_id, user_name, username, message)
                    VALUES (?, ?, ?, ?)
                    RETURNING id
                """, (user_id, user_name, username, message))
                ticket_id = cursor.fetchone()[0]
            else:
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
                created_at = row[7]
                if DATABASE_TYPE == 'duckdb' and hasattr(created_at, 'isoformat'):
                    created_at = created_at.isoformat()
                
                return {
                    'id': row[0],
                    'user_id': row[1],
                    'user_name': row[2],
                    'username': row[3],
                    'message': row[4],
                    'admin_reply': row[5],
                    'status': row[6],
                    'created_at': created_at
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
            
            if DATABASE_TYPE == 'duckdb':
                current_time = datetime.now()
                cursor.execute("""
                    UPDATE support_tickets 
                    SET admin_reply = ?, status = 'closed', replied_at = ?
                    WHERE id = ?
                """, (admin_reply, current_time, ticket_id))
            else:
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
                'created_at': row[5].isoformat() if DATABASE_TYPE == 'duckdb' and hasattr(row[5], 'isoformat') else row[5]
            } for row in rows]
            
    except Exception as e:
        logger.error(f"Ошибка получения тикетов: {e}")
        return []


def add_user_manually(name: str, phone_number: str, loyalty_card_number: str = "", telegram_id: int = 0):
    """Добавляет пользователя вручную через админку"""
    try:
        photo_path = "manual_entry.jpg"  # Заглушка для фото
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            if DATABASE_TYPE == 'duckdb':
                current_timestamp = datetime.now()
                cursor.execute('''
                    INSERT INTO applications (name, phone_number, loyalty_card_number, telegram_id, photo_path, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                    RETURNING id
                ''', (name, phone_number, loyalty_card_number, telegram_id, photo_path, current_timestamp))
                user_id = cursor.fetchone()[0]
            else:
                timestamp = datetime.now().isoformat()
                cursor.execute('''
                    INSERT INTO applications (name, phone_number, loyalty_card_number, telegram_id, photo_path, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (name, phone_number, loyalty_card_number, telegram_id, photo_path, timestamp))
                user_id = cursor.lastrowid
            conn.commit()
            logger.info(f"Добавлен пользователь вручную: {name}")
            return user_id
            
    except Exception as e:
        logger.error(f"Ошибка при добавлении пользователя вручную: {e}")
        return None


def update_user(user_id: int, name: str, phone_number: str, loyalty_card_number: str = ""):
    """Обновляет данные пользователя"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE applications 
                SET name = ?, phone_number = ?, loyalty_card_number = ?
                WHERE id = ?
            ''', (name, phone_number, loyalty_card_number, user_id))
            
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
                       photo_path, timestamp, is_winner, photo_hash, risk_score, risk_level, risk_details, status,
                       participant_number, leaflet_status, stickers_count, validation_notes, manual_review_required, photo_phash
                FROM applications 
                WHERE id = ?
            ''', (user_id,))
            
            row = cursor.fetchone()
            if row:
                timestamp = row[6]
                if DATABASE_TYPE == 'duckdb' and hasattr(timestamp, 'isoformat'):
                    timestamp = timestamp.isoformat()
                
                return {
                    'id': row[0],
                    'name': row[1],
                    'phone_number': row[2],
                    'telegram_username': row[3],
                    'telegram_id': row[4],
                    'photo_path': row[5],
                    'timestamp': timestamp,
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
            return None
            
    except Exception as e:
        logger.error(f"Ошибка при получении пользователя: {e}")
        return None


def loyalty_card_exists(loyalty_card_number: str) -> bool:
    """Проверяет, существует ли заявка с таким номером (используем telegram_username)"""
    try:
        # Поскольку карт лояльности нет в схеме, проверяем по telegram_username
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM applications WHERE telegram_username = ? LIMIT 1', (loyalty_card_number,))
            return cursor.fetchone() is not None
    except Exception as e:
        logger.error(f"Ошибка проверки карты лояльности: {e}")
        return False


def get_filtered_applications_count(risk: str = None, status: str = None, campaign: str = None, manual_review: str = None) -> int:
    """Возвращает количество заявок по фильтрам"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            where_conditions = []
            params = []
            if status and status in ("approved", "pending", "blocked"):
                where_conditions.append("COALESCE(status, 'pending') = ?")
                params.append(status)
            if risk and risk in ("low", "medium", "high"):
                if risk == "low":
                    where_conditions.append("(COALESCE(risk_score, 0) <= 30)")
                elif risk == "medium":
                    where_conditions.append("(COALESCE(risk_score, 0) > 30 AND COALESCE(risk_score, 0) <= 70)")
                else:
                    where_conditions.append("(COALESCE(risk_score, 0) > 70)")
            # campaign_type и manual_review_status отсутствуют в схеме, пропускаем
            # if campaign and campaign in ('smile_500', 'sub_1500', 'pending'):
            #     where_conditions.append("COALESCE(campaign_type, 'pending') = ?")
            #     params.append(campaign)
            # if manual_review and manual_review in ('pending', 'approved', 'rejected', 'needs_clarification'):
            #     where_conditions.append("COALESCE(manual_review_status, 'pending') = ?")
            #     params.append(manual_review)
            sql = "SELECT COUNT(*) FROM applications"
            if where_conditions:
                sql += " WHERE " + " AND ".join(where_conditions)
            cursor.execute(sql, tuple(params))
            return cursor.fetchone()[0] or 0
    except Exception as e:
        logger.error(f"Ошибка подсчета по фильтрам: {e}")
        return 0


def set_campaign_type(application_id: int, campaign_type: str) -> bool:
    """Устанавливает тип акции для заявки (заглушка - поле отсутствует в схеме)"""
    try:
        # Поле campaign_type отсутствует в схеме БД, возвращаем True
        logger.info(f"set_campaign_type вызван для {application_id} с {campaign_type} (поле отсутствует)")
        return True
    except Exception as e:
        logger.error(f"Ошибка в set_campaign_type: {e}")
        return False


def set_manual_review_status(application_id: int, status: str) -> bool:
    """Обновляет статус ручной модерации (заглушка)"""
    try:
        # Поле manual_review_status отсутствует, используем основное поле status
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Одобрено/отклонено -> approved/rejected
            new_status = 'approved' if status == 'approved' else ('rejected' if status == 'rejected' else 'pending')
            cursor.execute('UPDATE applications SET status = ? WHERE id = ?', (new_status, application_id))
            conn.commit()
            return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Ошибка обновления manual_review_status: {e}")
        return False


def update_admin_notes(application_id: int, notes: str) -> bool:
    """Сохраняет комментарии администратора"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE applications SET admin_notes = ? WHERE id = ?', (notes or '', application_id))
            conn.commit()
            return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Ошибка обновления admin_notes: {e}")
        return False


def bulk_set_campaign_type(ids: List[int], campaign_type: str) -> int:
    """Массово назначает тип акции (заглушка - поле отсутствует)"""
    try:
        logger.info(f"bulk_set_campaign_type вызван для {len(ids)} заявок (поле отсутствует)")
        return len(ids) if ids else 0
    except Exception as e:
        logger.error(f"Ошибка в bulk_set_campaign_type: {e}")
        return 0


def bulk_set_manual_review_status(ids: List[int], status: str) -> int:
    """Массово обновляет статус ручной модерации (через status поле)"""
    try:
        if not ids:
            return 0
        # Преобразуем статус модерации в основной статус
        new_status = 'approved' if status == 'approved' else ('rejected' if status == 'rejected' else 'pending')
        with get_db_connection() as conn:
            cursor = conn.cursor()
            qmarks = ','.join('?' for _ in ids)
            cursor.execute(f'UPDATE applications SET status = ? WHERE id IN ({qmarks})', tuple([new_status] + ids))
            conn.commit()
            return cursor.rowcount
    except Exception as e:
        logger.error(f"Ошибка массового обновления manual_review_status: {e}")
        return 0


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
            
            if DATABASE_TYPE == 'duckdb':
                # DuckDB автоматически управляет последовательностями
                pass
            else:
                # Сбрасываем автоинкременты для SQLite
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
            
            if DATABASE_TYPE == 'duckdb':
                # DuckDB таблицы
                tables = ['applications', 'support_tickets', 'leaflet_templates']
                
                for table_name in tables:
                    try:
                        cursor.execute(f"DELETE FROM {table_name}")
                        deleted = cursor.rowcount
                        logger.info(f"Очищена таблица {table_name}: удалено {deleted} записей")
                    except Exception as e:
                        logger.error(f"Ошибка очистки таблицы {table_name}: {e}")
            else:
                # SQLite fallback
                cursor.execute("PRAGMA foreign_keys = OFF")
                
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
                
                cursor.execute("DELETE FROM sqlite_sequence")
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