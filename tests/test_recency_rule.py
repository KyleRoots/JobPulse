"""
Tests for Rule 14: Recency of Relevant Experience in candidate vetting.

Tests the post-processing hard gate that penalizes candidates whose most
recent roles are unrelated to the job domain (career drift detection).
"""
import json
import pytest
from unittest.mock import MagicMock, patch


class TestRecencyHardGate:
    """Test the recency-of-experience post-processing hard gate.

    The hard gate runs AFTER the AI returns a score and recency_analysis,
    and enforces:
      - Both recent roles unrelated → 20pt penalty (midpoint of 15-25 range)
      - Most recent unrelated + 12+ months since relevant → 12pt penalty (midpoint of 10-15)
      - Recent role is relevant → no penalty
    """

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

    def _make_ai_response(self, match_score, recency_analysis=None, gaps="",
                          years_analysis=None):
        """Build a mock AI JSON response with recency_analysis."""
        resp = {
            "match_score": match_score,
            "match_summary": "Test summary",
            "skills_match": "Azure, Python",
            "experience_match": "Cloud engineer at Tier1",
            "gaps_identified": gaps,
            "key_requirements": "• Azure integration\n• Python\n• CI/CD",
            "years_analysis": years_analysis or {},
        }
        if recency_analysis is not None:
            resp["recency_analysis"] = recency_analysis
        return resp

    def _run_analysis(self, service, ai_response, job_id=34517):
        """Run analyze_candidate_job_match with a mocked OpenAI response."""
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = json.dumps(ai_response)
        service.openai_client.chat.completions.create.return_value = mock_completion

        job = {
            'id': job_id,
            'title': 'Azure Integration Developer',
            'description': 'Need Azure, Python, CI/CD experience',
            'address': {'city': 'Ottawa', 'state': 'ON', 'countryName': 'Canada'},
            'onSite': 2  # Hybrid
        }

        with patch.object(service, '_get_job_custom_requirements', return_value=None):
            result = service.analyze_candidate_job_match(
                resume_text="Sample resume text",
                job=job,
                prefetched_requirements="• Azure integration services\n• Python\n• CI/CD"
            )

        return result

    # ── Test: Both recent roles unrelated → 20pt penalty ──

    def test_both_roles_unrelated_applies_20pt_penalty(self, service):
        """Candidate whose last 2 roles are unrelated gets 20pt penalty."""
        ai_response = self._make_ai_response(
            match_score=95,
            recency_analysis={
                "most_recent_role": "Real Estate Consultant at eXp (Mar 2025 - Jan 2026)",
                "most_recent_role_relevant": False,
                "second_recent_role": "IT Operations Specialist at Tuulo (Jan 2024 - Nov 2024)",
                "second_recent_role_relevant": False,
                "last_relevant_role_ended": "March 2023",
                "months_since_relevant_work": 35,
                "penalty_applied": 20,
                "reasoning": "Both recent roles are unrelated to Azure Integration Developer"
            }
        )

        result = self._run_analysis(service, ai_response)

        assert result['match_score'] <= 75, \
            f"Score should be ≤75 (95-20) for both roles unrelated, got {result['match_score']}"
        assert "career trajectory has shifted" in result['gaps_identified']

    def test_both_roles_unrelated_no_bullet_points(self, service):
        """Roles with no bullet points should NOT be assumed relevant."""
        ai_response = self._make_ai_response(
            match_score=90,
            recency_analysis={
                "most_recent_role": "Real Estate Agent (Mar 2025 - Jan 2026)",
                "most_recent_role_relevant": False,
                "second_recent_role": "IT Operations Support (May 2023 - Dec 2023)",
                "second_recent_role_relevant": False,
                "last_relevant_role_ended": "March 2023",
                "months_since_relevant_work": 35,
                "penalty_applied": 20,
                "reasoning": "Second role has no bullet points; cannot assume relevance"
            }
        )

        result = self._run_analysis(service, ai_response)

        assert result['match_score'] <= 70, \
            f"Score should be ≤70 (90-20) when title-only roles, got {result['match_score']}"

    # ── Test: Most recent unrelated + 12+ months → 12pt penalty ──

    def test_most_recent_unrelated_12_months_gap(self, service):
        """Most recent role unrelated, relevant work ended 12+ months ago → 12pt."""
        ai_response = self._make_ai_response(
            match_score=85,
            recency_analysis={
                "most_recent_role": "Real Estate Consultant (Mar 2025 - Jan 2026)",
                "most_recent_role_relevant": False,
                "second_recent_role": "Cloud DevOps Engineer at Tier1 (May 2021 - Mar 2023)",
                "second_recent_role_relevant": True,
                "last_relevant_role_ended": "March 2023",
                "months_since_relevant_work": 35,
                "penalty_applied": 12,
                "reasoning": "Second role is relevant but ended 35 months ago"
            }
        )

        result = self._run_analysis(service, ai_response)

        assert result['match_score'] <= 73, \
            f"Score should be ≤73 (85-12) for stale relevance, got {result['match_score']}"
        assert "outside the target domain" in result['gaps_identified']

    # ── Test: Recent role is relevant → no penalty ──

    def test_active_practitioner_no_penalty(self, service):
        """Candidate currently working in the relevant domain → no penalty."""
        ai_response = self._make_ai_response(
            match_score=90,
            recency_analysis={
                "most_recent_role": "Azure Cloud Engineer at Acme (Jan 2024 - Present)",
                "most_recent_role_relevant": True,
                "second_recent_role": "Cloud Support Specialist at Beta (May 2021 - Dec 2023)",
                "second_recent_role_relevant": True,
                "last_relevant_role_ended": "current",
                "months_since_relevant_work": 0,
                "penalty_applied": 0,
                "reasoning": "Both recent roles are directly relevant"
            }
        )

        result = self._run_analysis(service, ai_response)

        assert result['match_score'] == 90, \
            f"Score should be 90 (no penalty) for active practitioner, got {result['match_score']}"

    def test_most_recent_unrelated_but_within_12_months(self, service):
        """Most recent role unrelated but relevant work ended < 12 months ago → no penalty."""
        ai_response = self._make_ai_response(
            match_score=85,
            recency_analysis={
                "most_recent_role": "Part-time Teaching (Jan 2026 - Present)",
                "most_recent_role_relevant": False,
                "second_recent_role": "Azure DevOps Engineer (Mar 2024 - Dec 2025)",
                "second_recent_role_relevant": True,
                "last_relevant_role_ended": "December 2025",
                "months_since_relevant_work": 2,
                "penalty_applied": 0,
                "reasoning": "Most recent is unrelated but relevant work ended only 2 months ago"
            }
        )

        result = self._run_analysis(service, ai_response)

        assert result['match_score'] == 85, \
            f"Score should be 85 (no penalty < 12 months gap), got {result['match_score']}"

    # ── Test: Edge cases and fail-safes ──

    def test_missing_recency_analysis_no_crash(self, service):
        """If AI omits recency_analysis entirely, score passes through unchanged."""
        ai_response = self._make_ai_response(match_score=85)
        ai_response.pop('recency_analysis', None)

        result = self._run_analysis(service, ai_response)

        assert result['match_score'] == 85, \
            f"Score should be unchanged when recency_analysis missing, got {result['match_score']}"

    def test_empty_recency_analysis_no_crash(self, service):
        """Empty recency_analysis dict → score unchanged."""
        ai_response = self._make_ai_response(match_score=85, recency_analysis={})

        result = self._run_analysis(service, ai_response)

        assert result['match_score'] == 85, \
            f"Score should be unchanged for empty recency_analysis, got {result['match_score']}"

    def test_penalty_floor_at_zero(self, service):
        """Score should not go below 0."""
        ai_response = self._make_ai_response(
            match_score=15,
            recency_analysis={
                "most_recent_role": "Real Estate Consultant",
                "most_recent_role_relevant": False,
                "second_recent_role": "Dog Walker",
                "second_recent_role_relevant": False,
                "last_relevant_role_ended": "January 2020",
                "months_since_relevant_work": 73,
                "penalty_applied": 25,
                "reasoning": "Completely unrelated career for 6+ years"
            }
        )

        result = self._run_analysis(service, ai_response)

        assert result['match_score'] >= 0, \
            f"Score should not go below 0, got {result['match_score']}"

    def test_appends_to_existing_gaps(self, service):
        """Recency note should be appended to existing gaps, not replace them."""
        ai_response = self._make_ai_response(
            match_score=80,
            gaps="Missing Bicep certification",
            recency_analysis={
                "most_recent_role": "Real Estate Consultant",
                "most_recent_role_relevant": False,
                "second_recent_role": "IT Support",
                "second_recent_role_relevant": False,
                "last_relevant_role_ended": "March 2023",
                "months_since_relevant_work": 35,
                "penalty_applied": 20,
                "reasoning": "Both roles unrelated"
            }
        )

        result = self._run_analysis(service, ai_response)

        assert "Missing Bicep certification" in result['gaps_identified'], \
            "Existing gaps should be preserved"
        assert "career trajectory" in result['gaps_identified'], \
            "Recency note should be appended"

    def test_ai_penalty_larger_than_hard_gate_uses_ai(self, service):
        """If AI already applied a larger penalty, use the AI's penalty."""
        ai_response = self._make_ai_response(
            match_score=70,  # AI already penalized from 95 to 70 (25pt)
            recency_analysis={
                "most_recent_role": "Real Estate Consultant",
                "most_recent_role_relevant": False,
                "second_recent_role": "IT Operations Support",
                "second_recent_role_relevant": False,
                "last_relevant_role_ended": "March 2023",
                "months_since_relevant_work": 35,
                "penalty_applied": 25,  # AI applied 25pt
                "reasoning": "Both roles unrelated"
            }
        )

        result = self._run_analysis(service, ai_response)

        # AI already scored 70. Hard gate target penalty is 20 (midpoint).
        # But AI applied 25, so effective_penalty = max(20, 25) = 25.
        # New score = 70 - 25 = 45
        assert result['match_score'] <= 50, \
            f"Score should reflect larger AI penalty, got {result['match_score']}"


