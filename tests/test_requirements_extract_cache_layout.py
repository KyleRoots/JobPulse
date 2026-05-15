"""Cache-layout regression tests for extract_job_requirements.

Verifies the prompt structure required for OpenAI prefix-caching:
the static instruction block must precede any variable per-job content
so the cacheable prefix spans system_message + instructions instead of
just system_message.

Targets the L3 cache-enablement work in the May 2026 cost batch:
screening.requirements_extract was running at 0.0% cache hit prior
to this restructure.
"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def service():
    with patch.dict('os.environ', {
        'OPENAI_API_KEY': 'test-key',
        'DATABASE_URL': 'sqlite:///:memory:',
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
            svc.model = 'gpt-5.4'
            svc.logger = MagicMock()
            return svc


def _captured_user_prompt(service):
    """Return the user-message content from the last OpenAI call."""
    call_args = service.openai_client.chat.completions.create.call_args
    messages = call_args.kwargs.get('messages') or call_args.args[0]
    user_msgs = [m for m in messages if m.get('role') == 'user']
    assert len(user_msgs) == 1, "expected exactly one user message"
    return user_msgs[0]['content']


def _build_extraction_call(service, job_title="Senior Engineer", job_description=None):
    """Trigger extract_job_requirements with a stub OpenAI response."""
    if job_description is None:
        job_description = (
            "We are looking for a Senior Engineer with strong Python skills. "
            "Requirements include 5+ years experience, AWS or Azure cloud "
            "platforms, Bachelor's degree in Computer Science, strong SQL, "
            "and experience with CI/CD pipelines. Nice to have: React, "
            "GraphQL. This is a full-time role in our Dallas, TX office."
        )
    mock_completion = MagicMock()
    mock_completion.choices = [MagicMock()]
    mock_completion.choices[0].message.content = (
        "• 5+ years Python\n• AWS or Azure\n• BS in CS\n• SQL\n• CI/CD"
    )
    service.openai_client.chat.completions.create.return_value = mock_completion

    with patch.object(service, '_save_ai_interpreted_requirements', create=True):
        with patch('services.openai_helper.resolve_model', return_value='gpt-5.4'):
            with patch('services.openai_helper.log_call'):
                result = service.extract_job_requirements(
                    job_id=12345,
                    job_title=job_title,
                    job_description=job_description,
                )
    return result, _captured_user_prompt(service)


class TestCacheOptimizedLayout:
    """Static instructions must come BEFORE variable per-job content."""

    def test_instructions_precede_job_title(self, service):
        _, prompt = _build_extraction_call(service)
        instructions_idx = prompt.find("CRITICAL ANTI-HALLUCINATION RULES")
        title_idx = prompt.find("JOB TITLE:")
        assert instructions_idx >= 0, "instructions block missing"
        assert title_idx >= 0, "job title marker missing"
        assert instructions_idx < title_idx, (
            "anti-hallucination instructions must precede JOB TITLE for "
            "prefix-caching — otherwise the cacheable prefix is broken"
        )

    def test_instructions_precede_job_description(self, service):
        _, prompt = _build_extraction_call(service)
        instructions_idx = prompt.find("Format as a bullet-point list")
        description_idx = prompt.find("JOB DESCRIPTION:")
        assert instructions_idx < description_idx, (
            "format instruction must precede JOB DESCRIPTION for caching"
        )

    def test_variable_content_is_at_end(self, service):
        _, prompt = _build_extraction_call(
            service,
            job_title="UNIQUE_TITLE_MARKER_XYZ",
            job_description=(
                "UNIQUE_DESC_MARKER_XYZ — this job needs Python, AWS, SQL, "
                "and 5+ years of experience building backend services."
            ),
        )
        # The unique markers should appear in the LAST 30% of the prompt,
        # confirming variable content lives at the tail.
        title_pos = prompt.find("UNIQUE_TITLE_MARKER_XYZ")
        desc_pos = prompt.find("UNIQUE_DESC_MARKER_XYZ")
        threshold = int(len(prompt) * 0.7)
        assert title_pos > threshold, (
            f"job title at pos {title_pos} should be past 70% mark "
            f"({threshold}) for prefix cacheability"
        )
        assert desc_pos > threshold, (
            f"job description at pos {desc_pos} should be past 70% mark "
            f"({threshold}) for prefix cacheability"
        )

    def test_static_prefix_is_byte_identical_across_jobs(self, service):
        """Two different jobs must produce IDENTICAL prompt prefixes
        up to the JOB TITLE marker — that's what makes caching work."""
        _, prompt_a = _build_extraction_call(
            service,
            job_title="Software Engineer",
            job_description=(
                "Looking for a Software Engineer with Python and AWS skills. "
                "Must have 3+ years experience and a CS degree."
            ),
        )
        # Reset the mock for a second call
        service.openai_client.chat.completions.create.reset_mock()
        _, prompt_b = _build_extraction_call(
            service,
            job_title="Data Scientist",
            job_description=(
                "Seeking a Data Scientist with PyTorch and SQL expertise. "
                "Must have 5+ years experience and an MS in Statistics."
            ),
        )
        prefix_a = prompt_a.split("JOB TITLE:")[0]
        prefix_b = prompt_b.split("JOB TITLE:")[0]
        assert prefix_a == prefix_b, (
            "static prefix must be byte-identical across jobs for cache reuse"
        )
        # Sanity check: prefix is non-trivial in size
        assert len(prefix_a) > 800, (
            f"static prefix is suspiciously small ({len(prefix_a)} chars) — "
            "expected the full instruction block to be cached"
        )


class TestSemanticPreservation:
    """The reorder must NOT change what the model is asked to do."""

    def test_anti_hallucination_rules_present(self, service):
        _, prompt = _build_extraction_call(service)
        for marker in [
            "CRITICAL ANTI-HALLUCINATION RULES",
            "ONLY list requirements that are EXPLICITLY written",
            "Do NOT infer or fabricate years-of-experience",
            "Do NOT add requirements based on what you think",
        ]:
            assert marker in prompt, f"missing anti-hallucination rule: {marker}"

    def test_focus_areas_present(self, service):
        _, prompt = _build_extraction_call(service)
        for marker in [
            "Required technical skills",
            "Required years of experience",
            "Required certifications",
            "Required education level",
            "Required industry-specific knowledge",
            "Required location or work authorization",
        ]:
            assert marker in prompt, f"missing focus area: {marker}"

    def test_exclusions_present(self, service):
        _, prompt = _build_extraction_call(service)
        for marker in [
            '"Nice to have"',
            "Soft skills",
            "Generic requirements",
            "EXACTLY 5-7 requirements",
        ]:
            assert marker in prompt, f"missing exclusion/format rule: {marker}"

    def test_extraction_still_returns_results(self, service):
        result, _ = _build_extraction_call(service)
        assert result is not None
        assert "Python" in result
