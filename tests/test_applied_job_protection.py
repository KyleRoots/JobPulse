"""
Tests for applied position protection in the vetting pipeline.

Verifies that the candidate's applied job is:
1. Always protected from the embedding pre-filter
2. Injected from Bullhorn when not in monitored tearsheets
3. Properly labeled in the Bullhorn note
4. Handles edge cases (closed jobs, invalid IDs, missing applied_job_id)
"""

import pytest
from unittest.mock import Mock, MagicMock, patch


class TestAppliedJobFilterProtection:
    """The applied job must bypass the embedding pre-filter."""
    
    @patch('candidate_vetting_service.BullhornService')
    def test_applied_job_excluded_from_filter_input(self, mock_bullhorn):
        """Applied job should not be passed to filter_relevant_jobs()."""
        from candidate_vetting_service import CandidateVettingService
        
        service = CandidateVettingService()
        
        # Create mock vetting log with applied_job_id
        vetting_log = Mock()
        vetting_log.applied_job_id = 100
        
        jobs_to_analyze = [
            {'id': 100, 'title': 'Applied Job'},
            {'id': 200, 'title': 'Other Job A'},
            {'id': 300, 'title': 'Other Job B'},
        ]
        
        # Track what gets passed to filter_relevant_jobs
        captured_jobs = []
        def mock_filter(resume_text, jobs, candidate_info, vetting_log_id):
            captured_jobs.extend(jobs)
            return jobs, 0  # pass all through
        
        service.embedding_service = Mock()
        service.embedding_service.filter_relevant_jobs = mock_filter
        service.embedding_service.is_filter_enabled.return_value = True
        
        # Simulate the protection logic from process_candidate
        applied_job_entry = None
        for j in jobs_to_analyze:
            if j.get('id') == vetting_log.applied_job_id:
                applied_job_entry = j
                break
        
        non_applied_jobs = (
            [j for j in jobs_to_analyze if j.get('id') != vetting_log.applied_job_id]
            if applied_job_entry else jobs_to_analyze
        )
        
        filtered_jobs, filtered_count = service.embedding_service.filter_relevant_jobs(
            "resume text", non_applied_jobs, {'id': 1, 'name': 'Test'}, 1
        )
        
        # Applied job (id=100) should NOT be in the filter input
        filter_ids = [j['id'] for j in captured_jobs]
        assert 100 not in filter_ids, "Applied job should not be passed to embedding filter"
        assert 200 in filter_ids
        assert 300 in filter_ids
    
    def test_applied_job_reinserted_after_filter(self):
        """Applied job should be re-added to results even if filter would have dropped it."""
        applied_job = {'id': 100, 'title': 'Applied Job'}
        other_job = {'id': 200, 'title': 'Other Job'}
        
        # Simulate: embedding filter returned only other_job (applied was excluded from input)
        filtered_jobs = [other_job]
        
        # Re-add applied job (simulating the protection logic)
        applied_job_entry = applied_job
        if applied_job_entry not in filtered_jobs:
            filtered_jobs.insert(0, applied_job_entry)
        
        # Applied job should be first in the list
        assert filtered_jobs[0]['id'] == 100, "Applied job should be inserted at front"
        assert len(filtered_jobs) == 2
    
    def test_applied_job_not_duplicated_if_naturally_passes(self):
        """If applied job would pass the filter anyway, don't add it twice."""
        applied_job = {'id': 100, 'title': 'Applied Job'}
        other_job = {'id': 200, 'title': 'Other Job'}
        
        # Simulate: filter returned both (applied job passed naturally)
        filtered_jobs = [applied_job, other_job]
        
        # Protection logic check
        applied_job_entry = applied_job
        if applied_job_entry not in filtered_jobs:
            filtered_jobs.insert(0, applied_job_entry)
        
        # Should still only have 2, not 3
        assert len(filtered_jobs) == 2
        assert sum(1 for j in filtered_jobs if j['id'] == 100) == 1
    
    def test_no_applied_job_id_passes_all_to_filter(self):
        """Without applied_job_id, all jobs go through the filter normally."""
        vetting_log_applied_job_id = None
        
        jobs = [
            {'id': 100, 'title': 'Job A'},
            {'id': 200, 'title': 'Job B'},
        ]
        
        # No applied job → non_applied_jobs = all jobs
        applied_job_entry = None
        if vetting_log_applied_job_id:
            for j in jobs:
                if j.get('id') == vetting_log_applied_job_id:
                    applied_job_entry = j
                    break
        
        non_applied_jobs = (
            [j for j in jobs if j.get('id') != vetting_log_applied_job_id]
            if applied_job_entry else jobs
        )
        
        assert len(non_applied_jobs) == 2, "Without applied_job_id, all jobs should go to filter"


