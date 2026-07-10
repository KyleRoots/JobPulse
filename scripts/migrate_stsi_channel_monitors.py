#!/usr/bin/env python3
"""
Migrate BullhornMonitor rows for STSI channel tearsheets.

- Renames tearsheet 1531 monitor to 'Sponsored - STSI - LinkedIn'
- Creates active monitors for Indeed (1640) and Zip Recruiter (1641)

Run against production:
    flask shell < scripts/migrate_stsi_channel_monitors.py
or:
    python scripts/migrate_stsi_channel_monitors.py
"""
from datetime import datetime, timedelta


MONITOR_CONFIGS = [
    {
        'tearsheet_id': 1531,
        'name': 'Sponsored - STSI - LinkedIn',
        'tearsheet_name': 'Sponsored - STSI - LinkedIn',
        'notification_email': '',
    },
    {
        'tearsheet_id': 1640,
        'name': 'Sponsored - STSI - Indeed',
        'tearsheet_name': 'Sponsored - STSI - Indeed',
        'notification_email': '',
    },
    {
        'tearsheet_id': 1641,
        'name': 'Sponsored - STSI - Zip Recruiter',
        'tearsheet_name': 'Sponsored - STSI - Zip Recruiter',
        'notification_email': '',
    },
]


def migrate(db, BullhornMonitor):
    now = datetime.utcnow()
    next_check = now + timedelta(minutes=5)
    results = []

    for cfg in MONITOR_CONFIGS:
        existing = BullhornMonitor.query.filter_by(tearsheet_id=cfg['tearsheet_id']).first()
        if existing:
            existing.name = cfg['name']
            existing.tearsheet_name = cfg['tearsheet_name']
            existing.notification_email = cfg['notification_email']
            existing.is_active = True
            existing.updated_at = now
            if not existing.next_check:
                existing.next_check = next_check
            results.append(f"updated monitor id={existing.id} tearsheet={cfg['tearsheet_id']}")
        else:
            monitor = BullhornMonitor(
                name=cfg['name'],
                tearsheet_id=cfg['tearsheet_id'],
                tearsheet_name=cfg['tearsheet_name'],
                notification_email=cfg['notification_email'],
                is_active=True,
                check_interval_minutes=5,
                next_check=next_check,
                send_notifications=False,
            )
            db.session.add(monitor)
            results.append(f"created monitor tearsheet={cfg['tearsheet_id']}")

    db.session.commit()
    return results


def main():
    from app import app, db
    from models import BullhornMonitor

    with app.app_context():
        results = migrate(db, BullhornMonitor)
        for line in results:
            print(line)
        print('STSI channel monitor migration complete.')


if __name__ == '__main__':
    main()
