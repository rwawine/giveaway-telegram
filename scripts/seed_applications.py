import argparse
import random
from datetime import datetime, timedelta
import os
import sys

# Ensure project root on sys.path
CURRENT_DIR = os.path.dirname(__file__)
PARENT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, os.pardir))
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from database.db_manager import init_database, save_application, get_applications_count


FIRST_NAMES = [
    "Alexey", "Ivan", "Petr", "Sergey", "Dmitry", "Mikhail", "Nikolay", "Andrey",
    "Oleg", "Vladislav", "Artem", "Egor", "Kirill", "Roman", "Ilya", "Maxim",
    "Anna", "Elena", "Olga", "Natalia", "Maria", "Ekaterina", "Svetlana", "Tatiana",
]

LAST_NAMES = [
    "Rubchenya", "Ivanov", "Petrov", "Sidorov", "Smirnov", "Kuznetsov", "Popov",
    "Vasiliev", "Pavlov", "Fedorov", "Morozov", "Volkov", "Alexandrov", "Lebedev",
]


def random_name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def random_username(rng: random.Random, idx: int) -> str:
    base = rng.choice(["user", "member", "player", "guest", "client", "human"])  # simple base
    return f"{base}_{idx}"


def random_phone(idx: int) -> str:
    # Deterministic unique phone numbers: +79990000000 + idx
    return f"+7999{idx:07d}"  # ensures uniqueness for large idx


def random_timestamp(rng: random.Random) -> str:
    # Within last 60 days
    days_back = rng.randint(0, 60)
    seconds_back = rng.randint(0, 24 * 3600)
    dt = datetime.now() - timedelta(days=days_back, seconds=seconds_back)
    return dt.isoformat(timespec="seconds")


def choose_status_and_risk(rng: random.Random):
    # Weighted distribution
    roll = rng.random()
    if roll < 0.05:
        status = "blocked"
        risk = rng.randint(70, 100)
        level = "high"
    elif roll < 0.30:
        status = "pending"
        risk = rng.randint(30, 70)
        level = "medium"
    else:
        status = "approved"
        risk = rng.randint(0, 30)
        level = "low"
    return status, risk, level


def main():
    parser = argparse.ArgumentParser(description="Seed applications into the database")
    parser.add_argument("--count", type=int, default=2000, help="How many applications to insert")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    args = parser.parse_args()

    rng = random.Random(args.seed)

    init_database()

    inserted = 0
    start_idx = get_applications_count() + 1

    for i in range(start_idx, start_idx + args.count):
        name = random_name(rng)
        username = random_username(rng, i)
        phone = random_phone(i)
        telegram_id = 100000000 + i
        # Use empty photo path to avoid 404 on gallery; column is NOT NULL but empty string is fine
        photo_path = ""
        status, risk_score, risk_level = choose_status_and_risk(rng)
        # lightweight risk details string; leave empty for speed
        ok = save_application(
            name=name,
            phone_number=phone,
            telegram_username=username,
            telegram_id=telegram_id,
            photo_path=photo_path,
            photo_hash=str(telegram_id),
            risk_score=risk_score,
            risk_level=risk_level,
            risk_details="",
            status=status,
        )
        if ok:
            inserted += 1

    total = get_applications_count()
    print(f"Inserted: {inserted}, Total applications: {total}")


if __name__ == "__main__":
    main()


