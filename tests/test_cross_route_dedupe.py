"""
Tests for the cross-route apply de-dupe guard.

Background: the SAME application can reach the inbound pipeline as TWO separate
inbox messages with DIFFERENT Message-IDs — one stamped by Microsoft/Exchange
(pulled from the apply@ mailbox via Graph) and one by SendGrid (delivered to the
inbound webhook). The message_id UNIQUE dedupe can't link them, so both would
create a Bullhorn submission (the duplicate Pipeline entries recruiters saw).

`_find_cross_route_sibling` collapses the second copy when a recent sibling
ParsedEmail already submitted the SAME (candidate, job) within the window, while
preserving genuine re-applies (hours/days apart) per the standing
"show repeat applies" decision.
"""
from datetime import datetime, timedelta


def _mk(db, ParsedEmail, candidate_id, job_id, submission_id, *,
        created_min_ago=1, message_id=None, status='completed'):
    """Insert a ParsedEmail row, then back-date created_at deterministically."""
    pe = ParsedEmail(
        message_id=message_id,
        sender_email='info@myticas.com',
        recipient_email='apply@myticas.com',
        subject='Test applied on Job',
        bullhorn_candidate_id=candidate_id,
        bullhorn_job_id=job_id,
        bullhorn_submission_id=submission_id,
        status=status,
    )
    db.session.add(pe)
    db.session.commit()
    pe.created_at = datetime.utcnow() - timedelta(minutes=created_min_ago)
    db.session.commit()
    return pe


