"""
Lottery system: pick one winner per campaign category with transparency logging.
"""

import logging
from typing import Dict, Any
from datetime import datetime

from config import DATABASE_TYPE
from database.db_manager import get_db_connection

logger = logging.getLogger(__name__)


def _select_random_id_for_campaign(campaign: str) -> int | None:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if DATABASE_TYPE == 'duckdb':
            cursor.execute(
                """
                SELECT id FROM applications
                WHERE COALESCE(status,'pending') != 'blocked'
                  AND COALESCE(manual_review_status,'pending') = 'approved'
                  AND COALESCE(campaign_type,'pending') = ?
                USING SAMPLE 1
                """,
                (campaign,),
            )
        else:
            cursor.execute(
                """
                SELECT id FROM applications
                WHERE COALESCE(status,'pending') != 'blocked'
                  AND COALESCE(manual_review_status,'pending') = 'approved'
                  AND COALESCE(campaign_type,'pending') = ?
                ORDER BY RANDOM() LIMIT 1
                """,
                (campaign,),
            )
        row = cursor.fetchone()
        return int(row[0]) if row else None


def draw_lottery_by_campaign() -> Dict[str, Any]:
    """Draws winners: one from 'smile_500' and one from 'sub_1500'.
    Returns a dict with details and timestamps.
    """
    ts = datetime.utcnow().isoformat() + 'Z'
    result: Dict[str, Any] = {
        'timestamp': ts,
        'winners': {}
    }
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Validate there are eligible participants in both categories
            cursor.execute(
                """
                SELECT campaign_type, COUNT(*) AS cnt
                FROM applications
                WHERE COALESCE(status,'pending') != 'blocked'
                  AND COALESCE(manual_review_status,'pending') = 'approved'
                  AND COALESCE(campaign_type,'pending') IN ('smile_500','sub_1500')
                GROUP BY campaign_type
                """
            )
            counts = {row[0] or 'pending': int(row[1]) for row in cursor.fetchall()}
            if counts.get('smile_500', 0) == 0 or counts.get('sub_1500', 0) == 0:
                missing = []
                if counts.get('smile_500', 0) == 0:
                    missing.append('smile_500')
                if counts.get('sub_1500', 0) == 0:
                    missing.append('sub_1500')
                raise RuntimeError(f"Недостаточно участников в категориях: {', '.join(missing)}")

            # Select winners
            smile_id = _select_random_id_for_campaign('smile_500')
            sub_id = _select_random_id_for_campaign('sub_1500')
            if not smile_id or not sub_id:
                raise RuntimeError("Не удалось выбрать победителей в одной из категорий")

            # Reset previous flags and set winners
            cursor.execute("UPDATE applications SET is_winner = FALSE WHERE is_winner = TRUE")
            cursor.execute("UPDATE applications SET is_winner = TRUE WHERE id IN (?, ?)", (smile_id, sub_id))

            # Fetch winner details
            cursor.execute(
                """
                SELECT id, name, phone_number, loyalty_card_number, telegram_id, photo_path, campaign_type
                FROM applications
                WHERE id IN (?, ?)
                """,
                (smile_id, sub_id),
            )
            rows = cursor.fetchall()
            for row in rows:
                winner = {
                    'id': int(row[0]),
                    'name': row[1],
                    'phone_number': row[2],
                    'loyalty_card_number': row[3],
                    'telegram_id': int(row[4]),
                    'photo_path': row[5],
                    'campaign_type': row[6] or 'pending'
                }
                result['winners'][winner['campaign_type']] = winner

            conn.commit()

        logger.info(
            "LOTTERY_DRAW: time=%s winners=%s",
            ts,
            {k: v['id'] for k, v in result['winners'].items()}
        )
        return result
    except Exception as e:
        logger.error(f"Ошибка розыгрыша: {e}")
        raise
