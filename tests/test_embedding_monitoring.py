"""
Tests for embedding filter monitoring features:
- Daily digest email (embedding_digest_service.py)
- Embedding audit page routes
- CSV export endpoints
"""

import json
import os
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


# ═══════════════════════════════════════════
# DAILY DIGEST EMAIL TESTS
# ═══════════════════════════════════════════

class TestGetDigestData:
    """Tests for digest data aggregation."""

    def test_returns_all_required_keys(self, app):
        """Digest data should contain all expected sections."""
        with app.app_context():
            from embedding_digest_service import get_digest_data

            data = get_digest_data()

            required_keys = [
                'period_start', 'period_end',
                'total_evaluated', 'total_filtered', 'total_passed',
                'filter_rate', 'daily_savings', 'filter_savings', 'model_savings',
                'borderline_pairs', 'borderline_count',
                'total_escalated', 'material_changes', 'threshold_crossings',
                'band_60_69', 'band_70_79', 'band_80_85',
            ]
            for key in required_keys:
                assert key in data, f"Missing key: {key}"

    def test_empty_period_returns_zeros(self, app):
        """With no data in period, all counts should be zero."""
        with app.app_context():
            from embedding_digest_service import get_digest_data

            # Far-future 'since' ensures no records exist
            data = get_digest_data(since=datetime(2099, 1, 1))

            assert data['total_filtered'] == 0
            assert data['total_passed'] == 0
            assert data['total_evaluated'] == 0
            assert data['filter_rate'] == 0
            assert data['total_escalated'] == 0
            assert data['borderline_count'] == 0

    def test_with_filter_logs(self, app):
        """Filtered logs should be aggregated correctly."""
        with app.app_context():
            from app import db
            from models import EmbeddingFilterLog
            from embedding_digest_service import get_digest_data

            # Create test filter logs
            now = datetime.utcnow()
            logs = [
                EmbeddingFilterLog(
                    bullhorn_candidate_id=100 + i,
                    candidate_name=f'Candidate {i}',
                    bullhorn_job_id=200 + i,
                    job_title=f'Job {i}',
                    similarity_score=0.10 + (i * 0.05),
                    threshold_used=0.25,
                    filtered_at=now - timedelta(hours=1)
                )
                for i in range(5)
            ]
            for log in logs:
                db.session.add(log)
            db.session.commit()

            data = get_digest_data(since=now - timedelta(hours=2))

            assert data['total_filtered'] == 5

            # Cleanup
            for log in logs:
                db.session.delete(log)
            db.session.commit()

    def test_borderline_pairs_filtered(self, app):
        """Only pairs with 0.20-0.30 similarity should appear as borderline."""
        with app.app_context():
            from app import db
            from models import EmbeddingFilterLog
            from embedding_digest_service import get_digest_data

            now = datetime.utcnow()
            # Create one borderline and one not borderline
            borderline = EmbeddingFilterLog(
                bullhorn_candidate_id=500,
                candidate_name='Borderline',
                bullhorn_job_id=600,
                job_title='Close Match',
                similarity_score=0.24,
                threshold_used=0.25,
                filtered_at=now - timedelta(hours=1)
            )
            not_borderline = EmbeddingFilterLog(
                bullhorn_candidate_id=501,
                candidate_name='Clear Filter',
                bullhorn_job_id=601,
                job_title='No Match',
                similarity_score=0.05,
                threshold_used=0.25,
                filtered_at=now - timedelta(hours=1)
            )
            db.session.add_all([borderline, not_borderline])
            db.session.commit()

            data = get_digest_data(since=now - timedelta(hours=2))

            assert data['borderline_count'] >= 1
            borderline_names = [p['candidate_name'] for p in data['borderline_pairs']]
            assert 'Borderline' in borderline_names
            assert 'Clear Filter' not in borderline_names

            # Cleanup
            db.session.delete(borderline)
            db.session.delete(not_borderline)
            db.session.commit()

    def test_escalation_band_stats(self, app):
        """Escalation stats should be broken down by score band."""
        with app.app_context():
            from app import db
            from models import EscalationLog
            from embedding_digest_service import get_digest_data

            now = datetime.utcnow()
            logs = [
                EscalationLog(
                    bullhorn_candidate_id=700,
                    candidate_name='Band60',
                    bullhorn_job_id=800,
                    job_title='Job A',
                    mini_score=65.0,
                    gpt4o_score=72.0,
                    score_delta=7.0,
                    material_change=True,
                    threshold_used=80.0,
                    crossed_threshold=False,
                    escalated_at=now - timedelta(hours=1)
                ),
                EscalationLog(
                    bullhorn_candidate_id=701,
                    candidate_name='Band80',
                    bullhorn_job_id=801,
                    job_title='Job B',
                    mini_score=82.0,
                    gpt4o_score=79.0,
                    score_delta=-3.0,
                    material_change=False,
                    threshold_used=80.0,
                    crossed_threshold=True,
                    escalated_at=now - timedelta(hours=1)
                ),
            ]
            for log in logs:
                db.session.add(log)
            db.session.commit()

            data = get_digest_data(since=now - timedelta(hours=2))

            assert data['total_escalated'] >= 2
            assert data['band_60_69']['count'] >= 1
            assert data['band_80_85']['count'] >= 1
            assert data['band_80_85']['crossed'] >= 1

            # Cleanup
            for log in logs:
                db.session.delete(log)
            db.session.commit()


