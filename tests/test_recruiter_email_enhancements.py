"""
Tests for the May 2026 recruiter-email enhancements:
  1. Subject line includes top-job title + ID (Option A format)
  2. Resume attachment with graceful fallback when Bullhorn fetch fails
  3. Newest-by-dateAdded résumé selection (not first entityFiles match)
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from screening.candidate_data import select_newest_resume_file
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


# ---------------------------------------------------------------------------
# select_newest_resume_file — Ocean Towne-style multi-version profiles
# ---------------------------------------------------------------------------
class TestSelectNewestResumeFile:

    def test_picks_newest_resume_not_first_in_list(self):
        """entityFiles often returns oldest-first; first 'Resume' must not win."""
        files = [
            {'id': 1, 'name': 'Resume_CV from Ocean Towne.docx', 'type': 'Resume',
             'dateAdded': 1704916841400},
            {'id': 2, 'name': 'Resume of ocean Towne .docx', 'type': 'Resume',
             'dateAdded': 1775275932297},
            {'id': 3, 'name': 'ML__E.docx', 'type': 'Resume',
             'dateAdded': 1786052046267},
        ]
        picked = select_newest_resume_file(files)
        assert picked is not None
        assert picked['id'] == 3
        assert picked['name'] == 'ML__E.docx'

    def test_prefers_resume_label_over_newer_non_resume_doc(self):
        files = [
            {'id': 10, 'name': 'cover_letter.docx', 'type': 'Cover Letter',
             'dateAdded': 9_000},
            {'id': 11, 'name': 'ML__E.docx', 'type': 'Resume',
             'dateAdded': 5_000},
        ]
        picked = select_newest_resume_file(files)
        assert picked['id'] == 11

    def test_falls_back_to_newest_doc_extension(self):
        files = [
            {'id': 1, 'name': 'notes.txt', 'type': 'Other', 'dateAdded': 1},
            {'id': 2, 'name': 'ocean Towne .docx', 'type': '', 'dateAdded': 100},
            {'id': 3, 'name': 'photo.png', 'type': 'Other', 'dateAdded': 200},
        ]
        picked = select_newest_resume_file(files)
        assert picked['id'] == 2

    def test_empty_and_none(self):
        assert select_newest_resume_file(None) is None
        assert select_newest_resume_file([]) is None

    def test_get_candidate_resume_downloads_newest(self):
        """End-to-end: get_candidate_resume must download newest Resume file."""
        from screening.candidate_data import CandidateDataAccessMixin

        class _Stub(CandidateDataAccessMixin):
            def _get_bullhorn_service(self):
                return self._bh

        stub = _Stub()
        session = MagicMock()
        list_resp = MagicMock(status_code=200)
        list_resp.json.return_value = {
            'EntityFiles': [
                {'id': 1, 'name': 'Resume_old.docx', 'type': 'Resume',
                 'dateAdded': 100},
                {'id': 99, 'name': 'ML__E.docx', 'type': 'Resume',
                 'dateAdded': 999},
            ]
        }
        dl_resp = MagicMock(status_code=200)
        dl_resp.content = b'NEWEST-BYTES'
        dl_resp.headers = {'Content-Type': 'application/octet-stream'}
        session.get.side_effect = [list_resp, dl_resp]
        stub._bh = SimpleNamespace(
            base_url='https://bh.example/',
            rest_token='tok',
            session=session,
        )

        content, filename = stub.get_candidate_resume(4309619)
        assert content == b'NEWEST-BYTES'
        assert filename == 'ML__E.docx'
        # Second GET is the file download for id 99
        dl_url = session.get.call_args_list[1][0][0]
        assert dl_url.endswith('/file/Candidate/4309619/99')
