"""
Tests for the prestige notification threshold gate.

Rule: the +5 prestige bump is a courtesy boost for candidates currently at
Tier-1 firms. The recruiter should ONLY be notified when the bumped final
score actually meets or exceeds the qualifying threshold. A candidate who
scores 0% raw → 5% post-bump must NOT trigger a recruiter email.
"""
from unittest.mock import MagicMock, patch

import pytest

from screening.notification import NotificationMixin


class _NotifSvc(NotificationMixin):
    """Minimal NotificationMixin instance for unit-testing the gate."""

    def __init__(self, threshold=80.0):
        self._threshold = threshold
        self.email_service = MagicMock()
        self.bullhorn = None

    def is_enabled(self):
        return True

    def get_threshold(self):
        return self._threshold


def _make_match(*, match_score, prestige_employer="Capgemini",
                prestige_boost_applied=True, bullhorn_job_id=34986,
                notification_sent=False, is_applied_job=True,
                recruiter_email="recruit@myticas.com",
                recruiter_name="Chris Halkai",
                job_title="Finance & Accounting Manager"):
    m = MagicMock()
    m.match_score = match_score
    m.prestige_employer = prestige_employer
    m.prestige_boost_applied = prestige_boost_applied
    m.bullhorn_job_id = bullhorn_job_id
    m.notification_sent = notification_sent
    m.notification_sent_at = None
    m.is_applied_job = is_applied_job
    m.recruiter_email = recruiter_email
    m.recruiter_name = recruiter_name
    m.job_title = job_title
    m.match_summary = "AI summary"
    m.skills_match = ""
    return m


def _make_log(name="Prashast Prashast", candidate_id=4133209):
    log = MagicMock()
    log.id = 1
    log.candidate_name = name
    log.bullhorn_candidate_id = candidate_id
    log.is_qualified = False
    return log


def test_gate_blocks_below_threshold(app):
    """The reported bug: candidate scored 5% post-bump (0% raw + 5 prestige).
    Threshold is 80. No email should fire."""
    with app.app_context():
        match = _make_match(match_score=5.0)
        with patch('screening.notification.CandidateJobMatch') as MockM, \
             patch('screening.notification.VettingConfig') as MockCfg:
            MockM.query.filter_by.return_value.filter.return_value.all.return_value = [match]
            MockCfg.query.filter_by.return_value.first.return_value = None

            svc = _NotifSvc(threshold=80.0)
            result = svc._send_prestige_review_notification(_make_log())

            assert result == 0
            svc.email_service.send_html_email.assert_not_called()


def test_gate_blocks_just_below_threshold(app):
    """Score 79 (one point shy of threshold 80) — no email."""
    with app.app_context():
        match = _make_match(match_score=79.0)
        with patch('screening.notification.CandidateJobMatch') as MockM, \
             patch('screening.notification.VettingConfig') as MockCfg:
            MockM.query.filter_by.return_value.filter.return_value.all.return_value = [match]
            MockCfg.query.filter_by.return_value.first.return_value = None

            svc = _NotifSvc(threshold=80.0)
            result = svc._send_prestige_review_notification(_make_log())

            assert result == 0
            svc.email_service.send_html_email.assert_not_called()


def test_gate_allows_at_threshold(app):
    """Score exactly at threshold → email fires (boundary inclusion)."""
    with app.app_context():
        match = _make_match(match_score=80.0)

        send_setting = MagicMock(setting_value='true')
        admin_setting = MagicMock(setting_value='admin@myticas.com')

        def _filter_by(setting_key=None, **kwargs):
            q = MagicMock()
            if setting_key == 'send_recruiter_emails':
                q.first.return_value = send_setting
            elif setting_key == 'admin_notification_email':
                q.first.return_value = admin_setting
            else:
                q.first.return_value = None
            return q

        with patch('screening.notification.CandidateJobMatch') as MockM, \
             patch('screening.notification.VettingConfig') as MockCfg, \
             patch('screening.notification.db') as MockDb:
            MockM.query.filter_by.return_value.filter.return_value.all.return_value = [match]
            MockCfg.query.filter_by.side_effect = _filter_by

            svc = _NotifSvc(threshold=80.0)
            svc.email_service.send_html_email.return_value = True

            result = svc._send_prestige_review_notification(_make_log())

            assert result == 1
            svc.email_service.send_html_email.assert_called_once()


def test_gate_allows_above_threshold(app):
    """Post-bump 85 ≥ threshold 80 → email fires."""
    with app.app_context():
        match = _make_match(match_score=85.0)

        send_setting = MagicMock(setting_value='true')
        admin_setting = MagicMock(setting_value='admin@myticas.com')

        def _filter_by(setting_key=None, **kwargs):
            q = MagicMock()
            if setting_key == 'send_recruiter_emails':
                q.first.return_value = send_setting
            elif setting_key == 'admin_notification_email':
                q.first.return_value = admin_setting
            else:
                q.first.return_value = None
            return q

        with patch('screening.notification.CandidateJobMatch') as MockM, \
             patch('screening.notification.VettingConfig') as MockCfg, \
             patch('screening.notification.db'):
            MockM.query.filter_by.return_value.filter.return_value.all.return_value = [match]
            MockCfg.query.filter_by.side_effect = _filter_by

            svc = _NotifSvc(threshold=80.0)
            svc.email_service.send_html_email.return_value = True

            result = svc._send_prestige_review_notification(_make_log())

            assert result == 1
            svc.email_service.send_html_email.assert_called_once()