class TestBuildDigestHtml:
    """Tests for HTML email template generation."""

    def test_html_contains_key_sections(self, app):
        """Generated HTML should contain all expected sections."""
        with app.app_context():
            from embedding_digest_service import build_digest_html

            data = {
                'period_start': datetime(2026, 2, 11, 0, 0),
                'period_end': datetime(2026, 2, 12, 0, 0),
                'total_evaluated': 100,
                'total_filtered': 30,
                'total_passed': 70,
                'filter_rate': 30.0,
                'daily_savings': 2.45,
                'filter_savings': 0.09,
                'model_savings': 1.89,
                'borderline_pairs': [],
                'borderline_count': 0,
                'total_escalated': 5,
                'material_changes': 2,
                'threshold_crossings': 1,
                'band_60_69': {'count': 2, 'material': 1, 'crossed': 0, 'avg_delta': 4.5},
                'band_70_79': {'count': 2, 'material': 1, 'crossed': 1, 'avg_delta': 6.0},
                'band_80_85': {'count': 1, 'material': 0, 'crossed': 0, 'avg_delta': 2.0},
            }

            html = build_digest_html(data)

            assert 'Embedding Filter Daily Digest' in html
            assert '100' in html  # total_evaluated
            assert '30' in html   # total_filtered
            assert '$2.45' in html  # daily savings
            assert 'Escalation Effectiveness' in html

    def test_html_includes_borderline_pairs(self, app):
        """Borderline pairs table should render when data exists."""
        with app.app_context():
            from embedding_digest_service import build_digest_html

            data = {
                'period_start': datetime(2026, 2, 11, 0, 0),
                'period_end': datetime(2026, 2, 12, 0, 0),
                'total_evaluated': 50,
                'total_filtered': 10,
                'total_passed': 40,
                'filter_rate': 20.0,
                'daily_savings': 1.00,
                'filter_savings': 0.03,
                'model_savings': 1.08,
                'borderline_pairs': [
                    {
                        'candidate_name': 'Jane Doe',
                        'job_title': 'Python Engineer',
                        'similarity_score': 0.2345,
                        'filtered_at': datetime.utcnow()
                    }
                ],
                'borderline_count': 1,
                'total_escalated': 0,
                'material_changes': 0,
                'threshold_crossings': 0,
                'band_60_69': {'count': 0, 'material': 0, 'crossed': 0, 'avg_delta': 0},
                'band_70_79': {'count': 0, 'material': 0, 'crossed': 0, 'avg_delta': 0},
                'band_80_85': {'count': 0, 'material': 0, 'crossed': 0, 'avg_delta': 0},
            }

            html = build_digest_html(data)

            assert 'Jane Doe' in html
            assert 'Python Engineer' in html
            assert '0.2345' in html


