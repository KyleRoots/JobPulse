"""
Task #95 — Stop duplicate recruiter emails on auditor re-vet.

The Quality Auditor's clear_candidate_vetting_state cascade wipes the
CandidateJobMatch.notification_sent flag along with the matches, so
without a durable signal the next vetting cycle's
send_recruiter_notifications would fire a second email even though the
Bullhorn note path correctly skips the duplicate. These tests pin down
the cross-revet dedupe behavior so the bug can't regress.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _make_match(job_id, score=90.0):
    return SimpleNamespace(
        id=None,
        bullhorn_job_id=job_id,
        job_title=f'Job {job_id}',
        match_score=score,
        notification_sent=False,
        notification_sent_at=None,
        is_applied_job=False,
        recruiter_email='r@example.com',
        recruiter_name='Rec',
        prestige_employer=None,
        prestige_boost_applied=False,
        match_summary='ok',
        skills_match='',
        technical_score=score,
    )


class TestLedgerHelpers:
    """Pure helper behaviour — write/read/window."""

    def test_record_then_lookup_returns_pair(self, app):
        from screening.notification import (
            _record_ledger_sent,
            _ledger_recently_sent_pairs,
        )
        from models import RecruiterNotificationLedger

        with app.app_context():
            RecruiterNotificationLedger.query.delete()
            from app import db
            db.session.commit()

            written = _record_ledger_sent(4660264, [11111, 22222], 'qualified')
            assert written == 2

            found = _ledger_recently_sent_pairs(
                4660264, [11111, 22222, 33333], 'qualified',
            )
            assert set(found.keys()) == {11111, 22222}

    def test_lookup_outside_window_returns_empty(self, app):
        from screening.notification import _ledger_recently_sent_pairs
        from models import RecruiterNotificationLedger
        from app import db

        with app.app_context():
            RecruiterNotificationLedger.query.delete()
            db.session.commit()
            old = RecruiterNotificationLedger(
                bullhorn_candidate_id=999,
                bullhorn_job_id=42,
                notification_type='qualified',
                sent_at=datetime.utcnow() - timedelta(hours=48),
            )
            db.session.add(old)
            db.session.commit()

            assert _ledger_recently_sent_pairs(
                999, [42], 'qualified', lookback_hours=24,
            ) == {}

    def test_record_is_idempotent_on_repeat(self, app):
        from screening.notification import _record_ledger_sent
        from models import RecruiterNotificationLedger
        from app import db

        with app.app_context():
            RecruiterNotificationLedger.query.delete()
            db.session.commit()
            _record_ledger_sent(123, [55], 'qualified')
            _record_ledger_sent(123, [55], 'qualified')
            count = RecruiterNotificationLedger.query.filter_by(
                bullhorn_candidate_id=123,
                bullhorn_job_id=55,
                notification_type='qualified',
            ).count()
            assert count == 1

    def test_notification_types_are_independent(self, app):
        from screening.notification import (
            _record_ledger_sent,
            _ledger_recently_sent_pairs,
        )
        from models import RecruiterNotificationLedger
        from app import db

        with app.app_context():
            RecruiterNotificationLedger.query.delete()
            db.session.commit()
            _record_ledger_sent(7, [9], 'qualified')
            assert _ledger_recently_sent_pairs(7, [9], 'qualified') != {}
            assert _ledger_recently_sent_pairs(7, [9], 'prestige') == {}
            assert _ledger_recently_sent_pairs(7, [9], 'location_review') == {}


class TestFilterMatchesByLedger:
    """The filter splits matches into (to_send, suppressed) using the ledger."""

    def test_filter_returns_all_when_ledger_empty(self, app):
        from screening.notification import _filter_matches_by_ledger
        from models import RecruiterNotificationLedger
        from app import db

        with app.app_context():
            RecruiterNotificationLedger.query.delete()
            db.session.commit()
            matches = [_make_match(101), _make_match(102)]
            to_send, suppressed = _filter_matches_by_ledger(
                matches, 555, 'qualified',
            )
            assert len(to_send) == 2
            assert suppressed == []

    def test_filter_suppresses_already_sent_pair(self, app):
        from screening.notification import (
            _filter_matches_by_ledger,
            _record_ledger_sent,
        )
        from models import RecruiterNotificationLedger
        from app import db

        with app.app_context():
            RecruiterNotificationLedger.query.delete()
            db.session.commit()
            _record_ledger_sent(555, [101], 'qualified')

            matches = [_make_match(101), _make_match(102)]
            to_send, suppressed = _filter_matches_by_ledger(
                matches, 555, 'qualified',
            )
            assert [m.bullhorn_job_id for m in to_send] == [102]
            assert [m.bullhorn_job_id for m in suppressed] == [101]


class TestRecruiterEmailDedupe:
    """End-to-end-ish behavior for the qualified-email send path."""

    def _vlog(self, candidate_id=4660264, name='Test Candidate'):
        return SimpleNamespace(
            id=1,
            bullhorn_candidate_id=candidate_id,
            candidate_name=name,
            is_qualified=True,
            applied_job_id=None,
            applied_job_title=None,
            notifications_sent=False,
            notification_count=0,
            highest_match_score=90.0,
            resume_text='',
        )

    def _stub_mixin(self, send_succeeds=True):
        from screening.notification import NotificationMixin

        send_calls = []

        class _Stub(NotificationMixin):
            def is_enabled(self):
                return True

            def get_threshold(self):
                return 75.0

            def get_candidate_resume(self, candidate_id):
                return None, None

            def _send_recruiter_email(self_inner, **kwargs):
                send_calls.append(kwargs)
                return send_succeeds

        return _Stub(), send_calls

    def _seed_config(self):
        """Ensure the kill-switch + admin email rows exist so the test
        path mirrors prod (recruiter emails ON, fallback admin set)."""
        from models import VettingConfig
        from app import db

        for k, v in (
            ('send_recruiter_emails', 'true'),
            ('admin_notification_email', 'admin@example.com'),
        ):
            row = VettingConfig.query.filter_by(setting_key=k).first()
            if row is None:
                db.session.add(VettingConfig(setting_key=k, setting_value=v))
            else:
                row.setting_value = v
        db.session.commit()

    def test_first_send_emails_and_records_ledger(self, app):
        from app import db
        from models import (
            CandidateVettingLog, CandidateJobMatch,
            RecruiterNotificationLedger,
        )

        with app.app_context():
            RecruiterNotificationLedger.query.delete()
            CandidateJobMatch.query.delete()
            CandidateVettingLog.query.delete()
            db.session.commit()
            self._seed_config()

            vlog = CandidateVettingLog(
                bullhorn_candidate_id=4660264,
                candidate_name='Justin Chuang',
                status='completed',
                is_qualified=True,
            )
            db.session.add(vlog)
            db.session.commit()

            match = CandidateJobMatch(
                vetting_log_id=vlog.id,
                bullhorn_job_id=34900,
                job_title='Senior Data Engineer',
                match_score=88.0,
                is_qualified=True,
                notification_sent=False,
                recruiter_email='rec@example.com',
                recruiter_name='Recruiter',
                is_applied_job=True,
            )
            db.session.add(match)
            db.session.commit()

            mixin, calls = self._stub_mixin(send_succeeds=True)
            sent = mixin.send_recruiter_notifications(vlog)

            assert sent == 1
            assert len(calls) == 1, "expected exactly one email send"

            ledger_count = RecruiterNotificationLedger.query.filter_by(
                bullhorn_candidate_id=4660264,
                bullhorn_job_id=34900,
                notification_type='qualified',
            ).count()
            assert ledger_count == 1

    def test_second_send_after_revet_is_suppressed(self, app):
        """The exact production scenario: vlog rebuilt by re-vet,
        match.notification_sent is False on the new row, but the ledger
        from the first send blocks the duplicate email."""
        from app import db
        from models import (
            CandidateVettingLog, CandidateJobMatch,
            RecruiterNotificationLedger,
        )

        with app.app_context():
            RecruiterNotificationLedger.query.delete()
            CandidateJobMatch.query.delete()
            CandidateVettingLog.query.delete()
            db.session.commit()
            self._seed_config()

            # Pre-populate ledger as if first cycle already sent.
            db.session.add(RecruiterNotificationLedger(
                bullhorn_candidate_id=4660264,
                bullhorn_job_id=34900,
                notification_type='qualified',
                sent_at=datetime.utcnow() - timedelta(minutes=3),
            ))
            db.session.commit()

            # Brand-new vlog + match rows, mimicking what the re-vet
            # rebuilds: notification_sent=False on the fresh match.
            vlog = CandidateVettingLog(
                bullhorn_candidate_id=4660264,
                candidate_name='Justin Chuang',
                status='completed',
                is_qualified=True,
            )
            db.session.add(vlog)
            db.session.commit()
            match = CandidateJobMatch(
                vetting_log_id=vlog.id,
                bullhorn_job_id=34900,
                job_title='Senior Data Engineer',
                match_score=88.0,
                is_qualified=True,
                notification_sent=False,
                recruiter_email='rec@example.com',
                recruiter_name='Recruiter',
                is_applied_job=True,
            )
            db.session.add(match)
            db.session.commit()

            mixin, calls = self._stub_mixin(send_succeeds=True)
            sent = mixin.send_recruiter_notifications(vlog)

            assert sent == 0, "second email must be suppressed"
            assert calls == [], "_send_recruiter_email must not be called"

            db.session.refresh(match)
            assert match.notification_sent is True, (
                "suppressed match must still be marked notified so "
                "subsequent regular cycles dedupe via the existing flag"
            )

    def test_revet_to_new_qualifying_pair_still_sends(self, app):
        """If a re-vet newly qualifies a previously-unqualified pair
        (so no prior email exists), the email MUST still send. The
        dedupe is keyed on prior-sent emails, not on the re-vet itself."""
        from app import db
        from models import (
            CandidateVettingLog, CandidateJobMatch,
            RecruiterNotificationLedger,
        )

        with app.app_context():
            RecruiterNotificationLedger.query.delete()
            CandidateJobMatch.query.delete()
            CandidateVettingLog.query.delete()
            db.session.commit()
            self._seed_config()

            # Ledger has an entry for a DIFFERENT job — the candidate
            # was previously emailed for job A, the re-vet now qualifies
            # them for job B for the first time.
            db.session.add(RecruiterNotificationLedger(
                bullhorn_candidate_id=4660264,
                bullhorn_job_id=34900,  # job A — already emailed
                notification_type='qualified',
                sent_at=datetime.utcnow() - timedelta(hours=2),
            ))
            db.session.commit()

            vlog = CandidateVettingLog(
                bullhorn_candidate_id=4660264,
                candidate_name='Justin Chuang',
                status='completed',
                is_qualified=True,
            )
            db.session.add(vlog)
            db.session.commit()

            # New qualifying match is for job B, which has NO ledger row.
            match = CandidateJobMatch(
                vetting_log_id=vlog.id,
                bullhorn_job_id=34901,  # job B — never emailed
                job_title='Data Architect',
                match_score=90.0,
                is_qualified=True,
                notification_sent=False,
                recruiter_email='rec@example.com',
                recruiter_name='Recruiter',
                is_applied_job=True,
            )
            db.session.add(match)
            db.session.commit()

            mixin, calls = self._stub_mixin(send_succeeds=True)
            sent = mixin.send_recruiter_notifications(vlog)

            assert sent == 1, "first-time email for new qualifying pair must send"
            assert len(calls) == 1
            ledger_count = RecruiterNotificationLedger.query.filter_by(
                bullhorn_candidate_id=4660264,
                bullhorn_job_id=34901,
                notification_type='qualified',
            ).count()
            assert ledger_count == 1, "new pair must be recorded for next time"