def test_gate_filters_mixed_matches(app):
    """When some prestige matches clear and some don't, only the qualifying
    ones drive the email; sub-threshold matches are dropped silently."""
    with app.app_context():
        below = _make_match(match_score=20.0, bullhorn_job_id=11111)
        above = _make_match(match_score=85.0, bullhorn_job_id=22222)

        send_setting = MagicMock(setting_value='true')
        admin_setting = MagicMock(setting_value='admin@myticas.com')

        def _filter_by(setting_key=None, **kwargs):
            q = MagicMock()
            if setting_key == 'send_recruiter_emails':
                q.first.return_value = send_setting
            elif setting_key == 'admin_notification_email':
                q.first.return_value = admin_setting
            else:
                q.first.return_value = None
            return q

        with patch('screening.notification.CandidateJobMatch') as MockM, \
             patch('screening.notification.VettingConfig') as MockCfg, \
             patch('screening.notification.db'):
            MockM.query.filter_by.return_value.filter.return_value.all.return_value = [below, above]
            MockCfg.query.filter_by.side_effect = _filter_by

            svc = _NotifSvc(threshold=80.0)
            svc.email_service.send_html_email.return_value = True

            result = svc._send_prestige_review_notification(_make_log())

            assert result == 1
            # Sent — but only the qualifying match should be flagged notified
            assert above.notification_sent is True
            # Note: the below match is excluded from the email entirely.


def test_gate_returns_zero_when_no_prestige_matches(app):
    """Original early-return path is preserved."""
    with app.app_context():
        with patch('screening.notification.CandidateJobMatch') as MockM:
            MockM.query.filter_by.return_value.filter.return_value.all.return_value = []

            svc = _NotifSvc(threshold=80.0)
            result = svc._send_prestige_review_notification(_make_log())

            assert result == 0
            svc.email_service.send_html_email.assert_not_called()


def test_gate_honors_per_job_threshold_override(app):
    """A match with a per-job custom threshold (70) below the global (80)
    should be allowed through if its score (75) clears the per-job bar.

    This locks in the resolve_match_threshold integration: the prestige gate
    must use the same per-job override that determined is_qualified status,
    not the global threshold."""
    with app.app_context():
        match = _make_match(match_score=75.0, bullhorn_job_id=34986)

        send_setting = MagicMock(setting_value='true')
        admin_setting = MagicMock(setting_value='admin@myticas.com')

        def _filter_by(setting_key=None, **kwargs):
            q = MagicMock()
            if setting_key == 'send_recruiter_emails':
                q.first.return_value = send_setting
            elif setting_key == 'admin_notification_email':
                q.first.return_value = admin_setting
            else:
                q.first.return_value = None
            return q

        custom_req = MagicMock()
        custom_req.bullhorn_job_id = 34986
        custom_req.vetting_threshold = 70.0

        with patch('screening.notification.CandidateJobMatch') as MockM, \
             patch('screening.notification.VettingConfig') as MockCfg, \
             patch('screening.notification.db'), \
             patch('models.JobVettingRequirements') as MockJVR:
            MockM.query.filter_by.return_value.filter.return_value.all.return_value = [match]
            MockCfg.query.filter_by.side_effect = _filter_by
            MockJVR.query.filter.return_value.all.return_value = [custom_req]

            svc = _NotifSvc(threshold=80.0)  # Global threshold
            svc.email_service.send_html_email.return_value = True

            result = svc._send_prestige_review_notification(_make_log())

            # 75 < global 80 would normally block, but per-job threshold of 70
            # lets it through.
            assert result == 1
            svc.email_service.send_html_email.assert_called_once()


def test_gate_honors_per_job_threshold_when_higher(app):
    """The mirror case: per-job threshold (90) above global (80). A score
    of 85 clears the global but NOT the per-job — must be blocked."""
    with app.app_context():
        match = _make_match(match_score=85.0, bullhorn_job_id=34986)

        custom_req = MagicMock()
        custom_req.bullhorn_job_id = 34986
        custom_req.vetting_threshold = 90.0

        with patch('screening.notification.CandidateJobMatch') as MockM, \
             patch('screening.notification.VettingConfig'), \
             patch('models.JobVettingRequirements') as MockJVR:
            MockM.query.filter_by.return_value.filter.return_value.all.return_value = [match]
            MockJVR.query.filter.return_value.all.return_value = [custom_req]

            svc = _NotifSvc(threshold=80.0)
            result = svc._send_prestige_review_notification(_make_log())

            assert result == 0
            svc.email_service.send_html_email.assert_not_called()