class TestSendDailyDigest:
    """Tests for end-to-end digest sending."""

    def test_send_digest_calls_email_service(self, app):
        """send_daily_digest should call EmailService.send_html_email."""
        with app.app_context():
            with patch('email_service.EmailService') as mock_email_cls:
                mock_email = MagicMock()
                mock_email.send_html_email.return_value = True
                mock_email_cls.return_value = mock_email

                from embedding_digest_service import send_daily_digest

                result = send_daily_digest()

                assert result is True
                mock_email.send_html_email.assert_called_once()
                call_kwargs = mock_email.send_html_email.call_args
                assert call_kwargs[1]['to_email'] == 'kroots@myticas.com'
                assert call_kwargs[1]['notification_type'] == 'embedding_digest'

    def test_send_digest_returns_false_on_failure(self, app):
        """send_daily_digest should return False when email fails."""
        with app.app_context():
            with patch('email_service.EmailService') as mock_email_cls:
                mock_email = MagicMock()
                mock_email.send_html_email.return_value = False
                mock_email_cls.return_value = mock_email

                from embedding_digest_service import send_daily_digest

                result = send_daily_digest()

                assert result is False

    def test_send_digest_handles_exception(self, app):
        """send_daily_digest should handle exceptions gracefully."""
        with app.app_context():
            with patch('email_service.EmailService') as mock_email_cls:
                mock_email_cls.side_effect = Exception("SendGrid error")

                from embedding_digest_service import send_daily_digest

                result = send_daily_digest()

                assert result is False


# ═══════════════════════════════════════════
# EMBEDDING AUDIT ROUTE TESTS
# ═══════════════════════════════════════════

class TestEmbeddingAuditPage:
    """Tests for the /vetting/embedding-audit route."""

    def test_audit_page_requires_login(self, client, app):
        """Audit page should redirect unauthenticated users."""
        with app.app_context():
            response = client.get('/screening/embedding-audit')
            assert response.status_code in (302, 308)

    def test_audit_page_renders(self, authenticated_client, app):
        """Audit page should render for authenticated users."""
        with app.app_context():
            response = authenticated_client.get('/screening/embedding-audit')
            assert response.status_code == 200
            assert b'Embedding Filter Audit' in response.data

    def test_audit_page_has_summary_banner(self, authenticated_client, app):
        """Audit page should show summary metrics."""
        with app.app_context():
            response = authenticated_client.get('/screening/embedding-audit')
            assert response.status_code == 200
            assert b'Filtered Today' in response.data
            assert b'Escalated Today' in response.data
            assert b'Filter Rate' in response.data

    def test_audit_page_has_tabs(self, authenticated_client, app):
        """Audit page should have Filtered Pairs and Escalations tabs."""
        with app.app_context():
            response = authenticated_client.get('/screening/embedding-audit')
            assert response.status_code == 200
            assert b'Filtered Pairs' in response.data
            assert b'Escalations' in response.data

    def test_audit_page_filters_by_date(self, authenticated_client, app):
        """Audit page should accept date filter parameters."""
        with app.app_context():
            response = authenticated_client.get(
                '/screening/embedding-audit?date_from=2026-01-01&date_to=2026-01-31'
            )
            assert response.status_code == 200

    def test_audit_page_filters_by_similarity(self, authenticated_client, app):
        """Audit page should accept similarity range filter parameters."""
        with app.app_context():
            response = authenticated_client.get(
                '/screening/embedding-audit?sim_min=0.1&sim_max=0.3'
            )
            assert response.status_code == 200

    def test_audit_page_escalation_tab(self, authenticated_client, app):
        """Escalations tab should render with score band filter."""
        with app.app_context():
            response = authenticated_client.get(
                '/screening/embedding-audit?tab=escalations&score_band=60-69'
            )
            assert response.status_code == 200

    def test_audit_page_sort_by_similarity(self, authenticated_client, app):
        """Filtered pairs should be sortable by similarity."""
        with app.app_context():
            response = authenticated_client.get(
                '/screening/embedding-audit?sort=similarity'
            )
            assert response.status_code == 200

    def test_audit_page_sort_escalations_by_delta(self, authenticated_client, app):
        """Escalations should be sortable by score delta."""
        with app.app_context():
            response = authenticated_client.get(
                '/screening/embedding-audit?tab=escalations&esc_sort=delta'
            )
            assert response.status_code == 200