class TestAppliedJobInjection:
    """The applied job must be fetched from Bullhorn when not in tearsheets."""
    
    @patch('candidate_vetting_service.BullhornService')
    def test_fetch_applied_job_returns_open_job(self, mock_bullhorn_cls):
        """_fetch_applied_job returns open jobs with correct fields."""
        from candidate_vetting_service import CandidateVettingService
        
        service = CandidateVettingService()
        
        mock_bullhorn = Mock()
        mock_bullhorn.rest_token = 'test_token'
        mock_bullhorn.base_url = 'https://rest.bullhornstaffing.com/rest-services/abc123/'
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'data': {
                'id': 33615,
                'title': 'UX Designer',
                'isOpen': True,
                'status': 'Accepting Candidates',
                'description': 'Design user interfaces',
                'assignedUsers': {'data': []},
                'address': {'city': 'Austin', 'state': 'TX'}
            }
        }
        mock_bullhorn.session.get.return_value = mock_response
        mock_bullhorn.get_user_emails.return_value = {}
        
        result = service._fetch_applied_job(mock_bullhorn, 33615)
        
        assert result is not None
        assert result['id'] == 33615
        assert result['title'] == 'UX Designer'
        assert result.get('_injected_applied_job') is True
    
    @patch('candidate_vetting_service.BullhornService')
    def test_fetch_applied_job_injects_closed_job(self, mock_bullhorn_cls):
        """Applied-job path injects closed jobs so APPLIED POSITION is always scored."""
        from candidate_vetting_service import CandidateVettingService
        
        service = CandidateVettingService()
        
        mock_bullhorn = Mock()
        mock_bullhorn.rest_token = 'test_token'
        mock_bullhorn.base_url = 'https://rest.bullhornstaffing.com/rest-services/abc123/'
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'data': {
                'id': 33615,
                'title': 'Closed Position',
                'isOpen': False,
                'status': 'Closed',
                'assignedUsers': {'data': []}
            }
        }
        mock_bullhorn.session.get.return_value = mock_response
        mock_bullhorn.get_user_emails.return_value = {}
        
        result = service._fetch_applied_job(mock_bullhorn, 33615)
        
        assert result is not None, "Closed applied jobs must still be injected for transparency"
        assert result['id'] == 33615
        assert result.get('_injected_applied_job') is True

    @patch('candidate_vetting_service.BullhornService')
    def test_fetch_applied_job_injects_half_closed_job(self, mock_bullhorn_cls):
        """Regression for Zoya Zaidi / job 35421: isOpen=False but status left as
        'Accepting Candidates' must still be injected on the applied-job path.

        Tearsheet browsing continues to use strict is_job_eligible; only
        applied-job injection is exempt so recruiters always see APPLIED POSITION.
        """
        from candidate_vetting_service import CandidateVettingService

        service = CandidateVettingService()

        mock_bullhorn = Mock()
        mock_bullhorn.rest_token = 'test_token'
        mock_bullhorn.base_url = 'https://rest.bullhornstaffing.com/rest-services/abc123/'

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'data': {
                'id': 35421,
                'title': 'Talent Acquisition Partner',
                'isOpen': False,
                'status': 'Accepting Candidates',
                'assignedUsers': {'data': []},
            }
        }
        mock_bullhorn.session.get.return_value = mock_response
        mock_bullhorn.get_user_emails.return_value = {}

        result = service._fetch_applied_job(mock_bullhorn, 35421)
        assert result is not None, "Half-closed applied jobs must be injected"
        assert result['id'] == 35421
        assert result.get('_injected_applied_job') is True

    @patch('candidate_vetting_service.BullhornService')
    def test_fetch_applied_job_injects_ineligible_status(self, mock_bullhorn_cls):
        """Applied-job path injects even when status is in INELIGIBLE_STATUSES."""
        from candidate_vetting_service import CandidateVettingService

        service = CandidateVettingService()

        mock_bullhorn = Mock()
        mock_bullhorn.rest_token = 'test_token'
        mock_bullhorn.base_url = 'https://rest.bullhornstaffing.com/rest-services/abc123/'

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'data': {
                'id': 40000,
                'title': 'Filled Role',
                'isOpen': True,
                'status': 'Filled',
                'assignedUsers': {'data': []},
            }
        }
        mock_bullhorn.session.get.return_value = mock_response
        mock_bullhorn.get_user_emails.return_value = {}

        result = service._fetch_applied_job(mock_bullhorn, 40000)
        assert result is not None, "Filled applied jobs must still be injected for transparency"
        assert result.get('_injected_applied_job') is True

    @patch('candidate_vetting_service.BullhornService')
    def test_fetch_applied_job_returns_none_for_invalid_id(self, mock_bullhorn_cls):
        """_fetch_applied_job returns None for non-existent job IDs."""
        from candidate_vetting_service import CandidateVettingService
        
        service = CandidateVettingService()
        
        mock_bullhorn = Mock()
        mock_bullhorn.rest_token = 'test_token'
        mock_bullhorn.base_url = 'https://rest.bullhornstaffing.com/rest-services/abc123/'
        
        mock_response = Mock()
        mock_response.status_code = 404
        mock_bullhorn.session.get.return_value = mock_response
        
        result = service._fetch_applied_job(mock_bullhorn, 999999)
        
        assert result is None
    
    @patch('candidate_vetting_service.BullhornService')
    def test_fetch_applied_job_returns_none_without_bullhorn(self, mock_bullhorn_cls):
        """_fetch_applied_job returns None when bullhorn service is unavailable."""
        from candidate_vetting_service import CandidateVettingService
        
        service = CandidateVettingService()
        
        result = service._fetch_applied_job(None, 33615)
        assert result is None
        
        # Also test with bullhorn that has no rest_token
        mock_bullhorn = Mock()
        mock_bullhorn.rest_token = None
        result = service._fetch_applied_job(mock_bullhorn, 33615)
        assert result is None
    
    @patch('candidate_vetting_service.BullhornService')
    def test_fetch_applied_job_handles_api_exception(self, mock_bullhorn_cls):
        """_fetch_applied_job handles API exceptions gracefully."""
        from candidate_vetting_service import CandidateVettingService
        
        service = CandidateVettingService()
        
        mock_bullhorn = Mock()
        mock_bullhorn.rest_token = 'test_token'
        mock_bullhorn.base_url = 'https://rest.bullhornstaffing.com/rest-services/abc123/'
        mock_bullhorn.session.get.side_effect = Exception("Connection timeout")
        
        # Should not raise, should return None
        result = service._fetch_applied_job(mock_bullhorn, 33615)
        assert result is None
    
    @patch('candidate_vetting_service.BullhornService')
    def test_fetch_applied_job_enriches_user_emails(self, mock_bullhorn_cls):
        """_fetch_applied_job enriches assignedUsers with email addresses."""
        from candidate_vetting_service import CandidateVettingService
        
        service = CandidateVettingService()
        
        mock_bullhorn = Mock()
        mock_bullhorn.rest_token = 'test_token'
        mock_bullhorn.base_url = 'https://rest.bullhornstaffing.com/rest-services/abc123/'
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'data': {
                'id': 33615,
                'title': 'UX Designer',
                'isOpen': True,
                'status': 'Accepting Candidates',
                'assignedUsers': {
                    'data': [
                        {'id': 42, 'firstName': 'John', 'lastName': 'Doe'}
                    ]
                }
            }
        }
        mock_bullhorn.session.get.return_value = mock_response
        mock_bullhorn.get_user_emails.return_value = {
            42: {'email': 'john@company.com'}
        }
        
        result = service._fetch_applied_job(mock_bullhorn, 33615)
        
        assert result is not None
        users = result['assignedUsers']['data']
        assert users[0]['email'] == 'john@company.com'
        mock_bullhorn.get_user_emails.assert_called_once_with([42])


