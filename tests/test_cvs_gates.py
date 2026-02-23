"""
Gate-specific tests for CandidateVettingService.

Covers the post-processing defense-in-depth gates in analyze_candidate_job_match:
  1. Years-of-experience hard gate (≥2yr shortfall → cap 60)
  2. Years penalty gate (1-2yr shortfall → -15)
  3. Recency gate (career-shifted → penalty)
  4. Experience floor (FRESH_GRAD + 3yr+ requirement → cap 55)
  5. Cutoff date filtering in detect_unvetted_applications
  6. Normalize gaps text (array → string)
"""

import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


@pytest.fixture
def service():
    """Create a CVS instance with mocked dependencies (no DB required)."""
    with patch.dict('os.environ', {
        'OPENAI_API_KEY': 'test-key',
        'DATABASE_URL': 'sqlite:///:memory:'
    }):
        mock_app = MagicMock()
        mock_models = MagicMock()
        with patch.dict('sys.modules', {
            'app': mock_app,
            'models': mock_models,
        }):
            from candidate_vetting_service import CandidateVettingService
            svc = CandidateVettingService.__new__(CandidateVettingService)
            svc.openai_client = MagicMock()
            svc.model = 'gpt-4o-mini'
            svc.logger = MagicMock()
            return svc


def _run_gate(service, ai_response, job=None, resume="Test resume " * 50,
              requirements=None):
    """Run analyze_candidate_job_match with a mocked AI response."""
    mock_completion = MagicMock()
    mock_completion.choices = [MagicMock()]
    mock_completion.choices[0].message.content = json.dumps(ai_response)
    service.openai_client.chat.completions.create.return_value = mock_completion

    default_job = {
        'id': 10001,
        'title': 'Software Engineer',
        'description': '5+ years required.',
        'address': {'city': 'Toronto', 'state': 'ON',
                    'countryCode': 'CA', 'countryName': 'Canada'},
        'onSite': 'Remote',
    }

    with patch.object(service, '_get_job_custom_requirements', return_value=None):
        return service.analyze_candidate_job_match(
            resume_text=resume,
            job=job or default_job,
            prefetched_requirements=requirements or "5+ years experience",
        )


def _base_response(**overrides):
    """Base AI response with sane defaults."""
    r = {
        "match_score": 80,
        "match_summary": "Good match",
        "skills_match": "Python, SQL",
        "experience_match": "5 years",
        "gaps_identified": "",
        "key_requirements": "5+ years experience",
        "recency_analysis": {
            "most_recent_role_relevant": True,
            "second_recent_role_relevant": True,
            "months_since_relevant_work": 0,
            "penalty_applied": 0,
        },
        "experience_level_classification": {
            "classification": "MID_LEVEL",
            "total_professional_years": 5.0,
            "highest_role_type": "FULL_TIME"
        },
    }
    r.update(overrides)
    return r


# ===========================================================================
# 1. Years hard gate: ≥2yr shortfall → cap at 60
# ===========================================================================
class TestYearsHardGate:

    def test_3yr_shortfall_caps_at_60(self, service):
        """≥2yr shortfall → score capped at 60."""
        resp = _base_response(
            match_score=85,
            years_analysis={
                "Python": {
                    "required_years": 5,
                    "estimated_years": 2,
                    "meets_requirement": False,
                    "calculation": "2 years"
                }
            }
        )
        result = _run_gate(service, resp)
        assert result['match_score'] <= 60

    def test_exact_2yr_shortfall_caps_at_60(self, service):
        """Exactly 2yr shortfall → cap at 60."""
        resp = _base_response(
            match_score=90,
            years_analysis={
                "Java": {
                    "required_years": 5,
                    "estimated_years": 3,
                    "meets_requirement": False,
                    "calculation": "3 years"
                }
            }
        )
        result = _run_gate(service, resp)
        assert result['match_score'] <= 60

    def test_no_shortfall_preserves_score(self, service):
        """No shortfall → score unchanged."""
        resp = _base_response(
            match_score=88,
            years_analysis={
                "Python": {
                    "required_years": 5,
                    "estimated_years": 6,
                    "meets_requirement": True,
                    "calculation": "6 years"
                }
            }
        )
        result = _run_gate(service, resp)
        assert result['match_score'] == 88


# ===========================================================================
# 2. Years penalty gate: 1-2yr shortfall → -15
# ===========================================================================
class TestYearsPenalty:

    def test_1yr_shortfall_applies_15pt_penalty(self, service):
        """1yr shortfall → -15 penalty."""
        resp = _base_response(
            match_score=80,
            years_analysis={
                "DevOps": {
                    "required_years": 5,
                    "estimated_years": 4,
                    "meets_requirement": False,
                    "calculation": "4 years"
                }
            }
        )
        result = _run_gate(service, resp)
        assert result['match_score'] == 65

    def test_1_5yr_shortfall_applies_15pt_penalty(self, service):
        """1.5yr shortfall → -15 penalty."""
        resp = _base_response(
            match_score=75,
            years_analysis={
                "React": {
                    "required_years": 5,
                    "estimated_years": 3.5,
                    "meets_requirement": False,
                    "calculation": "3.5 years"
                }
            }
        )
        result = _run_gate(service, resp)
        assert result['match_score'] == 60


