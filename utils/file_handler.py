"""
Утилиты для работы с файлами
"""

import os
import csv
import logging
from datetime import datetime
from typing import List, Dict

import pandas as pd

from config import PHOTOS_DIR, EXPORTS_DIR

logger = logging.getLogger(__name__)


def save_photo(photo_file, user_id: int) -> str:
    """
    Сохраняет фото в папку photos
    
    Args:
        photo_file: Объект файла фото из Telegram
        user_id: ID пользователя
    
    Returns:
        str: Путь к сохраненному файлу
    """
    try:
        # Создаем папку если она не существует
        os.makedirs(PHOTOS_DIR, exist_ok=True)
        
        # Генерируем имя файла
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"user_{user_id}_{timestamp}.jpg"
        file_path = os.path.join(PHOTOS_DIR, filename)
        
        # Сохраняем файл
        with open(file_path, 'wb') as f:
            f.write(photo_file)
        
        logger.info(f"Фото сохранено: {file_path}")
        return file_path
        
    except Exception as e:
        logger.error(f"Ошибка при сохранении фото: {e}")
        raise


def export_to_csv(applications: List[Dict]) -> str:
    """
    Экспортирует заявки в CSV файл
    
    Args:
        applications: Список заявок
        
    Returns:
        str: Путь к созданному файлу
    """
    try:
        os.makedirs(EXPORTS_DIR, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"applications_{timestamp}.csv"
        file_path = os.path.join(EXPORTS_DIR, filename)
        
        with open(file_path, 'w', newline='', encoding='utf-8-sig') as csvfile:
            if not applications:
                csvfile.write("Заявок нет\n")
                return file_path
                
            fieldnames = ['ID', 'Имя', 'Телефон', 'Карта лояльности (последние 4)', 'Время подачи', 'Победитель']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            writer.writeheader()
            for app in applications:
                last4 = (app.get('loyalty_card_number') or '')[-4:]
                writer.writerow({
                    'ID': app['id'],
                    'Имя': app['name'],
                    'Телефон': app['phone_number'],
                    'Карта лояльности (последние 4)': last4 if last4 else '',
                    'Время подачи': app['timestamp'],
                    'Победитель': 'Да' if app['is_winner'] else 'Нет'
                })
        
        logger.info(f"CSV экспорт создан: {file_path}")
        return file_path
        
    except Exception as e:
        logger.error(f"Ошибка при создании CSV: {e}")
        raise


def export_to_excel(applications: List[Dict]) -> str:
    """
    Экспортирует заявки в Excel файл
    
    Args:
        applications: Список заявок
        
    Returns:
        str: Путь к созданному файлу
    """
    try:
        os.makedirs(EXPORTS_DIR, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"applications_{timestamp}.xlsx"
        file_path = os.path.join(EXPORTS_DIR, filename)
        
        if not applications:
            # Создаем пустой файл
            df = pd.DataFrame({"Сообщение": ["Заявок нет"]})
            df.to_excel(file_path, index=False)
            return file_path
        
        # Подготавливаем данные
        data = []
        for app in applications:
            data.append({
                'ID': app['id'],
                'Имя': app['name'],
                'Телефон': app['phone_number'],
                'Карта лояльности': app.get('loyalty_card_number') or '',
                'Telegram ID': app['telegram_id'],
                'Время подачи': app['timestamp'],
                'Путь к фото': app['photo_path'],
                'Победитель': 'Да' if app['is_winner'] else 'Нет'
            })
        
        df = pd.DataFrame(data)
        
        # Создаем Excel файл с форматированием
        with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Заявки', index=False)
            
            # Автоматически подгоняем ширину столбцов
            worksheet = writer.sheets['Заявки']
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
        
        logger.info(f"Excel экспорт создан: {file_path}")
        return file_path
        
    except Exception as e:
        logger.error(f"Ошибка при создании Excel: {e}")
        raise


def cleanup_old_exports(days: int = 7):
    """
    Удаляет старые файлы экспорта
    
    Args:
        days: Количество дней для хранения файлов
    """
    try:
        if not os.path.exists(EXPORTS_DIR):
            return
            
        current_time = datetime.now()
        
        for filename in os.listdir(EXPORTS_DIR):
            file_path = os.path.join(EXPORTS_DIR, filename)
            
            if os.path.isfile(file_path):
                file_time = datetime.fromtimestamp(os.path.getctime(file_path))
                
                if (current_time - file_time).days > days:
                    os.remove(file_path)
                    logger.info(f"Удален старый файл экспорта: {filename}")
                    
    except Exception as e:
        logger.error(f"Ошибка при очистке старых экспортов: {e}")
