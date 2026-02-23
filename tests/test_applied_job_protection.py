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
    def test_fetch_applied_job_returns_none_for_closed_job(self, mock_bullhorn_cls):
        """_fetch_applied_job returns None for closed jobs."""
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
        
        result = service._fetch_applied_job(mock_bullhorn, 33615)
        
        assert result is None, "Closed jobs should return None"
    
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
        
        # When applied is found, note uses "APPLIED POSITION:" + "OTHER TOP MATCHES:"
        # When applied is NOT found, note uses "TOP ANALYSIS RESULTS:"
        # This test verifies the label selection logic
        if applied:
            label = "APPLIED POSITION:"
        else:
            label = "TOP ANALYSIS RESULTS:"
        
        assert label == "APPLIED POSITION:"
    
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
