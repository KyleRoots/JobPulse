"""
Tests for the May 2026 recruiter-email enhancements:
  1. Subject line includes top-job title + ID (Option A format)
  2. Resume attachment with graceful fallback when Bullhorn fetch fails
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from screening.notification import (
    NotificationMixin,
    _build_recruiter_subject,
    _resume_content_type,
    _safe_resume_filename,
    _RESUME_ATTACHMENT_MAX_BYTES,
)


def _match(job_id=None, title=None, score=None):
    return SimpleNamespace(
        bullhorn_job_id=job_id,
        job_title=title,
        match_score=score,
    )


# ---------------------------------------------------------------------------
# Subject line — Option A
# ---------------------------------------------------------------------------
class TestSubjectLine:

    def test_single_match_includes_title_and_id(self):
        s = _build_recruiter_subject(
            'Jane Doe',
            [_match(job_id=12345, title='Senior Data Engineer', score=88.5)],
        )
        assert s == 'Scout: Jane Doe — Senior Data Engineer (Job #12345)'

    def test_multi_match_uses_highest_score_and_plus_n_more(self):
        matches = [
            _match(job_id=1, title='Junior Dev', score=70.0),
            _match(job_id=2, title='Senior Data Engineer', score=92.0),
            _match(job_id=3, title='Data Architect', score=85.0),
        ]
        s = _build_recruiter_subject('Jane Doe', matches)
        assert s == 'Scout: Jane Doe — Senior Data Engineer (Job #2) +2 more'

    def test_two_matches_says_plus_one_more(self):
        matches = [
            _match(job_id=1, title='Role A', score=80),
            _match(job_id=2, title='Role B', score=70),
        ]
        s = _build_recruiter_subject('John Smith', matches)
        assert s.endswith('+1 more')
        assert 'Role A' in s and 'Job #1' in s

    def test_falls_back_to_legacy_when_no_matches(self):
        s = _build_recruiter_subject('Jane Doe', [])
        assert 'Qualified Candidate Alert' in s
        assert 'Jane Doe' in s

    def test_handles_missing_title(self):
        s = _build_recruiter_subject(
            'Jane', [_match(job_id=99, title=None, score=80)]
        )
        assert 'Position' in s and 'Job #99' in s

    def test_handles_missing_job_id(self):
        s = _build_recruiter_subject(
            'Jane', [_match(job_id=None, title='Engineer', score=80)]
        )
        assert s == 'Scout: Jane — Engineer'

    def test_handles_missing_score_treated_as_zero(self):
        matches = [
            _match(job_id=1, title='Has Score', score=50),
            _match(job_id=2, title='No Score', score=None),
        ]
        s = _build_recruiter_subject('Jane', matches)
        # Has Score (50) > None (0) → Has Score wins
        assert 'Has Score' in s and 'Job #1' in s

    def test_handles_blank_candidate_name(self):
        s = _build_recruiter_subject(
            '', [_match(job_id=1, title='Eng', score=80)]
        )
        assert 'Candidate' in s


# ---------------------------------------------------------------------------
# Resume helpers
# ---------------------------------------------------------------------------
class TestResumeHelpers:

    def test_content_type_pdf(self):
        assert _resume_content_type('Jane_Resume.pdf') == 'application/pdf'

    def test_content_type_docx(self):
        assert _resume_content_type('resume.DOCX') == \
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

    def test_content_type_unknown_falls_back(self):
        assert _resume_content_type('weird.xyz') == 'application/octet-stream'

    def test_content_type_none(self):
        assert _resume_content_type(None) == 'application/octet-stream'

    def test_safe_filename_preserves_extension(self):
        assert _safe_resume_filename('Jane Doe', 'orig.pdf') == 'Jane_Doe_Resume.pdf'

    def test_safe_filename_sanitizes_special_chars(self):
        out = _safe_resume_filename("O'Brien, Mary-Jane", 'r.docx')
        assert "'" not in out and ',' not in out
        assert out.endswith('.docx')

    def test_safe_filename_defaults_to_pdf(self):
        out = _safe_resume_filename('Jane', None)
        assert out == 'Jane_Resume.pdf'

    def test_safe_filename_blank_name_falls_back(self):
        assert _safe_resume_filename('', 'r.pdf') == 'Candidate_Resume.pdf'


# ---------------------------------------------------------------------------
# _fetch_resume_attachment — fail-open behavior
# ---------------------------------------------------------------------------
class TestResumeFetch:

    def _build_mixin(self, get_resume_return=None, get_resume_raises=None):
        class _Stub(NotificationMixin):
            def get_candidate_resume(self, candidate_id):
                if get_resume_raises:
                    raise get_resume_raises
                return get_resume_return

        return _Stub()

    def test_returns_none_when_no_candidate_id(self):
        mixin = self._build_mixin(get_resume_return=(b'data', 'r.pdf'))
        assert mixin._fetch_resume_attachment(0, 'Jane') is None

    def test_returns_attachment_when_resume_present(self):
        mixin = self._build_mixin(get_resume_return=(b'PDF DATA', 'orig.pdf'))
        result = mixin._fetch_resume_attachment(123, 'Jane Doe')
        assert isinstance(result, list) and len(result) == 1
        att = result[0]
        assert att['data'] == b'PDF DATA'
        assert att['filename'] == 'Jane_Doe_Resume.pdf'
        assert att['content_type'] == 'application/pdf'

    def test_returns_none_when_no_file_on_record(self):
        mixin = self._build_mixin(get_resume_return=(None, None))
        assert mixin._fetch_resume_attachment(123, 'Jane') is None

    def test_returns_none_on_fetch_exception(self):
        """Bullhorn HTTP/timeout failure must not break the email send."""
        mixin = self._build_mixin(get_resume_raises=RuntimeError('Bullhorn down'))
        assert mixin._fetch_resume_attachment(123, 'Jane') is None

    def test_returns_none_when_oversize(self):
        big = b'x' * (_RESUME_ATTACHMENT_MAX_BYTES + 1)
        mixin = self._build_mixin(get_resume_return=(big, 'huge.pdf'))
        assert mixin._fetch_resume_attachment(123, 'Jane') is None

    def test_at_size_cap_still_attaches(self):
        """Boundary: exactly at cap is allowed; cap+1 is rejected."""
        at_cap = b'x' * _RESUME_ATTACHMENT_MAX_BYTES
        mixin = self._build_mixin(get_resume_return=(at_cap, 'big.pdf'))
        result = mixin._fetch_resume_attachment(123, 'Jane')
        assert result is not None and len(result) == 1
