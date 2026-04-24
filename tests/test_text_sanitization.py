"""
Tests for utils.text_sanitization.sanitize_text and its application at
every persistence boundary that touches Bullhorn / OpenAI / user input.

PostgreSQL TEXT/VARCHAR columns reject NUL bytes (0x00). When candidate
descriptions, resume text, or AI output contain NUL bytes from upstream
sources (PDF artifacts, Bullhorn paste pipelines, OCR), the row fails
to flush and the candidate is silently dropped from screening.

These tests guarantee:
  1. The helper itself handles every input shape correctly.
  2. The helper is wired at every CandidateVettingLog and
     CandidateJobMatch field that comes from external systems.
  3. The legacy `_sanitize_text` import alias still works.
  4. Values round-trip through the ORM with NUL bytes stripped.
"""

import inspect
import pytest


# ============================================================================
# 1. Unit tests for sanitize_text()
# ============================================================================
class TestSanitizeTextHelper:

    def test_none_input_returns_none(self):
        from utils.text_sanitization import sanitize_text
        assert sanitize_text(None) is None

    def test_empty_string_returns_empty(self):
        from utils.text_sanitization import sanitize_text
        assert sanitize_text('') == ''

    def test_plain_string_unchanged(self):
        from utils.text_sanitization import sanitize_text
        assert sanitize_text('John Smith') == 'John Smith'

    def test_single_nul_byte_stripped(self):
        from utils.text_sanitization import sanitize_text
        assert sanitize_text('John\x00Smith') == 'JohnSmith'

    def test_multiple_nul_bytes_stripped(self):
        from utils.text_sanitization import sanitize_text
        assert sanitize_text('a\x00b\x00c\x00d') == 'abcd'

    def test_nul_at_start(self):
        from utils.text_sanitization import sanitize_text
        assert sanitize_text('\x00leading') == 'leading'

    def test_nul_at_end(self):
        from utils.text_sanitization import sanitize_text
        assert sanitize_text('trailing\x00') == 'trailing'

    def test_only_nul_bytes_returns_empty(self):
        from utils.text_sanitization import sanitize_text
        assert sanitize_text('\x00\x00\x00') == ''

    def test_preserves_other_whitespace_and_unicode(self):
        from utils.text_sanitization import sanitize_text
        # Tabs, newlines, accented chars, emoji must all survive
        s = 'Café\tWorld\n— with résumé\u00e9 ✨'
        assert sanitize_text(s) == s

    def test_non_string_input_coerced(self):
        from utils.text_sanitization import sanitize_text
        # Defensive: callers sometimes pass ints from upstream payloads
        assert sanitize_text(12345) == '12345'
        assert sanitize_text(0) == '0'

    def test_long_string_with_embedded_nul(self):
        from utils.text_sanitization import sanitize_text
        body = ('A' * 10000) + '\x00' + ('B' * 10000)
        cleaned = sanitize_text(body)
        assert '\x00' not in cleaned
        assert len(cleaned) == 20000

    def test_realistic_resume_paste_artifact(self):
        from utils.text_sanitization import sanitize_text
        # Simulates a PDF→clipboard→Bullhorn description pipeline
        # leaving a NUL byte between page boundaries
        resume = 'Page 1 content here.\x00Page 2 content here.'
        cleaned = sanitize_text(resume)
        assert '\x00' not in cleaned
        assert 'Page 1 content here.Page 2 content here.' == cleaned


# ============================================================================
# 2. Back-compat: legacy `_sanitize_text` import in vetting.resume_utils
# ============================================================================
class TestLegacyImportAlias:

    def test_legacy_alias_is_importable(self):
        from vetting.resume_utils import _sanitize_text
        assert callable(_sanitize_text)

    def test_legacy_alias_strips_nul_bytes(self):
        from vetting.resume_utils import _sanitize_text
        assert _sanitize_text('hello\x00world') == 'helloworld'

    def test_legacy_alias_is_same_function_as_new_helper(self):
        from vetting.resume_utils import _sanitize_text
        from utils.text_sanitization import sanitize_text
        assert _sanitize_text is sanitize_text

    def test_extract_resume_text_still_sanitizes(self, monkeypatch):
        """The original consumer (resume extraction) must still produce
        NUL-free output after the helper move."""
        from vetting import resume_utils
        # Bypass real PDF/DOCX parsing by stubbing the raw extractor
        monkeypatch.setattr(
            resume_utils,
            '_extract_resume_text_raw',
            lambda content, filename: 'resume\x00body\x00here'
        )
        result = resume_utils.extract_resume_text(b'fake', 'fake.pdf')
        assert result == 'resumebodyhere'
        assert '\x00' not in result


