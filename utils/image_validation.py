"""
Анализ фото лифлета: качество, перцептивный хеш, подсчет стикеров по зонам
"""

from __future__ import annotations

import io
import json
import logging
from typing import Dict, Any, List, Tuple

import numpy as np
from PIL import Image, ImageOps, ExifTags

from database.db_manager import (
    get_active_leaflet_template,
    count_similar_photo_phash,
)


logger = logging.getLogger(__name__)


def _image_from_bytes(photo_bytes: bytes) -> Image.Image:
    return Image.open(io.BytesIO(photo_bytes))


def compute_ahash_hex(image: Image.Image, hash_size: int = 8) -> str:
    """Average hash (aHash) в hex-формате (64-бит → 16 hex-символов)."""
    try:
        # Грейскейл и уменьшение
        img = ImageOps.exif_transpose(image.convert('L')).resize((hash_size, hash_size), Image.Resampling.LANCZOS)
        pixels = np.asarray(img, dtype=np.float32)
        avg = pixels.mean()
        bits = pixels > avg
        # Преобразуем в 64-битное число
        bitstring = ''.join('1' if b else '0' for b in bits.flatten())
        return f"{int(bitstring, 2):016x}"
    except Exception as e:
        logger.warning(f"pHash (aHash) ошибка: {e}")
        return ""


def variance_of_laplacian(image: Image.Image) -> float:
    """Оценка резкости: дисперсия Лапласиана (чем выше, тем резче)."""
    try:
        img = ImageOps.exif_transpose(image.convert('L'))
        arr = np.asarray(img, dtype=np.float32)
        # 3x3 лапласиан
        kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
        # Пэддинг по краям
        padded = np.pad(arr, 1, mode='edge')
        h, w = arr.shape
        # Свёртка вручную (без SciPy)
        out = (
            kernel[0, 0] * padded[0:h, 0:w] + kernel[0, 1] * padded[0:h, 1:w+1] + kernel[0, 2] * padded[0:h, 2:w+2] +
            kernel[1, 0] * padded[1:h+1, 0:w] + kernel[1, 1] * padded[1:h+1, 1:w+1] + kernel[1, 2] * padded[1:h+1, 2:w+2] +
            kernel[2, 0] * padded[2:h+2, 0:w] + kernel[2, 1] * padded[2:h+2, 1:w+1] + kernel[2, 2] * padded[2:h+2, 2:w+2]
        )
        return float(out.var())
    except Exception as e:
        logger.warning(f"Ошибка расчета резкости: {e}")
        return 0.0


def read_exif_meta(image: Image.Image) -> Dict[str, Any]:
    try:
        exif = getattr(image, 'getexif', lambda: None)()
        if not exif:
            return {}
        mapped = {}
        for k, v in exif.items():
            tag = ExifTags.TAGS.get(k, str(k))
            mapped[tag] = v
        return mapped
    except Exception:
        return {}


def _parse_validation_zones(zones_json: str) -> List[Dict[str, float]]:
    try:
        arr = json.loads(zones_json or '[]')
        if isinstance(arr, list):
            return [z for z in arr if isinstance(z, dict) and all(k in z for k in ('x', 'y', 'w', 'h'))]
        return []
    except Exception:
        return []


def _count_stickers_by_zones(image: Image.Image, zones_json: str) -> Tuple[int, List[float]]:
    """
    Простая эвристика: считаем зону "заполненной", если доля не-белых пикселей > threshold.
    Возвращает: (количество_стикеров, список_покрытий_зон)
    """
    zones = _parse_validation_zones(zones_json)
    if not zones:
        return 0, []
    img = ImageOps.exif_transpose(image.convert('L'))
    arr = np.asarray(img, dtype=np.uint8)
    h, w = arr.shape
    coverage_list: List[float] = []
    for z in zones:
        x0 = max(0, min(w - 1, int(z['x'] * w)))
        y0 = max(0, min(h - 1, int(z['y'] * h)))
        x1 = max(0, min(w, x0 + int(z['w'] * w)))
        y1 = max(0, min(h, y0 + int(z['h'] * h)))
        if x1 <= x0 or y1 <= y0:
            coverage_list.append(0.0)
            continue
        crop = arr[y0:y1, x0:x1]
        # Порог: белый фон ~ >= 240; считаем "чернила" как < 240
        nonwhite_ratio = float((crop < 240).sum()) / float(crop.size)
        coverage_list.append(nonwhite_ratio)
    # Порог покрытия зоны, чтобы считать стикер "видимым"
    stickers = sum(1 for c in coverage_list if c >= 0.20)
    return stickers, coverage_list


