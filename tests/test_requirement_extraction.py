"""
Regression tests for extract_job_requirements guardrail.

Verifies:
  1. Requirements are successfully extracted from a valid job description
  2. Short/insufficient descriptions are rejected (returns None)
  3. The extraction prompt contains anti-hallucination rules
"""
import json
import pytest
from unittest.mock import MagicMock, patch


class TestRequirementExtractionGuardrail:
    """Tests for extract_job_requirements safety guardrails."""

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

    # ── Test 1: Valid description → requirements returned ──

    def test_extraction_returns_requirements(self, service):
        """A sufficiently long job description yields extracted requirements."""
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = (
            "• 5+ years Python development\n"
            "• Experience with AWS or Azure cloud platforms\n"
            "• Bachelor's degree in Computer Science\n"
            "• Strong SQL skills\n"
            "• Experience with CI/CD pipelines"
        )
        service.openai_client.chat.completions.create.return_value = mock_completion

        job_description = (
            "We are looking for a Senior Software Engineer to join our team. "
            "Requirements: 5+ years Python, AWS or Azure, BS in CS, SQL, CI/CD. "
            "Nice to have: React, GraphQL. "
            "This is a full-time role in our Dallas, TX office."
        )

        # Mock _save_ai_interpreted_requirements to avoid DB access
        with patch.object(service, '_save_ai_interpreted_requirements'):
            result = service.extract_job_requirements(
                job_id=12345,
                job_title="Senior Software Engineer",
                job_description=job_description
            )

        assert result is not None
        assert "Python" in result
        assert "AWS" in result or "Azure" in result

    # ── Test 2: Short description → None returned ──

    def test_extraction_rejects_short_description(self, service):
        """Job descriptions shorter than 50 characters are rejected."""
        result = service.extract_job_requirements(
            job_id=99999,
            job_title="Test Job",
            job_description="Short desc"
        )

        assert result is None
        # OpenAI should NOT have been called
        service.openai_client.chat.completions.create.assert_not_called()

    # ── Test 3: Prompt contains anti-hallucination rules ──

    def test_extraction_prompt_contains_anti_hallucination_rules(self, service):
        """The extraction prompt must include anti-hallucination safeguards."""
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "• Python 3+ years"
        service.openai_client.chat.completions.create.return_value = mock_completion

        job_description = (
            "We need a Data Scientist with experience in ML. "
            "The role involves building models and deploying to production. "
            "Must have experience with Python and TensorFlow."
        )

        with patch.object(service, '_save_ai_interpreted_requirements'):
            service.extract_job_requirements(
                job_id=12345,
                job_title="Data Scientist",
                job_description=job_description
            )

        # Verify the prompt sent to OpenAI contains anti-hallucination rules
        call_args = service.openai_client.chat.completions.create.call_args
        messages = call_args[1]['messages'] if 'messages' in call_args[1] else call_args[0][0]
        user_prompt = messages[1]['content']
        system_prompt = messages[0]['content']

        assert 'ANTI-HALLUCINATION' in user_prompt, \
            "User prompt must contain ANTI-HALLUCINATION rules"
        assert 'Do NOT infer or fabricate years-of-experience' in user_prompt, \
            "Prompt must forbid fabricating years requirements"
        assert 'NEVER infer' in system_prompt or 'never infer' in system_prompt.lower(), \
            "System prompt must instruct against inferring requirements"