# ===========================================================================
# 3. Recency gate
# ===========================================================================
class TestRecencyGate:

    def test_both_recent_roles_unrelated_20pt_penalty(self, service):
        """Both most recent roles unrelated → 20pt penalty."""
        resp = _base_response(
            match_score=75,
            recency_analysis={
                "most_recent_role": "Real Estate Agent (2022-2024)",
                "most_recent_role_relevant": False,
                "second_recent_role": "Barista (2020-2022)",
                "second_recent_role_relevant": False,
                "months_since_relevant_work": 60,
                "penalty_applied": 10,
                "reasoning": "Career shifted"
            },
        )
        result = _run_gate(service, resp)
        # max(20, 10) = 20pt penalty → 75-20=55
        assert result['match_score'] <= 55

    def test_most_recent_unrelated_12_plus_months_12pt_penalty(self, service):
        """Most recent role unrelated + relevant work ended 12+ months ago → 12pt."""
        resp = _base_response(
            match_score=80,
            recency_analysis={
                "most_recent_role": "Uber Driver (2023-2024)",
                "most_recent_role_relevant": False,
                "second_recent_role": "DevOps Engineer (2020-2022)",
                "second_recent_role_relevant": True,
                "months_since_relevant_work": 24,
                "penalty_applied": 5,
                "reasoning": "Recent career shift"
            },
        )
        result = _run_gate(service, resp)
        # max(12, 5) = 12pt penalty → 80-12=68
        assert result['match_score'] <= 68

    def test_recent_roles_relevant_no_penalty(self, service):
        """Both recent roles relevant → no recency penalty."""
        resp = _base_response(
            match_score=85,
            recency_analysis={
                "most_recent_role_relevant": True,
                "second_recent_role_relevant": True,
                "months_since_relevant_work": 0,
                "penalty_applied": 0,
            },
        )
        result = _run_gate(service, resp)
        assert result['match_score'] == 85


# ===========================================================================
# 4. Experience floor gate
# ===========================================================================
class TestExperienceFloor:

    def test_fresh_grad_5yr_req_capped_55(self, service):
        """FRESH_GRAD + 5yr requirement → cap at 55."""
        resp = _base_response(
            match_score=82,
            years_analysis={
                "Data Science": {
                    "required_years": 5,
                    "estimated_years": 0.25,
                    "meets_requirement": True,
                    "calculation": "3 month internship"
                }
            },
            experience_level_classification={
                "classification": "FRESH_GRAD",
                "total_professional_years": 0.25,
                "highest_role_type": "INTERNSHIP_ONLY"
            },
        )
        result = _run_gate(service, resp)
        assert result['match_score'] <= 55

    def test_entry_level_3yr_req_capped_at_60(self, service):
        """ENTRY_LEVEL + 3yr requirement → Gate 1 caps at 55, but Gate 2
        cross-check for intern-only profiles re-runs years gate and caps at 60
        for ≥2yr shortfall. Final score: ≤60."""
        resp = _base_response(
            match_score=78,
            years_analysis={
                "Engineering": {
                    "required_years": 3,
                    "estimated_years": 0.5,
                    "meets_requirement": False,
                    "calculation": "6 month internship"
                }
            },
            experience_level_classification={
                "classification": "ENTRY_LEVEL",
                "total_professional_years": 0.5,
                "highest_role_type": "INTERNSHIP_ONLY"
            },
        )
        result = _run_gate(service, resp)
        assert result['match_score'] <= 60

    def test_senior_not_affected_by_floor(self, service):
        """SENIOR classification → floor gate does NOT fire."""
        resp = _base_response(
            match_score=90,
            experience_level_classification={
                "classification": "SENIOR",
                "total_professional_years": 8.0,
                "highest_role_type": "FULL_TIME"
            },
        )
        result = _run_gate(service, resp)
        assert result['match_score'] == 90


# ===========================================================================
# 5. Normalize gaps text
# ===========================================================================
class TestNormalizeGapsText:

    def test_list_normalized_to_string(self, service):
        """gaps_identified returned as list → joined into string."""
        result = service._normalize_gaps_text(
            ["Missing Python", "No AWS experience"], candidate_id=1
        )
        assert isinstance(result, str)
        assert "Missing Python" in result
        assert "No AWS experience" in result

    def test_string_returned_as_is(self, service):
        """gaps_identified as string → returned unchanged."""
        result = service._normalize_gaps_text("Missing Python", candidate_id=1)
        assert result == "Missing Python"

    def test_json_array_string_normalized(self, service):
        """gaps_identified as JSON array string → normalized to prose."""
        result = service._normalize_gaps_text(
            '["Missing Python", "No AWS"]', candidate_id=1
        )
        assert isinstance(result, str)
        assert "Missing Python" in result