def analyze_leaflet(photo_bytes: bytes) -> Dict[str, Any]:
    """Проводит анализ загруженного фото и возвращает метрики и статусы.

    Возврат:
        {
          width, height, blur_score, is_blurry, exif_has_datetime, orientation_ok,
          photo_phash, similar_phash_count,
          required_stickers, stickers_count, zones_coverage[],
          leaflet_status, validation_notes[], manual_review_required
        }
    """
    try:
        img = _image_from_bytes(photo_bytes)
        width, height = img.size

        # Качество
        blur = variance_of_laplacian(img)
        is_blurry = blur < 80.0  # эмпирический порог

        # EXIF
        exif = read_exif_meta(img)
        exif_dt = bool(exif.get('DateTimeOriginal') or exif.get('DateTime'))

        # Ориентация
        orientation_ok = True
        try:
            orientation = exif.get('Orientation')
            if orientation in (3, 6, 8):
                orientation_ok = False
        except Exception:
            pass

        # pHash
        phash = compute_ahash_hex(img)
        similar_cnt = count_similar_photo_phash(phash) if phash else 0

        # Шаблон и стикеры
        tpl = get_active_leaflet_template() or {}
        required_stickers = int(tpl.get('required_stickers') or 0)
        stickers_count, coverages = _count_stickers_by_zones(img, tpl.get('validation_zones') or '[]') if required_stickers > 0 else (0, [])

        # Решение по статусу
        notes: List[str] = []
        leaflet_status = 'approved'
        manual_review = 0

        if width < 1024 or height < 768:
            leaflet_status = 'rejected'
            notes.append('low_resolution')
        if is_blurry:
            leaflet_status = 'rejected'
            notes.append('blurry')
        if not orientation_ok:
            notes.append('orientation_suspect')
            manual_review = 1
        if similar_cnt > 0:
            leaflet_status = 'duplicate'
            notes.append('duplicate_photo')
        if required_stickers > 0 and stickers_count < required_stickers:
            # Не перекрываем duplicate/rejected, если уже определены более критичные статусы
            if leaflet_status == 'approved':
                leaflet_status = 'incomplete'
            notes.append(f'stickers_{stickers_count}_of_{required_stickers}')
            # Просим перезаливку, но оставляем на ручную при сомнених
            manual_review = 1
        if not exif_dt:
            notes.append('exif_datetime_missing')
            manual_review = 1

        return {
            'width': width,
            'height': height,
            'blur_score': float(blur),
            'is_blurry': bool(is_blurry),
            'exif_has_datetime': bool(exif_dt),
            'orientation_ok': bool(orientation_ok),
            'photo_phash': phash,
            'similar_phash_count': int(similar_cnt),
            'required_stickers': int(required_stickers),
            'stickers_count': int(stickers_count),
            'zones_coverage': [float(c) for c in coverages],
            'leaflet_status': leaflet_status,
            'validation_notes': notes,
            'manual_review_required': int(manual_review),
        }
    except Exception as e:
        logger.error(f"Ошибка анализа лифлета: {e}")
        return {
            'width': 0, 'height': 0,
            'blur_score': 0.0, 'is_blurry': False,
            'exif_has_datetime': False, 'orientation_ok': True,
            'photo_phash': '', 'similar_phash_count': 0,
            'required_stickers': 0, 'stickers_count': 0,
            'zones_coverage': [],
            'leaflet_status': 'pending',
            'validation_notes': ['analyze_error'],
            'manual_review_required': 1,
        }


__all__ = [
    'analyze_leaflet',
    'compute_ahash_hex',
]