# ============================================================================
# 3. Source-level wiring assertions
#    These pin down the exact persistence boundaries so that future edits
#    can't silently re-introduce the bug by removing a sanitize_text() call.
# ============================================================================
class TestPersistenceBoundaryWiring:

    def _read(self, relpath):
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, relpath), 'r', encoding='utf-8') as f:
            return f.read()

    def test_cvs_imports_sanitize_text(self):
        src = self._read('candidate_vetting_service.py')
        assert 'from utils.text_sanitization import sanitize_text' in src

    def test_cvs_sanitizes_candidate_name(self):
        src = self._read('candidate_vetting_service.py')
        assert "candidate_name = sanitize_text(" in src

    def test_cvs_sanitizes_candidate_email(self):
        src = self._read('candidate_vetting_service.py')
        assert "candidate_email = sanitize_text(" in src

    def test_cvs_sanitizes_description_resume_path(self):
        """The root cause: line ~382 used to assign raw description."""
        src = self._read('candidate_vetting_service.py')
        assert "resume_text = sanitize_text(description)" in src

    def test_cvs_sanitizes_error_message(self):
        src = self._read('candidate_vetting_service.py')
        assert "vetting_log.error_message = sanitize_text(str(e)" in src

    def test_cvs_sanitizes_all_match_record_text_fields(self):
        src = self._read('candidate_vetting_service.py')
        # Every text/varchar field on CandidateJobMatch coming from
        # upstream (Bullhorn job + AI analysis) must be sanitized
        required = [
            'job_title=sanitize_text(',
            'job_location=sanitize_text(',
            'tearsheet_name=sanitize_text(',
            'recruiter_name=sanitize_text(',
            'recruiter_email=sanitize_text(',
            'match_summary=sanitize_text(',
            'skills_match=sanitize_text(',
            'experience_match=sanitize_text(',
            'gaps_identified=sanitize_text(',
            'years_analysis_json=sanitize_text(',
        ]
        for needle in required:
            assert needle in src, f"Missing sanitize_text wrap: {needle}"

    def test_sandbox_imports_sanitize_text(self):
        src = self._read('routes/vetting_sandbox.py')
        assert 'from utils.text_sanitization import sanitize_text' in src

    def test_sandbox_sanitizes_vetting_log_text_fields(self):
        src = self._read('routes/vetting_sandbox.py')
        required = [
            'candidate_name=sanitize_text(',
            'candidate_email=sanitize_text(',
            'applied_job_title=sanitize_text(',
            'resume_text=sanitize_text(',
        ]
        for needle in required:
            assert needle in src, f"Missing sanitize_text wrap in sandbox: {needle}"

    def test_sandbox_sanitizes_match_record_text_fields(self):
        src = self._read('routes/vetting_sandbox.py')
        required = [
            'job_title=sanitize_text(',
            'job_location=sanitize_text(',
            'match_summary=sanitize_text(',
            'skills_match=sanitize_text(',
            'experience_match=sanitize_text(',
            'gaps_identified=sanitize_text(',
        ]
        for needle in required:
            assert needle in src, f"Missing sanitize_text wrap in sandbox match: {needle}"


