"""
Антифрод-модуль: многоуровневая проверка и скоринг рисков
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple


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
        # Простая эвристика: очень короткие имя/username повышают риск
        name = participant.get("name", "") or ""
        username = participant.get("telegram_username", "") or ""
        impact = 0
        messages: List[str] = []
        if len(name.strip()) < 2:
            impact += 10
            messages.append("Имя слишком короткое")
        if username and len(username.strip()) < 3:
            impact += 5
            messages.append("Username слишком короткий")
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


class AntiFraudSystem:
    def __init__(self):
        self.checks: List[BaseCheck] = [
            DeviceFingerprintCheck(),
            PhoneValidationCheck(),
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


__all__ = [
    "AntiFraudSystem",
    "sha256_hex",
]


