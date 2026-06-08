"""
Tests for the "Start Fresh" net-new cutover (Option A).

Verifies that POSTing to the start-fresh route advances BOTH detection floors:
  - vetting_cutoff_date  (gates the inbound / ParsedEmail path)
  - last_run_timestamp   (gates the Bullhorn-direct detectors: new applicants,
                          PandoLogic, Matador)

so re-enabling screening after a long pause does not re-pull the accumulated
backlog from either path.
"""

from datetime import datetime, timedelta


def _val(key):
    from models import VettingConfig
    cfg = VettingConfig.query.filter_by(setting_key=key).first()
    return cfg.setting_value if cfg else None


def test_start_fresh_advances_both_floors(authenticated_client, app, monkeypatch):
    """A stale last_run_timestamp must be advanced to ~now alongside the cutoff."""
    import utils.screening_dispatch as sd
    monkeypatch.setattr(
        sd, 'enqueue_vetting_now',
        lambda *a, **k: {'enqueued': True, 'reason': 'ok'},
    )

    with app.app_context():
        from app import db
        from models import VettingConfig
        from flask import url_for

        stale = (datetime.utcnow() - timedelta(days=23)).isoformat()
        existing = VettingConfig.query.filter_by(
            setting_key='last_run_timestamp'
        ).first()
        if existing:
            existing.setting_value = stale
        else:
            db.session.add(
                VettingConfig(setting_key='last_run_timestamp', setting_value=stale)
            )
        db.session.commit()

        url = url_for('vetting.start_fresh')

    resp = authenticated_client.post(url, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        cutoff = _val('vetting_cutoff_date')
        last_run = _val('last_run_timestamp')

        assert cutoff is not None
        assert last_run is not None

        last_run_dt = datetime.fromisoformat(last_run)
        cutoff_dt = datetime.strptime(cutoff, '%Y-%m-%d %H:%M:%S')

        # Both floors moved off the stale value to ~now.
        assert (datetime.utcnow() - last_run_dt) < timedelta(minutes=5)
        assert (datetime.utcnow() - cutoff_dt) < timedelta(minutes=5)


def test_start_fresh_creates_last_run_when_absent(authenticated_client, app, monkeypatch):
    """When no last_run_timestamp exists, start-fresh creates it at ~now."""
    import utils.screening_dispatch as sd
    monkeypatch.setattr(
        sd, 'enqueue_vetting_now',
        lambda *a, **k: {'enqueued': False, 'reason': 'scheduler unavailable'},
    )

    with app.app_context():
        from app import db
        from models import VettingConfig
        from flask import url_for

        VettingConfig.query.filter_by(setting_key='last_run_timestamp').delete()
        db.session.commit()

        url = url_for('vetting.start_fresh')

    resp = authenticated_client.post(url, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        last_run = _val('last_run_timestamp')
        assert last_run is not None
        last_run_dt = datetime.fromisoformat(last_run)
        assert (datetime.utcnow() - last_run_dt) < timedelta(minutes=5)
