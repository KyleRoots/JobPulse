"""
CandidateVettingService unit tests for JobPulse.

Tests CandidateVettingService methods with mocked Bullhorn, OpenAI, and database dependencies.
These tests exercise the service in isolation without actual API calls.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock


class TestCandidateVettingServiceInitialization:
    """Test CandidateVettingService initialization and configuration."""
    
    @patch('candidate_vetting_service.BullhornService')
    def test_init_with_bullhorn_service(self, mock_bullhorn):
        """Test initialization with provided BullhornService."""
        from candidate_vetting_service import CandidateVettingService
        
        mock_service = Mock()
        vetting_service = CandidateVettingService(bullhorn_service=mock_service)
        
        assert vetting_service is not None
    
    @patch('candidate_vetting_service.BullhornService')
    def test_init_without_bullhorn_service(self, mock_bullhorn):
        """Test initialization without BullhornService (lazy init)."""
        from candidate_vetting_service import CandidateVettingService
        
        vetting_service = CandidateVettingService()
        assert vetting_service is not None


class TestCandidateVettingServiceConfig:
    """Test CandidateVettingService configuration methods."""
    
    @patch('candidate_vetting_service.BullhornService')
    @patch('candidate_vetting_service.VettingConfig')
    def test_get_config_value_default(self, mock_config, mock_bullhorn):
        """Test getting config value with default."""
        from candidate_vetting_service import CandidateVettingService
        
        mock_config.query.filter_by.return_value.first.return_value = None
        
        service = CandidateVettingService()
        result = service.get_config_value('test_key', 'default_value')
        
        assert result == 'default_value'
    
    @patch('candidate_vetting_service.BullhornService')
    @patch('candidate_vetting_service.VettingConfig')
    def test_is_enabled_returns_bool(self, mock_config, mock_bullhorn):
        """Test is_enabled returns boolean."""
        from candidate_vetting_service import CandidateVettingService
        
        mock_config.query.filter_by.return_value.first.return_value = None
        
        service = CandidateVettingService()
        result = service.is_enabled()
        
        assert isinstance(result, bool)
    
    @patch('candidate_vetting_service.BullhornService')
    @patch('candidate_vetting_service.VettingConfig')
    def test_get_threshold_returns_int(self, mock_config, mock_bullhorn):
        """Test get_threshold returns integer."""
        from candidate_vetting_service import CandidateVettingService
        
        mock_config.query.filter_by.return_value.first.return_value = None
        
        service = CandidateVettingService()
        result = service.get_threshold()
        
        assert isinstance(result, int)


class TestCandidateVettingServiceJobThreshold:
    """Test CandidateVettingService job-specific threshold methods."""
    
    @patch('candidate_vetting_service.BullhornService')
    @patch('candidate_vetting_service.JobVettingRequirements')
    @patch('candidate_vetting_service.VettingConfig')
    def test_get_job_threshold_specific(self, mock_config, mock_requirements, mock_bullhorn):
        """Test getting job-specific threshold."""
        from candidate_vetting_service import CandidateVettingService
        
        # Mock job-specific threshold
        mock_job_req = Mock()
        mock_job_req.match_threshold = 75
        mock_requirements.query.filter_by.return_value.first.return_value = mock_job_req
        mock_config.query.filter_by.return_value.first.return_value = None
        
        service = CandidateVettingService()
        result = service.get_job_threshold(12345)
        
        assert result == 75
    
    @patch('candidate_vetting_service.BullhornService')
    @patch('candidate_vetting_service.JobVettingRequirements')
    @patch('candidate_vetting_service.VettingConfig')
    def test_get_job_threshold_fallback_to_global(self, mock_config, mock_requirements, mock_bullhorn):
        """Test falling back to global threshold when job-specific not set."""
        from candidate_vetting_service import CandidateVettingService
        
        # No job-specific threshold
        mock_requirements.query.filter_by.return_value.first.return_value = None
        mock_config.query.filter_by.return_value.first.return_value = None
        
        service = CandidateVettingService()
        result = service.get_job_threshold(12345)
        
        # Should return global default
        assert isinstance(result, int)


class TestCandidateVettingServiceJobRequirements:
    """Test CandidateVettingService job requirements methods."""
    
    @patch('candidate_vetting_service.BullhornService')
    @patch('candidate_vetting_service.JobVettingRequirements')
    def test_get_job_custom_requirements_none(self, mock_requirements, mock_bullhorn):
        """Test getting custom requirements when none set."""
        from candidate_vetting_service import CandidateVettingService
        
        mock_requirements.query.filter_by.return_value.first.return_value = None
        
        service = CandidateVettingService()
        result = service._get_job_custom_requirements(12345)
        
        assert result is None


class TestCandidateVettingServiceActiveJobs:
    """Test CandidateVettingService active job methods."""
    
    @patch('candidate_vetting_service.BullhornService')
    @patch('candidate_vetting_service.BullhornMonitor')
    def test_get_active_job_ids_empty(self, mock_monitor, mock_bullhorn):
        """Test getting active job IDs with no monitors."""
        from candidate_vetting_service import CandidateVettingService
        
        mock_monitor.query.filter_by.return_value.all.return_value = []
        mock_bullhorn_instance = Mock()
        mock_bullhorn_instance.test_connection.return_value = False
        mock_bullhorn.return_value = mock_bullhorn_instance
        
        service = CandidateVettingService()
        result = service.get_active_job_ids()
        
        assert isinstance(result, set)


class TestCandidateVettingServiceSyncMethods:
    """Test CandidateVettingService sync methods."""
    
    @patch('candidate_vetting_service.BullhornService')
    @patch('candidate_vetting_service.BullhornMonitor')
    def test_sync_requirements_with_active_jobs(self, mock_monitor, mock_bullhorn):
        """Test syncing requirements with active jobs."""
        from candidate_vetting_service import CandidateVettingService
        
        mock_monitor.query.filter_by.return_value.all.return_value = []
        mock_bullhorn_instance = Mock()
        mock_bullhorn_instance.test_connection.return_value = False
        mock_bullhorn.return_value = mock_bullhorn_instance
        
        service = CandidateVettingService()
        result = service.sync_requirements_with_active_jobs()
        
        assert isinstance(result, dict)
    
    @patch('candidate_vetting_service.BullhornService')
    @patch('candidate_vetting_service.BullhornMonitor')
    @patch('candidate_vetting_service.CandidateJobMatch')
    def test_sync_job_recruiter_assignments_no_jobs(self, mock_match, mock_monitor, mock_bullhorn):
        """Test syncing recruiter assignments with no jobs."""
        from candidate_vetting_service import CandidateVettingService
        
        mock_monitor.query.filter_by.return_value.all.return_value = []
        mock_bullhorn_instance = Mock()
        mock_bullhorn_instance.test_connection.return_value = False
        mock_bullhorn.return_value = mock_bullhorn_instance
        
        service = CandidateVettingService()
        result = service.sync_job_recruiter_assignments(jobs=[])
        
        assert isinstance(result, dict)


class TestCandidateVettingServiceCleanup:
    """Test CandidateVettingService cleanup methods."""
    
    @patch('candidate_vetting_service.BullhornService')
    def test_cleanup_duplicate_notes_batch_deprecated(self, mock_bullhorn):
        """Test that deprecated cleanup method returns empty result."""
        from candidate_vetting_service import CandidateVettingService
        
        service = CandidateVettingService()
        result = service.cleanup_duplicate_notes_batch(batch_size=10)
        
        # Deprecated method should return summary dict
        assert isinstance(result, dict)


class TestMapWorkType:
    """Test map_work_type helper function."""
    
    def test_map_work_type_numeric(self):
        """Test mapping numeric work type values."""
        from candidate_vetting_service import map_work_type
        
        assert map_work_type(1) in ['Remote', 'On-Site', 'Hybrid', 'Unknown', None, '']
        assert map_work_type(2) in ['Remote', 'On-Site', 'Hybrid', 'Unknown', None, '']
        assert map_work_type(3) in ['Remote', 'On-Site', 'Hybrid', 'Unknown', None, '']
    
    def test_map_work_type_string(self):
        """Test mapping string work type values."""
        from candidate_vetting_service import map_work_type
        
        result = map_work_type('Remote')
        assert result is not None
    
    def test_map_work_type_none(self):
        """Test mapping None work type."""
        from candidate_vetting_service import map_work_type
        
        result = map_work_type(None)
        # Should handle None gracefully
        assert result is None or isinstance(result, str)
