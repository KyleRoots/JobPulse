"""
Regression tests for Rule 14 — Recency-of-Experience Hard Gate.

Tests the post-processing penalty that fires AFTER the AI returns a score and
recency_analysis. The hard gate enforces:
  - Both recent roles unrelated → 20pt penalty (15-25 range midpoint)
  - Most recent unrelated + 12mo+ gap → 12pt penalty (10-15 range midpoint)
  - Both recent roles relevant → no penalty
"""
import json
import pytest
from unittest.mock import MagicMock, patch


class TestRecencyHardGate:
    """Post-processing recency-of-experience hard gate."""

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

    def _make_ai_response(self, match_score, recency_analysis=None, gaps=""):
        """Build a mock AI JSON response with recency_analysis."""
        resp = {
            "match_score": match_score,
            "match_summary": "Test summary",
            "skills_match": "Python, React",
            "experience_match": "5 years at Acme Corp",
            "gaps_identified": gaps,
            "key_requirements": "• Python 3+ years\n• React 2+ years",
        }
        if recency_analysis is not None:
            resp["recency_analysis"] = recency_analysis
        return resp

    def _run_gate(self, service, ai_response, job_id=123):
        """Run analyze_candidate_job_match with a controlled AI response."""
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = json.dumps(ai_response)
        service.openai_client.chat.completions.create.return_value = mock_completion

        job = {
            'id': job_id,
            'title': 'Data Engineer',
            'description': 'Need 3+ years Python, data pipeline experience',
            'address': {'city': 'Dallas', 'state': 'TX', 'countryName': 'United States'},
            'onSite': 1
        }

        with patch.object(service, '_get_job_custom_requirements', return_value=None):
            result = service.analyze_candidate_job_match(
                resume_text="Sample resume text",
                job=job,
                prefetched_requirements="• Python 3+ years\n• Data pipelines"
            )
        return result

    # ── Test 1: Both recent roles unrelated → 20pt penalty ──

    def test_both_recent_roles_unrelated_applies_20pt_penalty(self, service):
        """Candidate's last two roles are in unrelated fields → 20pt penalty."""
        ai_response = self._make_ai_response(
            match_score=78,
            recency_analysis={
                "most_recent_role_relevant": False,
                "second_recent_role_relevant": False,
                "months_since_relevant_work": 24,
                "penalty_applied": 10  # AI applied 10, hard gate should enforce 20
            }
        )

        result = self._run_gate(service, ai_response)

        # Hard gate enforces max(20, 10) = 20pt penalty → 78 - 20 = 58
        assert result['match_score'] <= 58, \
            f"Expected score ≤58 (78 - 20pt penalty), got {result['match_score']}"
        assert "career trajectory has shifted" in result.get('gaps_identified', '').lower(), \
            "Expected career trajectory gap note"

    # ── Test 2: Most recent unrelated + 12mo gap → 12pt penalty ──

    def test_most_recent_unrelated_12mo_gap_applies_penalty(self, service):
        """Most recent role unrelated, relevant work ended 18 months ago → 12pt penalty."""
        ai_response = self._make_ai_response(
            match_score=75,
            recency_analysis={
                "most_recent_role_relevant": False,
                "second_recent_role_relevant": True,
                "months_since_relevant_work": 18,
                "penalty_applied": 5  # AI applied 5, hard gate should enforce 12
            }
        )

        result = self._run_gate(service, ai_response)

        # Hard gate enforces max(12, 5) = 12pt penalty → 75 - 12 = 63
        assert result['match_score'] <= 63, \
            f"Expected score ≤63 (75 - 12pt penalty), got {result['match_score']}"
        assert "not current" in result.get('gaps_identified', '').lower(), \
            "Expected recency gap note about experience not being current"

    # ── Test 3: Both recent roles relevant → no penalty ──

    def test_both_recent_roles_relevant_no_penalty(self, service):
        """Candidate's recent roles are relevant → score unchanged."""
        ai_response = self._make_ai_response(
            match_score=82,
            recency_analysis={
                "most_recent_role_relevant": True,
                "second_recent_role_relevant": True,
                "months_since_relevant_work": 0,
                "penalty_applied": 0
            }
        )

        result = self._run_gate(service, ai_response)

        assert result['match_score'] == 82, \
            f"Expected score 82 (no penalty), got {result['match_score']}"
        # No recency gap note should be appended
        gaps = result.get('gaps_identified', '')
        assert "career trajectory" not in gaps.lower()
        assert "not current" not in gaps.lower()
