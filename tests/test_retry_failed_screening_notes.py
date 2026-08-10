"""Regression: completed screens with note_created=False must be reparable.

Terry Vallo (4674305) Aug 10 2026 — screening completed (Location Review 88→78)
but Bullhorn never got a Scout note; cooldown + vetted_at blocked natural retry.
"""
from datetime import datetime


def test_retry_failed_screening_notes_creates_note_and_notifies(app, monkeypatch):
    from app import db
    from models import CandidateVettingLog, VettingConfig
    import candidate_vetting_service as cvs_pkg
    from tasks.vetting import run_retry_failed_screening_notes

    with app.app_context():
        VettingConfig.query.filter_by(setting_key='vetting_enabled').delete()
        db.session.add(VettingConfig(setting_key='vetting_enabled', setting_value='true'))
        db.session.commit()

        CandidateVettingLog.query.filter(
            CandidateVettingLog.status == 'completed',
            CandidateVettingLog.note_created == False,
        ).delete(synchronize_session=False)
        db.session.commit()

        log = CandidateVettingLog(
            bullhorn_candidate_id=4674305,
            candidate_name='Terry Vallo',
            status='completed',
            is_qualified=False,
            highest_match_score=78.0,
            note_created=False,
            bullhorn_note_id=None,
            notifications_sent=False,
            analyzed_at=datetime.utcnow(),
            is_sandbox=False,
        )
        db.session.add(log)
        db.session.commit()

        calls = {'note': 0, 'notif': 0}

        class _FakeSvc:
            def __init__(self, *args, **kwargs):
                pass

            def create_candidate_note(self, vetting_log):
                calls['note'] += 1
                return True

            def send_recruiter_notifications(self, vetting_log):
                calls['notif'] += 1
                return 1

        monkeypatch.setattr(cvs_pkg, 'CandidateVettingService', _FakeSvc)

        summary = run_retry_failed_screening_notes(batch_size=10)
        assert summary['status'] == 'ok', summary
        assert summary['notes_created'] == 1, summary
        assert summary['notifications_sent'] == 1, summary
        assert calls == {'note': 1, 'notif': 1}


def test_retry_failed_screening_notes_idle_when_clean(app):
    from app import db
    from models import CandidateVettingLog, VettingConfig
    from tasks.vetting import run_retry_failed_screening_notes

    with app.app_context():
        VettingConfig.query.filter_by(setting_key='vetting_enabled').delete()
        db.session.add(VettingConfig(setting_key='vetting_enabled', setting_value='true'))
        db.session.commit()

        CandidateVettingLog.query.filter(
            CandidateVettingLog.status == 'completed',
            CandidateVettingLog.note_created == False,
        ).delete(synchronize_session=False)
        CandidateVettingLog.query.filter(
            CandidateVettingLog.status == 'completed',
            CandidateVettingLog.note_created == True,
            CandidateVettingLog.notifications_sent == False,
            CandidateVettingLog.highest_match_score >= 65,
        ).delete(synchronize_session=False)
        db.session.commit()

        summary = run_retry_failed_screening_notes(batch_size=10)
        assert summary['status'] == 'idle'
        assert summary['pending'] == 0