class TestCrossRouteSibling:
    def test_within_window_collapses(self, app):
        """A second copy whose sibling already submitted the same (candidate,
        job) seconds ago → sibling found → caller will collapse."""
        from app import db
        from models import ParsedEmail

        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()

            first = _mk(db, ParsedEmail, 111, 222, 99001,
                        created_min_ago=1, message_id='<a@OUTLOOK.COM>')
            second = ParsedEmail(
                message_id='<a@geopod-ismtpd-1>',
                sender_email='info@myticas.com',
                recipient_email='apply@myticas.com',
                subject='Test applied on Job',
                bullhorn_candidate_id=111, bullhorn_job_id=222,
                status='processing')
            db.session.add(second)
            db.session.commit()

            sib = svc._find_cross_route_sibling(second.id, 111, 222)
            assert sib is not None
            assert sib.id == first.id
            assert sib.bullhorn_submission_id == 99001

    def test_outside_window_preserves_reapply(self, app):
        """A genuine re-apply 24h later → no sibling → proceeds normally."""
        from app import db
        from models import ParsedEmail

        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()

            _mk(db, ParsedEmail, 333, 444, 99002, created_min_ago=24 * 60)
            assert svc._find_cross_route_sibling(None, 333, 444) is None

    def test_different_job_not_collapsed(self, app):
        """Same candidate, DIFFERENT job → not a duplicate."""
        from app import db
        from models import ParsedEmail

        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()

            _mk(db, ParsedEmail, 555, 666, 99003, created_min_ago=1)
            assert svc._find_cross_route_sibling(None, 555, 999) is None

    def test_sibling_without_submission_ignored(self, app):
        """A recent sibling that NEVER produced a submission must NOT collapse
        this copy — otherwise a failed/stalled first copy could drop a real
        applicant."""
        from app import db
        from models import ParsedEmail

        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()

            _mk(db, ParsedEmail, 777, 888, None,
                created_min_ago=1, status='processing')
            assert svc._find_cross_route_sibling(None, 777, 888) is None

    def test_disabled_flag_skips_guard(self, app):
        """Master switch off → guard never fires even with a recent sibling."""
        from app import db
        from models import ParsedEmail, VettingConfig

        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()

            VettingConfig.set_value('cross_route_dedupe_enabled', 'false')
            try:
                _mk(db, ParsedEmail, 1212, 1313, 99004, created_min_ago=1)
                assert svc._find_cross_route_sibling(None, 1212, 1313) is None
            finally:
                VettingConfig.set_value('cross_route_dedupe_enabled', 'true')

    def test_zero_window_disables(self, app):
        """Window of 0 minutes disables the guard."""
        from app import db
        from models import ParsedEmail, VettingConfig

        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()

            VettingConfig.set_value('cross_route_dedupe_window_minutes', '0')
            try:
                _mk(db, ParsedEmail, 1414, 1515, 99005, created_min_ago=1)
                assert svc._find_cross_route_sibling(None, 1414, 1515) is None
            finally:
                VettingConfig.set_value('cross_route_dedupe_window_minutes', '30')

    def test_excludes_self(self, app):
        """A row never collapses against itself."""
        from app import db
        from models import ParsedEmail

        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()

            me = _mk(db, ParsedEmail, 1616, 1717, 99006, created_min_ago=1)
            assert svc._find_cross_route_sibling(me.id, 1616, 1717) is None

    def test_process_email_collapses_second_copy(self, app):
        """End-to-end: when a sibling already submitted the same (candidate,
        job), process_email collapses the second transport copy — NO duplicate
        Bullhorn submission, résumé upload, or candidate creation — and marks
        the row status='duplicate'."""
        from unittest.mock import MagicMock, patch
        from app import db
        from models import ParsedEmail

        with app.app_context():
            from email_inbound_service import EmailInboundService

            candidate_id, job_id, submission_id = 4664597, 35214, 770001
            # Sibling: the FIRST transport copy that already produced a
            # submission a minute ago (Exchange/mailbox-pull copy).
            _mk(db, ParsedEmail, candidate_id, job_id, submission_id,
                created_min_ago=1, message_id='<dup@OUTLOOK.COM>')

            svc = EmailInboundService()
            svc.detect_source = MagicMock(return_value='LinkedIn')
            svc.detect_feed = MagicMock(return_value='')
            svc.extract_bullhorn_job_id = MagicMock(return_value=job_id)
            svc.extract_candidate_from_email = MagicMock(return_value={
                'first_name': 'Jane', 'last_name': 'Doe',
                'email': 'jane.doe@example.com', 'phone': '555-0100',
            })
            svc._extract_attachments = MagicMock(return_value=[])
            svc._select_best_resume = MagicMock(return_value=None)
            # Candidate-level dedupe resolves the SECOND copy to the SAME
            # Bullhorn candidate as the sibling.
            svc.find_duplicate_candidate = MagicMock(
                return_value=(candidate_id, 0.95))
            svc.map_to_bullhorn_fields = MagicMock(return_value={})
            svc._build_enrichment_update = MagicMock(return_value={})

            bullhorn = MagicMock()
            bullhorn.get_candidate.return_value = {'id': candidate_id}
            bullhorn.create_job_submission = MagicMock()
            bullhorn.upload_candidate_file = MagicMock()
            bullhorn.create_candidate = MagicMock()
            bullhorn.create_candidate_note = MagicMock()

            payload = {
                'from': 'info@myticas.com', 'to': 'apply@myticas.com',
                'subject': 'Jane Doe has applied on LinkedIn',
                'text': 'application', 'html': 'application',
                'headers': 'Message-ID: <dup@geopod-ismtpd-2>\n',
            }

            with patch('app.get_bullhorn_service', return_value=bullhorn):
                result = svc.process_email(payload)

            assert result['success'] is True
            assert result['is_duplicate'] is True
            assert result['duplicate'] is True
            assert result['submission_id'] == submission_id
            # The whole point: no SECOND submission / upload / candidate.
            bullhorn.create_job_submission.assert_not_called()
            bullhorn.upload_candidate_file.assert_not_called()
            bullhorn.create_candidate.assert_not_called()

            row = ParsedEmail.query.filter_by(
                message_id='<dup@geopod-ismtpd-2>').first()
            assert row is not None
            assert row.status == 'duplicate'
            assert row.bullhorn_submission_id is None
            assert 'Cross-route duplicate' in (row.processing_notes or '')

    def test_window_helper_defaults_and_caps(self, app):
        """Window helper falls back to default on garbage and caps at 24h."""
        from models import VettingConfig

        with app.app_context():
            from email_inbound_service import EmailInboundService
            svc = EmailInboundService()

            VettingConfig.set_value('cross_route_dedupe_window_minutes', 'abc')
            try:
                assert svc._cross_route_dedupe_window_minutes() == 30
                VettingConfig.set_value(
                    'cross_route_dedupe_window_minutes', '99999')
                assert svc._cross_route_dedupe_window_minutes() == 1440
            finally:
                VettingConfig.set_value(
                    'cross_route_dedupe_window_minutes', '30')
