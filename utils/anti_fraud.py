"""
Антифрод-модуль: многоуровневая проверка и скоринг рисков
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

from config import LOYALTY_CARD_LENGTH
from database.db_manager import loyalty_card_exists, get_all_applications


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class CheckResult:
    name: str
    passed: bool
    impact: int
    message: str

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "impact": self.impact,
            "message": self.message,
        }


class BaseCheck:
    name = "base"

    def run(self, participant: Dict, context: Dict) -> CheckResult:
        raise NotImplementedError


class DeviceFingerprintCheck(BaseCheck):
    name = "device_fingerprint"

    def run(self, participant: Dict, context: Dict) -> CheckResult:
        # В рамках Telegram у нас только telegram_id (уникальный)
        # Риск 0: если уникален (всегда уникален за счет БД), иначе высокий
        is_unique = context.get("is_telegram_id_unique", True)
        if is_unique:
            return CheckResult(self.name, True, 0, "Устройство/аккаунт уникален")
        return CheckResult(self.name, False, 40, "Дубликат telegram_id")


class PhoneValidationCheck(BaseCheck):
    name = "phone_validation"

    def run(self, participant: Dict, context: Dict) -> CheckResult:
        phone: str = participant.get("phone_number", "")
        phone_digits = re.sub(r"\D+", "", phone or "")
        is_valid = 10 <= len(phone_digits) <= 15
        if is_valid:
            return CheckResult(self.name, True, 0, "Телефон валиден")
        return CheckResult(self.name, False, 25, "Некорректный номер телефона")


class PhotoHashCheck(BaseCheck):
    name = "photo_hash"

    def run(self, participant: Dict, context: Dict) -> CheckResult:
        photo_hash: str = participant.get("photo_hash") or ""
        if not photo_hash:
            return CheckResult(self.name, False, 30, "Отсутствует хеш фото")
        duplicate_count: int = context.get("duplicate_photo_count", 0)
        if duplicate_count > 0:
            return CheckResult(self.name, False, 60, "Найден дубликат фото")
        return CheckResult(self.name, True, 0, "Фото оригинальное")


class BehaviorAnalysisCheck(BaseCheck):
    name = "behavior_analysis"

    def run(self, participant: Dict, context: Dict) -> CheckResult:
        # Простая эвристика: очень короткие имя повышают риск
        name = participant.get("name", "") or ""
        impact = 0
        messages: List[str] = []
        if len(name.strip()) < 2:
            impact += 10
            messages.append("Имя слишком короткое")
        if impact == 0:
            return CheckResult(self.name, True, 0, "Поведение нормальное")
        return CheckResult(self.name, False, impact, "; ".join(messages))


class VelocityCheck(BaseCheck):
    name = "velocity"

    def run(self, participant: Dict, context: Dict) -> CheckResult:
        # Много регистраций за короткий период — повышенный риск
        # Порог: > 30 регистраций за последние 60 секунд → +15
        recent_registrations: int = context.get("recent_registrations_60s", 0)
        if recent_registrations > 30:
            return CheckResult(self.name, False, 15, "Аномальная скорость регистраций")
        return CheckResult(self.name, True, 0, "Скорость регистраций нормальная")


class GeoLocationCheck(BaseCheck):
    name = "geolocation"

    def run(self, participant: Dict, context: Dict) -> CheckResult:
        # Геоданные отсутствуют → считаем нейтральными (0)
        return CheckResult(self.name, True, 0, "Геолокация не проверяется")


class LoyaltyCardUniquenessCheck(BaseCheck):
    name = "loyalty_card_uniqueness"

    def run(self, participant: Dict, context: Dict) -> CheckResult:
        card = (participant.get("loyalty_card_number") or "").strip()
        if not card:
            return CheckResult(self.name, False, 20, "Номер карты не указан")
        try:
            if loyalty_card_exists(card):
                return CheckResult(self.name, False, 60, "Номер карты уже используется")
        except Exception:
            # В случае ошибки проверки не штрафуем
            pass
        return CheckResult(self.name, True, 0, "Карта уникальна")


class LoyaltyCardPatternCheck(BaseCheck):
    name = "loyalty_card_pattern"

    def run(self, participant: Dict, context: Dict) -> CheckResult:
        card = ''.join(ch for ch in (participant.get("loyalty_card_number") or "") if ch.isdigit())
        if not card:
            return CheckResult(self.name, False, 15, "Номер карты не указан")
        reasons = []
        if len(card) != LOYALTY_CARD_LENGTH:
            reasons.append(f"Длина не равна {LOYALTY_CARD_LENGTH}")
        if len(set(card)) == 1:
            reasons.append("Все цифры одинаковые")
        # Последовательности типа 0123456789 или 9876543210
        seq = "0123456789"
        if card in seq or card in seq[::-1] or seq.find(card[:4]) != -1:
            reasons.append("Подозрительно последовательный номер")
        if reasons:
            return CheckResult(self.name, False, 25, "; ".join(reasons))
        return CheckResult(self.name, True, 0, "Паттернов не найдено")


class AntiFraudSystem:
    def __init__(self):
        self.checks: List[BaseCheck] = [
            DeviceFingerprintCheck(),
            PhoneValidationCheck(),
            LoyaltyCardUniquenessCheck(),
            LoyaltyCardPatternCheck(),
            PhotoHashCheck(),
            BehaviorAnalysisCheck(),
            VelocityCheck(),
            GeoLocationCheck(),
        ]

    def calculate_risk_score(self, participant: Dict, context: Dict) -> Tuple[int, str, List[Dict]]:
        """
        Возвращает (risk_score 0..100, risk_level, details[])
        risk_level: low/medium/high
        """
        total = 0
        details: List[Dict] = []
        for check in self.checks:
            result = check.run(participant, context)
            details.append(result.to_dict())
            if not result.passed:
                total += result.impact

        # Нормируем и ограничиваем 0..100
        total = max(0, min(100, total))
        if total <= 30:
            level = "low"
        elif total <= 70:
            level = "medium"
        else:
            level = "high"
        return total, level, details


def detect_suspicious_loyalty_card(card: str) -> Dict:
    """Возвращает словарь с полями {suspicious: bool, reasons: [..]}"""
    clean = ''.join(ch for ch in (card or '') if ch.isdigit())
    reasons: List[str] = []
    if not clean:
        reasons.append("Пустой номер")
    if len(clean) != LOYALTY_CARD_LENGTH:
        reasons.append(f"Длина не равна {LOYALTY_CARD_LENGTH}")
    if clean and len(set(clean)) == 1:
        reasons.append("Все цифры одинаковые")
    seq = "0123456789"
    if clean and (clean in seq or clean in seq[::-1] or seq.find(clean[:4]) != -1):
        reasons.append("Последовательный номер")
    return {"suspicious": len(reasons) > 0, "reasons": reasons}


def group_similar_applications(applications: List[Dict] = None) -> List[List[Dict]]:
    """Группирует похожие заявки (по карте, телефону или pHash).
    Возвращает список кластеров (списков заявок)."""
    try:
        apps = applications or get_all_applications()
        clusters: List[List[Dict]] = []
        seen = set()
        
        def hamming(a: str, b: str) -> int:
            try:
                return bin(int(a, 16) ^ int(b, 16)).count('1')
            except Exception:
                return 64
        
        for i, app in enumerate(apps):
            if i in seen:
                continue
            cluster = [app]
            seen.add(i)
            for j in range(i+1, len(apps)):
                if j in seen:
                    continue
                other = apps[j]
                same_card = app.get('loyalty_card_number') and app.get('loyalty_card_number') == other.get('loyalty_card_number')
                same_phone = app.get('phone_number') and app.get('phone_number') == other.get('phone_number')
                ph1 = app.get('photo_phash') or ''
                ph2 = other.get('photo_phash') or ''
                similar_photo = ph1 and ph2 and hamming(ph1, ph2) <= 4
                if same_card or same_phone or similar_photo:
                    cluster.append(other)
                    seen.add(j)
            if len(cluster) > 1:
                clusters.append(cluster)
        return clusters
    except Exception:
        return []


__all__ = [
    "AntiFraudSystem",
    "sha256_hex",
    "detect_suspicious_loyalty_card",
    "group_similar_applications",
]


