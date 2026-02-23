"""
Tests for the experience floor gate (fresh-grad / intern-only false positive prevention).

Regression tests for the 4589857 false positive: a fresh grad with academic
AI projects was scored 85% on a Data Scientist role requiring 5 years of
experience.  The experience floor gate prevents this by:
  1. Capping score at 55 when FRESH_GRAD/ENTRY classification meets a 3+yr
     requirement.
  2. Cross-checking and overriding false `meets_requirement` flags in
     years_analysis when the candidate is intern-only.
"""
import json
import pytest
from unittest.mock import MagicMock, patch


class TestExperienceFloorGate:
    """Post-processing experience floor gate that caps scores for
    fresh-grad / intern-only candidates matched against senior roles."""

    @pytest.fixture
    def service(self):
        """Create a CandidateVettingService with mocked dependencies."""
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

    # ── Helpers ──────────────────────────────────────────────────────

    def _make_ai_response(self, match_score, years_analysis=None,
                          experience_level=None, gaps=""):
        """Build a mock AI JSON response including experience_level_classification."""
        resp = {
            "match_score": match_score,
            "match_summary": "Test summary",
            "skills_match": "Python, TensorFlow, scikit-learn",
            "experience_match": "3 months AI internship, academic projects",
            "gaps_identified": gaps,
            "key_requirements": (
                "• Minimum 5 years of experience within Data Science\n"
                "• Must have a Bachelor's or Master's Degree\n"
                "• Experience with Python\n"
                "• Experience with AI/ML model deployment workflows\n"
                "• Must be located within Egypt or Spain"
            ),
            "recency_analysis": {
                "most_recent_role": "AI Intern at TachyHealth (09/2023 – 12/2023)",
                "most_recent_role_relevant": True,
                "second_recent_role": "IT Support Intern at University (06/2023 – 08/2023)",
                "second_recent_role_relevant": False,
                "last_relevant_role_ended": "12/2023",
                "months_since_relevant_work": 26,
                "penalty_applied": 0,
                "reasoning": "Most recent role is loosely AI-related"
            },
        }
        if years_analysis is not None:
            resp["years_analysis"] = years_analysis
        if experience_level is not None:
            resp["experience_level_classification"] = experience_level
        return resp

    def _run_gate(self, service, ai_response, job_id=34638,
                  custom_requirements=None):
        """Run analyze_candidate_job_match with a mocked AI response."""
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = json.dumps(ai_response)
        service.openai_client.chat.completions.create.return_value = mock_completion

        job = {
            'id': job_id,
            'title': 'Data Scientist - AI, Analytics & Machine Learning',
            'description': (
                'We are seeking a Data Scientist with minimum 5 years '
                'of experience in data science. Strong Python skills and '
                'experience with AI/ML model deployment workflows required.'
            ),
            'address': {
                'city': '', 'state': None, 'countryCode': 'EG',
                'countryName': 'Egypt',
            },
            'onSite': 'Remote',
        }

        reqs = custom_requirements or (
            "• Minimum 5 years of experience within Data Science\n"
            "• Must have a Bachelor's or Master's Degree\n"
            "• Experience with Python\n"
            "• Experience with AI/ML model deployment workflows\n"
            "• Must be located within Egypt or Spain"
        )

        with patch.object(service, '_get_job_custom_requirements',
                          return_value=None):
            result = service.analyze_candidate_job_match(
                resume_text=(
                    "Jane Doe, Cairo Egypt. BSc Computer Science 2025. "
                    "Intern at TachyHealth 09/2023-12/2023 (AI analytics). "
                    "IT Support Intern 06/2023-08/2023. Projects: Kafka+PySpark "
                    "pipeline, RAG chatbot, CNN image classifier. "
                    "Skills: Python, TensorFlow, scikit-learn, Docker."
                ),
                job=job,
                prefetched_requirements=reqs,
            )
        return result

    # ── Tests ────────────────────────────────────────────────────────

    def test_fresh_grad_ai_overscore_capped_by_floor(self, service):
        """AI over-scores a fresh grad at 85% — experience floor caps at 55.

        Reproduces the 4589857 false positive: AI returns meets_requirement
        True (incorrect) and an inflated score.  The experience floor gate
        detects FRESH_GRAD + 5yr requirement and hard-caps the score.
        """
        ai_response = self._make_ai_response(
            match_score=85,
            years_analysis={
                "Data Science": {
                    "required_years": 5,
                    "estimated_years": 3.5,   # AI inflated (counted projects)
                    "meets_requirement": True,  # AI incorrectly claims met
                    "calculation": "Intern 4mo + projects 2yr = ~3.5yr"
                }
            },
            experience_level={
                "classification": "FRESH_GRAD",
                "total_professional_years": 0.25,
                "highest_role_type": "INTERNSHIP_ONLY"
            },
        )

        result = self._run_gate(service, ai_response)

        # Gate 1: FRESH_GRAD + 5yr requirement → cap at 55
        assert result['match_score'] <= 55, (
            f"Fresh grad should be capped at ≤55, got {result['match_score']}"
        )

        # Gate 2 cross-check: meets_requirement should be overridden to False
        ds = result['years_analysis']['Data Science']
        assert ds['meets_requirement'] is False, (
            "meets_requirement should be overridden to False for intern-only"
        )

        # Gaps should mention the experience floor
        gaps = result.get('gaps_identified', '')
        assert 'experience floor' in gaps.lower() or 'CRITICAL' in gaps, (
            f"Expected experience floor note in gaps, got: {gaps}"
        )

    def test_fresh_grad_correct_classification_still_caps(self, service):
        """AI correctly reports years shortfall — floor provides defense-in-depth.

        Even when the AI honestly flags the shortfall, the experience floor
        gate provides additional protection by capping at 55.
        """
        ai_response = self._make_ai_response(
            match_score=65,
            years_analysis={
                "Data Science": {
                    "required_years": 5,
                    "estimated_years": 0.25,
                    "meets_requirement": False,
                    "calculation": "TachyHealth intern 3mo = 0.25yr"
                }
            },
            experience_level={
                "classification": "FRESH_GRAD",
                "total_professional_years": 0.25,
                "highest_role_type": "INTERNSHIP_ONLY"
            },
            gaps="Insufficient experience: 0.25yr vs 5yr required",
        )

        result = self._run_gate(service, ai_response)

        # Both years gate (≥2yr shortfall → cap 60) AND floor gate (55) apply
        assert result['match_score'] <= 55, (
            f"Fresh grad + 4.75yr shortfall should be ≤55, "
            f"got {result['match_score']}"
        )

        # Gaps should reflect the shortfall
        gaps = result.get('gaps_identified', '')
        assert 'Data Science' in gaps or 'experience' in gaps.lower(), (
            f"Expected experience-related gap, got: {gaps}"
        )
