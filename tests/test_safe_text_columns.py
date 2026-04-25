"""Regression tests for the SafeText / SafeString persistence-boundary guard.

These tests defend two invariants:

1. ``SafeText`` and ``SafeString`` strip NUL bytes (``\\x00``) from any value
   bound to them, no matter how the value reached the column. This is the
   actual safety property the types exist to provide.

2. Every Bullhorn-sourced text field on ``CandidateVettingLog`` and
   ``CandidateJobMatch`` is declared with ``SafeText``/``SafeString`` (not
   raw ``db.Text``/``db.String``). If a future contributor adds a new
   Bullhorn-sourced field and forgets to use the safe type, this test
   fails immediately rather than waiting for a NUL-byte payload to reach
   production and silently drop a candidate from screening.

The audited field list is intentionally explicit. To register a new
Bullhorn-sourced field, add it to the appropriate ``AUDITED_*`` set
below AND declare the column as ``SafeText``/``SafeString`` in
``models.py``. Removing a field from the audit set requires a deliberate
choice — do not auto-shrink it.
"""
import pytest
from sqlalchemy import Text, String

from app import app, db
from models import CandidateVettingLog, CandidateJobMatch
from utils.sqlalchemy_types import SafeText, SafeString


AUDITED_VETTING_LOG_FIELDS = {
    'candidate_name',
    'candidate_email',
    'applied_job_title',
    'resume_text',
    'error_message',
}

AUDITED_JOB_MATCH_FIELDS = {
    'job_title',
    'job_location',
    'tearsheet_name',
    'recruiter_name',
    'recruiter_email',
    'match_summary',
    'skills_match',
    'experience_match',
    'gaps_identified',
    'years_analysis_json',
    'prestige_employer',
}


def _column_type(model, attr_name):
    return model.__table__.columns[attr_name].type


# ---------------------------------------------------------------------------
# Type-decorator behavior
# ---------------------------------------------------------------------------

class TestSafeTypesStripNulBytes:
    """Both decorators must scrub NUL bytes regardless of caller behavior."""

    def test_safe_text_strips_nul_bytes_on_bind(self):
        col = SafeText()
        assert col.process_bind_param("hello\x00world", None) == "helloworld"

    def test_safe_string_strips_nul_bytes_on_bind(self):
        col = SafeString(255)
        assert col.process_bind_param("a\x00b\x00c", None) == "abc"

    def test_safe_text_passes_clean_value_through(self):
        col = SafeText()
        assert col.process_bind_param("clean text", None) == "clean text"

    def test_safe_string_passes_clean_value_through(self):
        col = SafeString(50)
        assert col.process_bind_param("clean text", None) == "clean text"

    def test_safe_text_handles_none(self):
        assert SafeText().process_bind_param(None, None) is None

    def test_safe_string_handles_none(self):
        assert SafeString(255).process_bind_param(None, None) is None

    def test_safe_string_preserves_length_kwarg(self):
        col = SafeString(500)
        assert col.impl.length == 500


# ---------------------------------------------------------------------------
# Audit guard — fail fast if a Bullhorn-sourced field forgets the safe type
# ---------------------------------------------------------------------------

class TestAuditedFieldsUseSafeTypes:
    """Every field in the audit lists MUST use SafeText or SafeString."""

    @pytest.mark.parametrize("field", sorted(AUDITED_VETTING_LOG_FIELDS))
    def test_vetting_log_field_uses_safe_type(self, field):
        col_type = _column_type(CandidateVettingLog, field)
        assert isinstance(col_type, (SafeText, SafeString)), (
            f"CandidateVettingLog.{field} must use SafeText/SafeString — "
            f"raw {type(col_type).__name__} allows NUL bytes through to "
            f"PostgreSQL and will silently drop candidates from screening "
            f"when Bullhorn payloads contain PDF/Word artifacts."
        )

    @pytest.mark.parametrize("field", sorted(AUDITED_JOB_MATCH_FIELDS))
    def test_job_match_field_uses_safe_type(self, field):
        col_type = _column_type(CandidateJobMatch, field)
        assert isinstance(col_type, (SafeText, SafeString)), (
            f"CandidateJobMatch.{field} must use SafeText/SafeString — "
            f"raw {type(col_type).__name__} allows NUL bytes through to "
            f"PostgreSQL and will silently drop candidates from screening "
            f"when Bullhorn payloads contain PDF/Word artifacts."
        )


# ---------------------------------------------------------------------------
# End-to-end integration — write and read back through the ORM
# ---------------------------------------------------------------------------

class TestEndToEndPersistence:
    """Round-trip a NUL-bearing payload through the real ORM/DB stack."""

    def test_vetting_log_strips_nul_via_orm(self):
        with app.app_context():
            log = CandidateVettingLog(
                bullhorn_candidate_id=999_000_001,
                candidate_name="Jane\x00Doe",
                candidate_email="jane\x00@example.com",
                applied_job_title="Senior\x00Engineer",
                resume_text="Resume body with\x00 NUL",
                error_message="failed\x00reason",
            )
            db.session.add(log)
            db.session.commit()
            try:
                fetched = db.session.get(CandidateVettingLog, log.id)
                assert fetched.candidate_name == "JaneDoe"
                assert fetched.candidate_email == "jane@example.com"
                assert fetched.applied_job_title == "SeniorEngineer"
                assert fetched.resume_text == "Resume body with NUL"
                assert fetched.error_message == "failedreason"
            finally:
                db.session.delete(fetched)
                db.session.commit()

    def test_job_match_strips_nul_via_orm(self):
        with app.app_context():
            log = CandidateVettingLog(
                bullhorn_candidate_id=999_000_002,
                candidate_name="Test",
            )
            db.session.add(log)
            db.session.flush()
            match = CandidateJobMatch(
                vetting_log_id=log.id,
                bullhorn_job_id=888_000_001,
                job_title="Lead\x00Architect",
                job_location="Austin\x00, TX",
                tearsheet_name="Tear\x00sheet",
                recruiter_name="Rec\x00ruiter",
                recruiter_email="rec\x00@x.com",
                match_score=85.0,
                match_summary="Strong\x00fit",
                skills_match="Skill\x00list",
                experience_match="Exp\x00detail",
                gaps_identified="Gap\x00list",
                years_analysis_json='{"a"\x00:1}',
                prestige_employer="Big\x00Corp",
            )
            db.session.add(match)
            db.session.commit()
            try:
                fetched = db.session.get(CandidateJobMatch, match.id)
                assert fetched.job_title == "LeadArchitect"
                assert fetched.job_location == "Austin, TX"
                assert fetched.tearsheet_name == "Tearsheet"
                assert fetched.recruiter_name == "Recruiter"
                assert fetched.recruiter_email == "rec@x.com"
                assert fetched.match_summary == "Strongfit"
                assert fetched.skills_match == "Skilllist"
                assert fetched.experience_match == "Expdetail"
                assert fetched.gaps_identified == "Gaplist"
                assert fetched.years_analysis_json == '{"a":1}'
                assert fetched.prestige_employer == "BigCorp"
            finally:
                db.session.delete(fetched)
                db.session.delete(log)
                db.session.commit()