class TestAppliedJobInjectionIntegration:
    """Integration-style tests for the applied job injection in process_candidate flow."""

    def test_injection_check_finds_job_in_tearsheets(self):
        """When applied job IS in tearsheets, no injection needed."""
        applied_job_id = 100
        jobs = [
            {'id': 100, 'title': 'Applied Job'},
            {'id': 200, 'title': 'Other Job'},
        ]
        
        applied_in_tearsheets = any(j.get('id') == applied_job_id for j in jobs)
        assert applied_in_tearsheets is True
    
    def test_injection_check_detects_missing_job(self):
        """When applied job is NOT in tearsheets, injection is needed."""
        applied_job_id = 300
        jobs = [
            {'id': 100, 'title': 'Job A'},
            {'id': 200, 'title': 'Job B'},
        ]
        
        applied_in_tearsheets = any(j.get('id') == applied_job_id for j in jobs)
        assert applied_in_tearsheets is False


class TestAppliedJobNoteLabeling:
    """Verify note formatting labels applied jobs correctly."""
    
    def test_qualified_applied_job_labeled_correctly(self):
        """Qualified applied job should show 'APPLIED POSITION (QUALIFIED)' label."""
        # Simulate the note generation logic for qualified + applied
        applied_match = Mock()
        applied_match.is_applied_job = True
        applied_match.bullhorn_job_id = 33615
        applied_match.job_title = 'UX Designer'
        applied_match.match_score = 90.0
        applied_match.match_summary = 'Strong match'
        applied_match.skills_match = 'All skills matched'
        
        other_match = Mock()
        other_match.is_applied_job = False
        other_match.bullhorn_job_id = 33620
        other_match.job_title = 'Product Designer'
        other_match.match_score = 85.0
        other_match.match_summary = 'Good match'
        other_match.skills_match = 'Most skills matched'
        
        qualified_matches = [applied_match, other_match]
        
        # Run the separation logic from create_candidate_note
        applied = None
        other_qualified = []
        for match in qualified_matches:
            if match.is_applied_job:
                applied = match
            else:
                other_qualified.append(match)
        
        assert applied is not None
        assert applied.bullhorn_job_id == 33615
        assert len(other_qualified) == 1
    
    def test_not_qualified_applied_job_labeled_correctly(self):
        """Not-qualified applied job should show 'APPLIED POSITION' label (not TOP ANALYSIS)."""
        # Simulate the note generation for not-qualified
        applied_match = Mock()
        applied_match.is_applied_job = True
        applied_match.bullhorn_job_id = 33615
        applied_match.job_title = 'Data Scientist'
        applied_match.match_score = 45.0
        applied_match.gaps_identified = 'Missing ML experience'
        
        other_match = Mock()
        other_match.is_applied_job = False
        other_match.bullhorn_job_id = 33620
        other_match.job_title = 'Business Analyst'
        other_match.match_score = 70.0
        other_match.gaps_identified = ''
        
        matches = [applied_match, other_match]
        
        applied = None
        other_matches = []
        for match in matches:
            if match.is_applied_job:
                applied = match
            else:
                other_matches.append(match)
        
        # Note should use "APPLIED POSITION:" not "TOP ANALYSIS RESULTS:"
        assert applied is not None
        assert applied.match_score == 45.0
        
        # When applied is found, note uses "APPLIED POSITION:" and only adds
        # "OTHER TOP MATCHES:" when related roles were also scored.
        # When applied is NOT found, note uses "TOP ANALYSIS RESULTS:"
        # This test verifies the label selection logic
        if applied:
            label = "APPLIED POSITION:"
            other_label = "OTHER TOP MATCHES:" if other_matches else None
        else:
            label = "TOP ANALYSIS RESULTS:"
            other_label = None
        
        assert label == "APPLIED POSITION:"
        assert other_label == "OTHER TOP MATCHES:"
    
    def test_not_qualified_omits_other_top_matches_when_none(self):
        """Single-job not-qualified notes should not show an empty OTHER TOP MATCHES header."""
        applied_match = Mock()
        applied_match.is_applied_job = True
        applied_match.bullhorn_job_id = 35559
        applied_match.job_title = 'Structural Engineer, Sr. (IL)'
        applied_match.match_score = 0.0

        matches = [applied_match]
        other_matches = [m for m in matches if not m.is_applied_job]

        if applied_match and other_matches:
            other_header = "OTHER TOP MATCHES:"
        else:
            other_header = None

        assert other_header is None
        assert other_matches == []

    def test_missing_applied_job_shows_top_analysis(self):
        """Without applied job in results, note falls back to 'TOP ANALYSIS RESULTS'."""
        matches = [
            Mock(is_applied_job=False, bullhorn_job_id=200, match_score=70),
            Mock(is_applied_job=False, bullhorn_job_id=300, match_score=60),
        ]
        
        applied = None
        for match in matches:
            if match.is_applied_job:
                applied = match
        
        if applied:
            label = "APPLIED POSITION:"
        else:
            label = "TOP ANALYSIS RESULTS:"
        
        assert label == "TOP ANALYSIS RESULTS:"


