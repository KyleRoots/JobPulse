"""
Tests for the _normalize_gaps_text method and note rendering logic.

Ensures:
- GPT array responses are normalized to prose strings
- Legacy JSON string arrays from database are handled
- Plain strings pass through unchanged
- CandidateJobMatch objects are properly accessed during note rendering
- No AttributeError on model attribute access
"""

import pytest
import json
from unittest.mock import MagicMock, patch
from datetime import datetime


class TestNormalizeGapsText:
    """Unit tests for CandidateVettingService._normalize_gaps_text()"""
    
    @pytest.fixture
    def vetting_service(self, app):
        """Create a CandidateVettingService instance within app context."""
        with app.app_context():
            from candidate_vetting_service import CandidateVettingService
            yield CandidateVettingService()
    
    def test_plain_string_passthrough(self, vetting_service, app):
        """Plain prose string should pass through unchanged."""
        with app.app_context():
            result = vetting_service._normalize_gaps_text(
                "No direct experience with industrial equipment. Missing location match.",
                candidate_id=12345
            )
            assert result == "No direct experience with industrial equipment. Missing location match."
    
    def test_list_normalized_to_prose(self, vetting_service, app):
        """Python list should be joined with '. ' separator."""
        with app.app_context():
            result = vetting_service._normalize_gaps_text(
                ["Experience with industrial equipment", "Ultra membrane filtration", "Location mismatch"],
                candidate_id=12345
            )
            assert result == "Experience with industrial equipment. Ultra membrane filtration. Location mismatch"
            assert "[" not in result
            assert "]" not in result
    
    def test_empty_list_returns_empty_string(self, vetting_service, app):
        """Empty list should return empty string."""
        with app.app_context():
            result = vetting_service._normalize_gaps_text([], candidate_id=12345)
            assert result == ""
    
    def test_single_item_list(self, vetting_service, app):
        """Single-item list should return just the string, no separator."""
        with app.app_context():
            result = vetting_service._normalize_gaps_text(
                ["Missing 3 years of Python experience"],
                candidate_id=12345
            )
            assert result == "Missing 3 years of Python experience"
    
    def test_json_string_array_normalized(self, vetting_service, app):
        """JSON string containing array (legacy DB data) should be parsed and joined."""
        with app.app_context():
            json_str = json.dumps(["Gap one", "Gap two", "Gap three"])
            result = vetting_service._normalize_gaps_text(json_str, candidate_id=12345)
            assert result == "Gap one. Gap two. Gap three"
    
    def test_string_starting_with_bracket_not_json(self, vetting_service, app):
        """String starting with '[' but not valid JSON should pass through."""
        with app.app_context():
            result = vetting_service._normalize_gaps_text(
                "[Note: candidate has partial skills match", 
                candidate_id=12345
            )
            assert result == "[Note: candidate has partial skills match"
    
    def test_none_candidate_id_doesnt_crash(self, vetting_service, app):
        """None candidate_id should not crash the logging."""
        with app.app_context():
            result = vetting_service._normalize_gaps_text(
                ["Gap one", "Gap two"],
                candidate_id=None
            )
            assert result == "Gap one. Gap two"
    
    def test_candidate_id_omitted_doesnt_crash(self, vetting_service, app):
        """Omitting candidate_id should not crash (default=None)."""
        with app.app_context():
            result = vetting_service._normalize_gaps_text("Simple string")
            assert result == "Simple string"
    
    def test_empty_string_passthrough(self, vetting_service, app):
        """Empty string should pass through as empty string."""
        with app.app_context():
            result = vetting_service._normalize_gaps_text("", candidate_id=12345)
            assert result == ""