# ============================================================================
# 4. ORM round-trip — values arrive at the database column NUL-free
# ============================================================================
class TestORMRoundTripStripsNulBytes:

    def test_vetting_log_round_trip_is_nul_free(self, app):
        """When we apply sanitize_text before assignment, the stored row
        contains no NUL bytes. (SQLite tolerates NULs but PostgreSQL does
        not — this test guarantees the value never reaches the DB layer
        with NULs in the first place.)"""
        from app import db
        from models import CandidateVettingLog
        from utils.text_sanitization import sanitize_text

        with app.app_context():
            log = CandidateVettingLog(
                bullhorn_candidate_id=999991,
                candidate_name=sanitize_text('John\x00Smith'),
                candidate_email=sanitize_text('john\x00@example.com'),
                resume_text=sanitize_text('Resume body\x00with\x00nuls'),
                error_message=sanitize_text('boom\x00details'),
                status='completed',
            )
            db.session.add(log)
            db.session.commit()
            log_id = log.id

            # Re-fetch from DB
            db.session.expire_all()
            fetched = db.session.get(CandidateVettingLog, log_id)
            assert fetched is not None
            assert fetched.candidate_name == 'JohnSmith'
            assert fetched.candidate_email == 'john@example.com'
            assert fetched.resume_text == 'Resume bodywithnuls'
            assert fetched.error_message == 'boomdetails'
            for field in (fetched.candidate_name, fetched.candidate_email,
                          fetched.resume_text, fetched.error_message):
                assert '\x00' not in (field or '')

            db.session.delete(fetched)
            db.session.commit()

    def test_match_record_round_trip_is_nul_free(self, app):
        from app import db
        from models import CandidateVettingLog, CandidateJobMatch
        from utils.text_sanitization import sanitize_text

        with app.app_context():
            log = CandidateVettingLog(
                bullhorn_candidate_id=999992,
                candidate_name='Test',
                status='completed',
            )
            db.session.add(log)
            db.session.flush()

            match = CandidateJobMatch(
                vetting_log_id=log.id,
                bullhorn_job_id=12345,
                job_title=sanitize_text('Senior\x00Dev'),
                job_location=sanitize_text('Toronto\x00ON'),
                tearsheet_name=sanitize_text('Sponsored\x00OTT'),
                recruiter_name=sanitize_text('Jane\x00Doe'),
                recruiter_email=sanitize_text('jane\x00@example.com'),
                match_score=85.0,
                is_qualified=True,
                match_summary=sanitize_text('Great fit\x00overall'),
                skills_match=sanitize_text('Python\x00SQL'),
                experience_match=sanitize_text('5\x00yrs'),
                gaps_identified=sanitize_text('None\x00known'),
                years_analysis_json=sanitize_text('{"yrs": 5}\x00'),
            )
            db.session.add(match)
            db.session.commit()
            match_id = match.id

            db.session.expire_all()
            fetched = db.session.get(CandidateJobMatch, match_id)
            assert fetched is not None
            for attr in ('job_title', 'job_location', 'tearsheet_name',
                         'recruiter_name', 'recruiter_email',
                         'match_summary', 'skills_match', 'experience_match',
                         'gaps_identified', 'years_analysis_json'):
                value = getattr(fetched, attr)
                assert '\x00' not in (value or ''), f"{attr} contained NUL"

            assert fetched.job_title == 'SeniorDev'
            assert fetched.match_summary == 'Great fitoverall'

            db.session.delete(fetched)
            db.session.delete(log)
            db.session.commit()


# ============================================================================
# 5. End-to-end: the description path no longer raises on NUL bytes
# ============================================================================
class TestDescriptionPathDoesNotRaise:

    def test_description_with_nul_does_not_blow_up_assignment(self, app):
        """The original failure mode: line 386 of candidate_vetting_service.py
        used to assign raw description (with NULs) to vetting_log.resume_text.
        After the fix, sanitize_text is applied. This test recreates the
        critical assignment in isolation to prove the fix works."""
        from app import db
        from models import CandidateVettingLog
        from utils.text_sanitization import sanitize_text

        # Simulate the cleaned description with NUL bytes baked in
        # (matches what arrives at line 385 after HTML stripping)
        description = ('A' * 50) + '\x00' + ('B' * 50) + '\x00' + ('C' * 50)
        assert '\x00' in description

        # This is the exact assignment now in candidate_vetting_service.py
        resume_text = sanitize_text(description)
        assert '\x00' not in resume_text
        assert len(resume_text) == 150

        with app.app_context():
            log = CandidateVettingLog(
                bullhorn_candidate_id=999993,
                candidate_name='Desc Test',
                resume_text=resume_text[:50000],
                status='completed',
            )
            db.session.add(log)
            # The flush would fail in production with raw description
            db.session.commit()
            assert '\x00' not in (log.resume_text or '')

            db.session.delete(log)
            db.session.commit()