class TestCSVExports:
    """Tests for CSV export endpoints."""

    def test_filtered_csv_requires_login(self, client, app):
        """Filtered CSV export should redirect unauthenticated users."""
        with app.app_context():
            response = client.get('/screening/embedding-audit/filtered-csv')
            assert response.status_code in (302, 308)

    def test_filtered_csv_returns_csv(self, authenticated_client, app):
        """Filtered CSV export should return CSV content type."""
        with app.app_context():
            response = authenticated_client.get('/screening/embedding-audit/filtered-csv')
            assert response.status_code == 200
            assert 'text/csv' in response.content_type
            # Should have CSV header row
            content = response.data.decode('utf-8')
            assert 'Date,Candidate ID,Candidate Name' in content

    def test_escalations_csv_requires_login(self, client, app):
        """Escalations CSV export should redirect unauthenticated users."""
        with app.app_context():
            response = client.get('/screening/embedding-audit/escalations-csv')
            assert response.status_code in (302, 308)

    def test_escalations_csv_returns_csv(self, authenticated_client, app):
        """Escalations CSV export should return CSV content type."""
        with app.app_context():
            response = authenticated_client.get('/screening/embedding-audit/escalations-csv')
            assert response.status_code == 200
            assert 'text/csv' in response.content_type
            content = response.data.decode('utf-8')
            assert 'GPT-4o-mini Score,GPT-4o Score' in content

    def test_filtered_csv_with_data(self, authenticated_client, app):
        """Filtered CSV should include data from EmbeddingFilterLog."""
        with app.app_context():
            from app import db
            from models import EmbeddingFilterLog

            log = EmbeddingFilterLog(
                bullhorn_candidate_id=900,
                candidate_name='CSV Test',
                bullhorn_job_id=901,
                job_title='CSV Job',
                similarity_score=0.15,
                threshold_used=0.25,
                filtered_at=datetime.utcnow() - timedelta(hours=1)
            )
            db.session.add(log)
            db.session.commit()

            response = authenticated_client.get('/screening/embedding-audit/filtered-csv')
            content = response.data.decode('utf-8')

            assert 'CSV Test' in content
            assert 'CSV Job' in content
            assert '0.1500' in content

            # Cleanup
            db.session.delete(log)
            db.session.commit()

    def test_escalations_csv_with_data(self, authenticated_client, app):
        """Escalations CSV should include data from EscalationLog."""
        with app.app_context():
            from app import db
            from models import EscalationLog

            log = EscalationLog(
                bullhorn_candidate_id=910,
                candidate_name='Esc CSV Test',
                bullhorn_job_id=911,
                job_title='Esc Job',
                mini_score=72.0,
                gpt4o_score=80.0,
                score_delta=8.0,
                material_change=True,
                threshold_used=80.0,
                crossed_threshold=True,
                escalated_at=datetime.utcnow() - timedelta(hours=1)
            )
            db.session.add(log)
            db.session.commit()

            response = authenticated_client.get('/screening/embedding-audit/escalations-csv')
            content = response.data.decode('utf-8')

            assert 'Esc CSV Test' in content
            assert 'Esc Job' in content
            assert 'Yes' in content  # material_change and crossed_threshold

            # Cleanup
            db.session.delete(log)
            db.session.commit()


class TestSendDigestRoute:
    """Tests for the /vetting/send-digest endpoint."""

    def test_send_digest_requires_login(self, client, app):
        """Digest trigger endpoint should redirect unauthenticated users."""
        with app.app_context():
            response = client.post('/screening/send-digest')
            assert response.status_code in (302, 308)

    def test_send_digest_triggers_email(self, authenticated_client, app):
        """POST to send-digest should trigger the digest email."""
        with app.app_context():
            with patch('embedding_digest_service.send_daily_digest') as mock_send:
                mock_send.return_value = True

                response = authenticated_client.post(
                    '/screening/send-digest',
                    follow_redirects=True
                )

                assert response.status_code == 200