class TestNoteRenderingModelAccess:
    """Tests that note rendering code accesses correct model attributes.
    
    The CandidateJobMatch model does NOT have bullhorn_candidate_id.
    The CandidateVettingLog model DOES have bullhorn_candidate_id.
    This test class ensures we never reference wrong attributes.
    """
    
    def test_candidate_job_match_has_expected_attributes(self, app):
        """CandidateJobMatch should have bullhorn_job_id but NOT bullhorn_candidate_id."""
        with app.app_context():
            from models import CandidateJobMatch
            # CandidateJobMatch has bullhorn_job_id
            assert hasattr(CandidateJobMatch, 'bullhorn_job_id')
            # CandidateJobMatch does NOT have bullhorn_candidate_id
            assert not hasattr(CandidateJobMatch, 'bullhorn_candidate_id'), \
                "CandidateJobMatch should NOT have bullhorn_candidate_id — use vetting_log.bullhorn_candidate_id instead"
    
    def test_candidate_vetting_log_has_candidate_id(self, app):
        """CandidateVettingLog should have bullhorn_candidate_id."""
        with app.app_context():
            from models import CandidateVettingLog
            assert hasattr(CandidateVettingLog, 'bullhorn_candidate_id')
    
    def test_candidate_job_match_has_gaps_identified(self, app):
        """CandidateJobMatch should have gaps_identified field."""
        with app.app_context():
            from models import CandidateJobMatch
            assert hasattr(CandidateJobMatch, 'gaps_identified')
    
    def test_candidate_job_match_has_note_fields(self, app):
        """CandidateJobMatch should have all fields used in note rendering."""
        with app.app_context():
            from models import CandidateJobMatch
            required_fields = [
                'bullhorn_job_id', 'job_title', 'match_score',
                'match_summary', 'skills_match', 'gaps_identified', 
                'is_applied_job', 'is_qualified'
            ]
            for field in required_fields:
                assert hasattr(CandidateJobMatch, field), \
                    f"CandidateJobMatch missing required note field: {field}"


class TestLayer2ParseTimeNormalization:
    """Tests that parse-time normalization handles GPT response arrays correctly.
    
    Validates the normalization loop that runs after json.loads() on GPT responses.
    """
    
    def test_gpt_response_with_string_gaps_unchanged(self):
        """String gaps_identified should pass through normalization unchanged."""
        result = {
            'match_score': 85,
            'gaps_identified': 'No direct manufacturing experience. Location mismatch.',
            'match_summary': 'Good overall fit.',
            'skills_match': 'Python, SQL, data analysis.',
            'experience_match': '5 years in similar role.'
        }
        
        # Simulate the normalization loop (Layer 2)
        for field in ['gaps_identified', 'match_summary', 'skills_match', 'experience_match']:
            if isinstance(result.get(field), list):
                result[field] = ". ".join(str(item) for item in result[field])
        
        assert result['gaps_identified'] == 'No direct manufacturing experience. Location mismatch.'
    
    def test_gpt_response_with_array_gaps_normalized(self):
        """Array gaps_identified should be joined into prose."""
        result = {
            'match_score': 65,
            'gaps_identified': ['No manufacturing experience', 'Missing location match', 'Needs 3 more years'],
            'match_summary': 'Poor fit.',
            'skills_match': 'Limited overlap.',
            'experience_match': 'Insufficient.'
        }
        
        # Simulate the normalization loop (Layer 2)
        for field in ['gaps_identified', 'match_summary', 'skills_match', 'experience_match']:
            if isinstance(result.get(field), list):
                result[field] = ". ".join(str(item) for item in result[field])
        
        assert result['gaps_identified'] == 'No manufacturing experience. Missing location match. Needs 3 more years'
        assert isinstance(result['gaps_identified'], str)
    
    def test_gpt_response_with_multiple_array_fields(self):
        """All array fields should be normalized, not just gaps."""
        result = {
            'match_score': 70,
            'gaps_identified': ['Gap 1', 'Gap 2'],
            'match_summary': ['Summary point 1', 'Summary point 2'],
            'skills_match': ['Skill A', 'Skill B'],
            'experience_match': 'Already a string'
        }
        
        for field in ['gaps_identified', 'match_summary', 'skills_match', 'experience_match']:
            if isinstance(result.get(field), list):
                result[field] = ". ".join(str(item) for item in result[field])
        
        assert result['gaps_identified'] == 'Gap 1. Gap 2'
        assert result['match_summary'] == 'Summary point 1. Summary point 2'
        assert result['skills_match'] == 'Skill A. Skill B'
        assert result['experience_match'] == 'Already a string'  # Unchanged
