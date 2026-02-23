"""
Test vetting settings template with overflow conditionals.

Verifies that the template renders correctly when there are >30 recommended
and >30 not-recommended candidates, triggering the overflow message lines
(previously buggy: used undefined 'recommended' instead of 'recommended_candidates').
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock


def make_mock_candidate(candidate_id, is_qualified, score=85.0):
    """Create a mock CandidateVettingLog-like object for template rendering."""
    mock = MagicMock()
    mock.id = candidate_id
    mock.bullhorn_candidate_id = 10000 + candidate_id
    mock.candidate_name = f"Test Candidate {candidate_id}"
    mock.candidate_email = f"candidate{candidate_id}@test.com"
    mock.applied_job_id = 100
    mock.applied_job_title = "Test Job"
    mock.status = 'completed'
    mock.is_qualified = is_qualified
    mock.highest_match_score = score if is_qualified else 40.0
    mock.total_jobs_matched = 2 if is_qualified else 0
    mock.note_created = True
    mock.notifications_sent = True
    mock.notification_count = 1
    mock.detected_at = datetime.utcnow() - timedelta(hours=candidate_id)
    mock.analyzed_at = datetime.utcnow() - timedelta(hours=candidate_id - 1)
    mock.created_at = datetime.utcnow() - timedelta(hours=candidate_id)
    mock.updated_at = datetime.utcnow() - timedelta(hours=candidate_id)
    mock.error_message = None
    mock.resume_text = "Sample resume text"
    
    # job_matches relationship — return an empty list (sortable by Jinja2)
    mock.job_matches = []
    
    return mock


class TestVettingOverflowConditionals:
    """Test that overflow conditionals render with >30 candidates."""

    def test_recommended_overflow_renders(self, app):
        """
        Template renders overflow message when >30 recommended candidates.
        
        This tests the fix for the pre-existing bug where the template used
        'recommended|length' instead of 'recommended_candidates|length'.
        """
        from flask import render_template
        
        with app.test_request_context():
            recommended = [make_mock_candidate(i, True) for i in range(35)]
            not_recommended = [make_mock_candidate(i + 100, False) for i in range(5)]
            
            html = render_template('vetting_settings.html',
                settings={
                    'vetting_enabled': True,
                    'send_recruiter_emails': False,
                    'match_threshold': 80,
                    'batch_size': 25,
                    'admin_notification_email': '',
                    'health_alert_email': ''
                },
                stats={
                    'total_processed': 40,
                    'qualified': 35,
                    'notifications_sent': 35,
                    'pending': 0
                },
                recent_activity=[],
                recommended_candidates=recommended,
                not_recommended_candidates=not_recommended,
                job_requirements=[],
                latest_health=None,
                recent_issues=[],
                pending_candidates=[],
                recent_vetting=[],
                active_page='screening'
            )
            
            assert 'Showing 30 of 35' in html
            assert 'recommended candidates' in html

    def test_not_recommended_overflow_renders(self, app):
        """
        Template renders overflow message when >30 not-recommended candidates.
        
        This tests the fix for the pre-existing bug where the template used
        'not_recommended|length' instead of 'not_recommended_candidates|length'.
        """
        from flask import render_template
        
        with app.test_request_context():
            recommended = [make_mock_candidate(i, True) for i in range(5)]
            not_recommended = [make_mock_candidate(i + 100, False) for i in range(35)]
            
            html = render_template('vetting_settings.html',
                settings={
                    'vetting_enabled': True,
                    'send_recruiter_emails': False,
                    'match_threshold': 80,
                    'batch_size': 25,
                    'admin_notification_email': '',
                    'health_alert_email': ''
                },
                stats={
                    'total_processed': 40,
                    'qualified': 5,
                    'notifications_sent': 5,
                    'pending': 0
                },
                recent_activity=[],
                recommended_candidates=recommended,
                not_recommended_candidates=not_recommended,
                job_requirements=[],
                latest_health=None,
                recent_issues=[],
                pending_candidates=[],
                recent_vetting=[],
                active_page='screening'
            )
            
            assert 'Showing 30 of 35' in html
            assert 'not' in html.lower()

    def test_both_overflow_with_pending(self, app):
        """
        Full scenario: 35 recommended, 35 not-recommended, 35 pending.
        
        Also verifies the fixed {{ stats.pending }} Jinja2 expression
        renders correctly in the filterConfig JavaScript block.
        """
        from flask import render_template
        
        with app.test_request_context():
            recommended = [make_mock_candidate(i, True) for i in range(35)]
            not_recommended = [make_mock_candidate(i + 100, False) for i in range(35)]
            pending = [make_mock_candidate(i + 200, False) for i in range(35)]
            for p in pending:
                p.status = 'pending'
            
            html = render_template('vetting_settings.html',
                settings={
                    'vetting_enabled': True,
                    'send_recruiter_emails': False,
                    'match_threshold': 80,
                    'batch_size': 25,
                    'admin_notification_email': '',
                    'health_alert_email': ''
                },
                stats={
                    'total_processed': 70,
                    'qualified': 35,
                    'notifications_sent': 35,
                    'pending': 35
                },
                recent_activity=[],
                recommended_candidates=recommended,
                not_recommended_candidates=not_recommended,
                job_requirements=[],
                latest_health=None,
                recent_issues=[],
                pending_candidates=pending,
                recent_vetting=[],
                active_page='screening'
            )
            
            # Verify the page rendered at all (no TemplateSyntaxError)
            assert len(html) > 0
            # Verify all three overflow messages are present:
            # pending (35), recommended (35), not_recommended (35)
            assert html.count('Showing 30 of 35') == 3
            # Verify {{ stats.pending }} rendered correctly in JS (should be "count: 35")
            assert 'count: 35' in html