class TestOtherTopMatchesHeader:
    """Regression: empty OTHER TOP MATCHES heading must not appear on single-job notes."""

    def test_single_job_not_qualified_omits_other_top_matches_header(self, app):
        with app.app_context():
            from candidate_vetting_service import CandidateVettingService
            from models import CandidateVettingLog, CandidateJobMatch
            from app import db
            from datetime import datetime

            vetting_log = CandidateVettingLog(
                bullhorn_candidate_id=46734001,
                candidate_name='William Sander Regression',
                status='completed',
                is_qualified=False,
                highest_match_score=0.0,
                note_created=False,
                analyzed_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
            db.session.add(vetting_log)
            db.session.commit()

            match = CandidateJobMatch(
                vetting_log_id=vetting_log.id,
                bullhorn_job_id=35559,
                job_title='Structural Engineer, Sr. (IL)',
                match_score=0.0,
                is_qualified=False,
                is_applied_job=True,
                match_summary='CS background; role needs structural engineering.',
                skills_match='N/A — insufficient overlap',
                experience_match='N/A — insufficient overlap',
                gaps_identified='Degree mismatch | No structural design experience',
            )
            db.session.add(match)
            db.session.commit()

            service = CandidateVettingService()
            mock_bullhorn = MagicMock()
            mock_bullhorn.get_candidate_notes.return_value = []
            mock_bullhorn.create_candidate_note.return_value = 9001001

            with patch.object(service, '_get_bullhorn_service', return_value=mock_bullhorn):
                assert service.create_candidate_note(vetting_log) is True

            mock_bullhorn.create_candidate_note.assert_called_once()
            note_text = mock_bullhorn.create_candidate_note.call_args[0][1]
            assert 'APPLIED POSITION:' in note_text
            assert 'OTHER TOP MATCHES:' not in note_text

            CandidateJobMatch.query.filter_by(vetting_log_id=vetting_log.id).delete()
            CandidateVettingLog.query.filter_by(bullhorn_candidate_id=46734001).delete()
            db.session.commit()

    def test_multi_job_not_qualified_keeps_other_top_matches_header(self, app):
        with app.app_context():
            from candidate_vetting_service import CandidateVettingService
            from models import CandidateVettingLog, CandidateJobMatch
            from app import db
            from datetime import datetime

            vetting_log = CandidateVettingLog(
                bullhorn_candidate_id=46734002,
                candidate_name='Multi Job Regression',
                status='completed',
                is_qualified=False,
                highest_match_score=60.0,
                note_created=False,
                analyzed_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
            db.session.add(vetting_log)
            db.session.commit()

            db.session.add(CandidateJobMatch(
                vetting_log_id=vetting_log.id,
                bullhorn_job_id=35559,
                job_title='Structural Engineer, Sr. (IL)',
                match_score=0.0,
                is_qualified=False,
                is_applied_job=True,
                match_summary='Wrong discipline.',
                skills_match='N/A',
                experience_match='N/A',
                gaps_identified='Degree mismatch',
            ))
            db.session.add(CandidateJobMatch(
                vetting_log_id=vetting_log.id,
                bullhorn_job_id=35578,
                job_title='AI Engineer',
                match_score=60.0,
                is_qualified=False,
                is_applied_job=False,
                match_summary='Partial AI overlap.',
                skills_match='Python',
                experience_match='2 years',
                gaps_identified='Missing Docker',
            ))
            db.session.commit()

            service = CandidateVettingService()
            mock_bullhorn = MagicMock()
            mock_bullhorn.get_candidate_notes.return_value = []
            mock_bullhorn.create_candidate_note.return_value = 9001002

            with patch.object(service, '_get_bullhorn_service', return_value=mock_bullhorn):
                assert service.create_candidate_note(vetting_log) is True

            note_text = mock_bullhorn.create_candidate_note.call_args[0][1]
            assert 'APPLIED POSITION:' in note_text
            assert 'OTHER TOP MATCHES:' in note_text
            assert '35578' in note_text

            CandidateJobMatch.query.filter_by(vetting_log_id=vetting_log.id).delete()
            CandidateVettingLog.query.filter_by(bullhorn_candidate_id=46734002).delete()
            db.session.commit()


class TestRelatedMatchNoteBrevity:
    """Regression: OTHER TOP MATCHES must not mid-sentence truncate (4673413)."""

    def test_complete_brief_clause_prefers_first_pipe_gap(self):
        from screening.note_builder import NoteBuilderMixin

        text = (
            "No evidence of Electrical Engineering degree; holds Mechanical instead. "
            "Electrical systems design experience required 10+ years; candidate has 3+. "
            "No mention of PE licensure or power systems coursework on the resume at all."
        )
        # Long single clause without | — first sentence should win, not a mid-cut …
        out = NoteBuilderMixin._complete_brief_clause(text, max_len=120)
        assert out.endswith('.')
        assert '…' not in out
        assert 'No evidence of Electrical Engineering degree' in out
        assert 'No mention' not in out  # later sentence discarded, not mid-cut

    def test_complete_brief_clause_uses_pipe_bullets(self):
        from screening.note_builder import NoteBuilderMixin

        text = (
            "Revit required (4+ years); no evidence of Revit found in resume. | "
            "Industrial building mechanical/HVAC design and U.S. Mechanical Code/IMC "
            "required; resume shows industrial systems in India, but not US facilities work."
        )
        out = NoteBuilderMixin._complete_brief_clause(text, max_len=180)
        assert out == "Revit required (4+ years); no evidence of Revit found in resume."
        assert '…' not in out

    def test_complete_brief_clause_returns_empty_when_unfittable(self):
        from screening.note_builder import NoteBuilderMixin

        # One giant clause, no sentence break, over max — prefer empty over mid-cut
        text = "x" * 250
        assert NoteBuilderMixin._complete_brief_clause(text, max_len=180) == ''

    def test_select_other_matches_omits_trailing_clear_rejects(self):
        from screening.note_builder import NoteBuilderMixin

        matches = [
            Mock(bullhorn_job_id=35036, match_score=20.0),
            Mock(bullhorn_job_id=34829, match_score=20.0),
            Mock(bullhorn_job_id=35323, match_score=5.0),
            Mock(bullhorn_job_id=34829, match_score=4.0),
            Mock(bullhorn_job_id=34829, match_score=0.0),
        ]
        selected = NoteBuilderMixin._select_other_matches_for_note(matches)
        assert len(selected) == 2
        assert all(not brief for _, brief in selected)
        assert {m.bullhorn_job_id for m, _ in selected} == {35036, 34829}

    def test_select_other_matches_keeps_trailing_near_miss_brief(self):
        from screening.note_builder import NoteBuilderMixin

        matches = [
            Mock(bullhorn_job_id=1, match_score=75.0),
            Mock(bullhorn_job_id=2, match_score=72.0),
            Mock(bullhorn_job_id=3, match_score=65.0),
            Mock(bullhorn_job_id=4, match_score=10.0),
        ]
        selected = NoteBuilderMixin._select_other_matches_for_note(matches)
        assert [(m.bullhorn_job_id, brief) for m, brief in selected] == [
            (1, False),
            (2, False),
            (3, True),
        ]

    def test_saitharun_style_note_omits_truncated_trailing_gaps(self, app):
        """End-to-end: clear-reject trailing related roles must not appear with … cuts."""
        with app.app_context():
            from candidate_vetting_service import CandidateVettingService
            from models import CandidateVettingLog, CandidateJobMatch
            from app import db
            from datetime import datetime

            vetting_log = CandidateVettingLog(
                bullhorn_candidate_id=4673413,
                candidate_name='Saitharun Madipadaga Regression',
                status='completed',
                is_qualified=False,
                highest_match_score=20.0,
                note_created=False,
                analyzed_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
            db.session.add(vetting_log)
            db.session.commit()

            long_gap = (
                "No evidence of Electrical Engineering degree; holds Mechanical Engineering "
                "degree instead. Electrical systems design experience required 10+ years; "
                "candidate has 3+ years mechanical design experience only. No mention of PE "
                "licensure or power distribution coursework anywhere on the resume."
            )
            rows = [
                (35507, 'Facilities Mechanical Engineer', 0.0, True, 'Applied gaps'),
                (35036, 'Civil/Structural Engineer, Senior', 20.0, False, 'CSA experience required'),
                (34829, 'HVAC Designer, Senior (Remote)', 20.0, False, 'Revit missing'),
                (35323, 'Electrical Engineer, Sr. (IN)', 5.0, False, long_gap),
                (34828, 'HVAC Designer Dup Low', 4.0, False, long_gap),
                (34827, 'HVAC Designer Dup Zero', 0.0, False, long_gap),
            ]
            for jid, title, score, applied, gaps in rows:
                db.session.add(CandidateJobMatch(
                    vetting_log_id=vetting_log.id,
                    bullhorn_job_id=jid,
                    job_title=title,
                    match_score=score,
                    technical_score=40.0 if jid in (35507, 35036) else score,
                    is_qualified=False,
                    is_applied_job=applied,
                    match_summary='Wrong discipline fit.',
                    skills_match='N/A',
                    experience_match='N/A',
                    gaps_identified=(
                        gaps + ' | Location mismatch: candidate in Erie, PA'
                        if jid in (35507, 35036) else gaps
                    ),
                ))
            db.session.commit()

            service = CandidateVettingService()
            mock_bullhorn = MagicMock()
            mock_bullhorn.get_candidate_notes.return_value = []
            mock_bullhorn.create_candidate_note.return_value = 9003413

            with patch.object(service, '_get_bullhorn_service', return_value=mock_bullhorn):
                assert service.create_candidate_note(vetting_log) is True

            note_text = mock_bullhorn.create_candidate_note.call_args[0][1]
            assert 'OTHER TOP MATCHES:' in note_text
            assert '35036' in note_text
            assert '34829' in note_text
            # Trailing clear rejects omitted — no mid-sentence ellipsis artifacts
            assert '35323' not in note_text
            assert '34828' not in note_text
            assert '34827' not in note_text
            assert '…' not in note_text
            assert 'No mention…' not in note_text
            assert 'but not…' not in note_text

            CandidateJobMatch.query.filter_by(vetting_log_id=vetting_log.id).delete()
            CandidateVettingLog.query.filter_by(bullhorn_candidate_id=4673413).delete()
            db.session.commit()

    def test_brief_block_never_appends_ellipsis(self):
        from screening.note_builder import NoteBuilderMixin

        match = Mock(
            bullhorn_job_id=99,
            job_title='Near Miss Role',
            match_score=65.0,
            technical_score=65.0,
            gaps_identified=(
                "Missing Kubernetes production experience. | Also missing Terraform "
                "and deep AWS networking beyond a single classroom lab project that "
                "does not demonstrate production ownership of VPC design."
            ),
            match_summary='Partial platform overlap.',
            skills_match='Python',
            prestige_boost_applied=False,
            prestige_employer=None,
        )
        lines = NoteBuilderMixin()._format_match_note_block(
            match, {}, show_gaps=True, brief=True
        )
        joined = '\n'.join(lines)
        assert '…' not in joined
        assert 'Gaps: Missing Kubernetes production experience.' in joined
        assert 'Summary:' not in joined
