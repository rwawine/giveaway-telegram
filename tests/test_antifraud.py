from utils.anti_fraud import AntiFraudSystem


def test_antifraud_low_risk():
    af = AntiFraudSystem()
    participant = {
        'name': 'Alexey',
        'phone_number': '+375339015915',
        'telegram_username': 'alex',
        'telegram_id': 123,
        'photo_hash': 'abcd',
    }
    context = {
        'is_telegram_id_unique': True,
        'duplicate_photo_count': 0,
        'recent_registrations_60s': 0,
    }
    score, level, details = af.calculate_risk_score(participant, context)
    assert 0 <= score <= 30
    assert level == 'low'
    assert isinstance(details, list)


def test_antifraud_high_risk_duplicate():
    af = AntiFraudSystem()
    participant = {
        'name': 'A',  # short name → +10
        'phone_number': '12',  # invalid → +25
        'telegram_username': 'aa',  # short → +5
        'telegram_id': 1,
        'photo_hash': 'same',
    }
    context = {
        'is_telegram_id_unique': False,  # +40
        'duplicate_photo_count': 1,      # +60 (capped to 100 overall)
        'recent_registrations_60s': 100, # +15
    }
    score, level, details = af.calculate_risk_score(participant, context)
    assert score == 100
    assert level == 'high'