class TestRecencyPromptPresence:
    """Test that the prompt includes recency_analysis instructions."""

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

    def test_system_message_contains_rule_14(self, service):
        """System message should include Rule 14 about recency."""
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = json.dumps({
            "match_score": 70,
            "match_summary": "test",
            "skills_match": "test",
            "experience_match": "test",
            "gaps_identified": "test",
            "key_requirements": "test",
            "years_analysis": {},
            "recency_analysis": {}
        })
        service.openai_client.chat.completions.create.return_value = mock_completion

        job = {
            'id': 999,
            'title': 'Test Job',
            'description': 'Need Azure experience',
            'address': {},
            'onSite': 1
        }

        with patch.object(service, '_get_job_custom_requirements', return_value=None):
            service.analyze_candidate_job_match(
                resume_text="Sample resume",
                job=job,
                prefetched_requirements=None
            )

        call_args = service.openai_client.chat.completions.create.call_args
        messages = call_args.kwargs.get('messages', call_args[1].get('messages', []))
        system_prompt = messages[0]['content']
        user_prompt = messages[1]['content']

        # Check system prompt contains Rule 14
        assert "RECENCY OF RELEVANT EXPERIENCE" in system_prompt
        assert "career trajectory" in system_prompt
        assert "bullet points" in system_prompt

        # Check user prompt contains recency_analysis schema
        assert "recency_analysis" in user_prompt
        assert "most_recent_role_relevant" in user_prompt
        assert "months_since_relevant_work" in user_prompt

    def test_scoring_band_mentions_recency(self, service):
        """The 85-100 scoring band should mention recency requirement."""
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = json.dumps({
            "match_score": 70,
            "match_summary": "test",
            "skills_match": "test",
            "experience_match": "test",
            "gaps_identified": "test",
            "key_requirements": "test",
            "years_analysis": {},
            "recency_analysis": {}
        })
        service.openai_client.chat.completions.create.return_value = mock_completion

        job = {
            'id': 999,
            'title': 'Test Job',
            'description': 'Need Azure',
            'address': {},
            'onSite': 1
        }

        with patch.object(service, '_get_job_custom_requirements', return_value=None):
            service.analyze_candidate_job_match(
                resume_text="Sample resume",
                job=job,
                prefetched_requirements=None
            )

        call_args = service.openai_client.chat.completions.create.call_args
        messages = call_args.kwargs.get('messages', call_args[1].get('messages', []))
        user_prompt = messages[1]['content']

        assert "practiced relevant skills in a recent role" in user_prompt
