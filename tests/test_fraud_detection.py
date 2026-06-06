"""Tests for the Phase 1 fraud / fake-candidate detection layer.

Two layers of coverage:
  1. Pure-signal unit tests (no Flask, no DB) for `fraud_detection.signals`
     and `fraud_detection.disposable_domains`.
  2. Engine tests (`fraud_detection.engine.FraudSignalEngine`) against the
     shared SQLite test DB, verifying persistence, config gating, banding,
     and the fail-soft / advisory contract.
"""

import json
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from fraud_detection import signals as fsig
from fraud_detection.disposable_domains import extract_domain, is_disposable_domain


# ─────────────────────────────────────────────────────────────────────────────
# Pure signal tests (dependency-free)
# ─────────────────────────────────────────────────────────────────────────────

def test_disposable_domain_detection():
    assert is_disposable_domain("foo@mailinator.com") is True
    assert is_disposable_domain("foo@gmail.com") is False
    assert extract_domain("Foo.Bar@Example.COM") == "example.com"


def test_evaluate_disposable_email():
    sig = fsig.evaluate_disposable_email("a@mailinator.com")
    assert sig is not None
    assert sig.code == "disposable_email"
    assert sig.points == fsig.POINTS_DISPOSABLE_EMAIL
    assert fsig.evaluate_disposable_email("a@gmail.com") is None
    assert fsig.evaluate_disposable_email(None) is None


def test_contact_anomalies_name_with_url():
    sigs = fsig.evaluate_contact_anomalies("Click http://spam.ru", "a@b.com", "5551234567")
    codes = {s.code for s in sigs}
    assert "name_anomaly" in codes


def test_contact_anomalies_bad_email():
    sigs = fsig.evaluate_contact_anomalies("Jane Doe", "not-an-email", "5551234567")
    codes = {s.code for s in sigs}
    assert "email_syntax" in codes


def test_contact_anomalies_placeholder_phone():
    for bad in ("1111111111", "1234567890", "000"):
        sigs = fsig.evaluate_contact_anomalies("Jane Doe", "a@b.com", bad)
        assert any(s.code == "phone_anomaly" for s in sigs), bad


def test_contact_anomalies_clean():
    sigs = fsig.evaluate_contact_anomalies("Jane Doe", "jane@company.com", "4165551234")
    assert sigs == []


def test_work_history_future_date():
    future = (date.today() + timedelta(days=400)).isoformat()
    sigs = fsig.evaluate_work_history([{"title": "Eng", "start": future}])
    assert any(s.code == "work_future_date" for s in sigs)


def test_work_history_negative_duration():
    sigs = fsig.evaluate_work_history([
        {"title": "Eng", "start": "2022-01-01", "end": "2020-01-01"},
    ])
    assert any(s.code == "work_negative_duration" for s in sigs)


def test_work_history_overlap():
    sigs = fsig.evaluate_work_history([
        {"title": "A", "start": "2020-01-01", "end": "2023-01-01"},
        {"title": "B", "start": "2020-06-01", "end": "2023-06-01"},
    ])
    assert any(s.code == "work_overlap" for s in sigs)


def test_work_history_clean():
    sigs = fsig.evaluate_work_history([
        {"title": "A", "start": "2018-01-01", "end": "2020-01-01"},
        {"title": "B", "start": "2020-02-01", "end": "2022-01-01"},
    ])
    assert sigs == []


def test_resume_reuse():
    # No other records → no signals.
    assert fsig.evaluate_resume_reuse() == []
    assert fsig.evaluate_resume_reuse(genuine_identities=[], duplicate_records=[]) == []

    # A genuinely different identity → one scored signal carrying the proof.
    sigs = fsig.evaluate_resume_reuse(genuine_identities=[
        {"candidate_id": 1, "name": "Jane Smith", "email": "jane@x.com", "last_seen": "2026-05-01"},
        {"candidate_id": 2, "name": "John Doe", "email": "john@y.com", "last_seen": "2026-05-02"},
    ])
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.code == "resume_reuse" and sig.points == fsig.POINTS_RESUME_REUSE
    assert "Jane Smith" in sig.evidence and "jane@x.com" in sig.evidence
    assert sig.details["other_identities"] == 2

    # Same-person duplicate record → informational only (0 points), never scored.
    info = fsig.evaluate_resume_reuse(duplicate_records=[
        {"candidate_id": 9, "name": "Edmond Vartanian", "email": "ed@x.com", "last_seen": "2026-04-28"},
    ])
    assert len(info) == 1
    assert info[0].code == "resume_duplicate_record" and info[0].points == 0
    assert "merge" in info[0].evidence.lower()

    # Both at once → a scored genuine signal AND an informational duplicate note.
    both = fsig.evaluate_resume_reuse(
        genuine_identities=[{"name": "Imposter", "email": "x@x.com"}],
        duplicate_records=[{"name": "Self", "email": "self@x.com"}],
    )
    codes = {s.code for s in both}
    assert codes == {"resume_reuse", "resume_duplicate_record"}


def test_identity_reuse_threshold():
    assert fsig.evaluate_identity_reuse(distinct_names_for_email=2) == []
    sigs = fsig.evaluate_identity_reuse(distinct_names_for_email=4)
    assert any(s.code == "identity_reuse_email" for s in sigs)


def test_profile_near_duplicate():
    assert fsig.evaluate_profile_near_duplicate(0.99, identity_differs=False) is None
    assert fsig.evaluate_profile_near_duplicate(0.50, identity_differs=True) is None
    sig = fsig.evaluate_profile_near_duplicate(0.95, identity_differs=True)
    assert sig is not None and sig.code == "profile_near_duplicate"


def test_velocity():
    assert fsig.evaluate_velocity(3) is None
    sig = fsig.evaluate_velocity(10)
    assert sig is not None and sig.code == "application_velocity"


def test_parse_date_variants():
    assert fsig._parse_date("2020") == date(2020, 1, 1)
    assert fsig._parse_date("2020-05") == date(2020, 5, 1)
    assert fsig._parse_date("05/2020") == date(2020, 5, 1)
    assert fsig._parse_date("present") is None
    assert fsig._parse_date("") is None
    assert fsig._parse_date(datetime(2021, 3, 4, 12)) == date(2021, 3, 4)


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation banding
# ─────────────────────────────────────────────────────────────────────────────

def test_aggregate_clear():
    res = fsig.aggregate([fsig.evaluate_velocity(10)])  # 15 points
    assert res.risk_band == fsig.FraudRiskBand.CLEAR
    assert res.risk_score == 15


def test_aggregate_review():
    sigs = [
        fsig.evaluate_disposable_email("a@mailinator.com"),  # 25
        fsig.evaluate_velocity(10),                           # 15
    ]
    res = fsig.aggregate(sigs)  # 40 → review (>=40)
    assert res.risk_band == fsig.FraudRiskBand.REVIEW
    assert res.risk_score == 40


def test_aggregate_high_risk_and_cap():
    sigs = [
        *fsig.evaluate_resume_reuse(genuine_identities=[
            {"name": "Other Person", "email": "other@x.com"}]),  # 40
        fsig.evaluate_disposable_email("a@guerrillamail.com"),  # 25
        *fsig.evaluate_identity_reuse(distinct_names_for_email=5),  # 35
    ]
    res = fsig.aggregate(sigs)  # 100 cap → high_risk
    assert res.risk_band == fsig.FraudRiskBand.HIGH_RISK
    assert res.risk_score == 100


def test_aggregate_custom_thresholds():
    res = fsig.aggregate([fsig.evaluate_velocity(10)],
                         review_threshold=10, high_risk_threshold=14)
    assert res.risk_band == fsig.FraudRiskBand.HIGH_RISK


def test_aggregate_ignores_none():
    res = fsig.aggregate([None, None])
    assert res.risk_score == 0
    assert res.risk_band == fsig.FraudRiskBand.CLEAR


# ─────────────────────────────────────────────────────────────────────────────
# LinkedIn capture + reuse
# ─────────────────────────────────────────────────────────────────────────────

def test_extract_linkedin_url_variants():
    assert fsig.extract_linkedin_url(
        "Reach me at https://www.linkedin.com/in/Jane-Doe-123/"
    ) == "linkedin.com/in/jane-doe-123"
    assert fsig.extract_linkedin_url(
        "profile: linkedin.com/in/john_smith?utm=foo"
    ) == "linkedin.com/in/john_smith"
    # Country-subdomain + scheme variations normalize to the same key.
    assert fsig.extract_linkedin_url("http://uk.linkedin.com/in/abc") == \
        "linkedin.com/in/abc"


def test_extract_linkedin_url_none_and_company():
    assert fsig.extract_linkedin_url(None, "") == ""
    # Company pages (/company/) are NOT personal /in/ profiles.
    assert fsig.extract_linkedin_url("linkedin.com/company/acme") == ""
    # First match across multiple sources wins.
    assert fsig.extract_linkedin_url(None, "linkedin.com/in/second") == \
        "linkedin.com/in/second"


def test_evaluate_linkedin_reuse():
    sig = fsig.evaluate_linkedin("linkedin.com/in/x", 2)
    assert sig is not None
    assert sig.code == "linkedin_reuse"
    assert sig.points == fsig.POINTS_LINKEDIN_REUSE
    # No URL or no other identities → no signal.
    assert fsig.evaluate_linkedin("", 5) is None
    assert fsig.evaluate_linkedin("linkedin.com/in/x", 0) is None


# ─────────────────────────────────────────────────────────────────────────────
# Name completeness + third-party submission composite
# ─────────────────────────────────────────────────────────────────────────────

def test_is_incomplete_name():
    assert fsig.is_incomplete_name("Jane", "") is True          # first only
    assert fsig.is_incomplete_name("Jane", "D") is True         # first + initial
    assert fsig.is_incomplete_name("Jane", "D.") is True
    assert fsig.is_incomplete_name("Jane", "Doe") is False      # complete
    assert fsig.is_incomplete_name("", "Doe") is False          # no usable first
    assert fsig.is_incomplete_name("J", "") is False            # junk first


def test_evaluate_name_completeness():
    assert fsig.evaluate_name_completeness("Jane", "Doe") is None
    sig = fsig.evaluate_name_completeness("Jane", "D")
    assert sig is not None
    assert sig.code == "name_incomplete"
    assert sig.points == fsig.POINTS_NAME_INCOMPLETE


def test_is_personal_email():
    assert fsig.is_personal_email("a@gmail.com") is True
    assert fsig.is_personal_email("a@outlook.com") is True
    assert fsig.is_personal_email("a@acme-corp.com") is False
    assert fsig.is_personal_email(None) is False


def test_third_party_submission_composite():
    # Incomplete name + non-personal email → fires (base only).
    sig = fsig.evaluate_third_party_submission(
        name_incomplete=True, email_personal=False, foreign_location=False)
    assert sig is not None
    assert sig.code == "third_party_submission"
    assert sig.points == fsig.POINTS_THIRD_PARTY_BASE
    # Foreign-location amplifier adds points.
    sig2 = fsig.evaluate_third_party_submission(
        name_incomplete=True, email_personal=False, foreign_location=True)
    assert sig2.points == fsig.POINTS_THIRD_PARTY_BASE + fsig.POINTS_THIRD_PARTY_FOREIGN


def test_third_party_submission_requires_both_halves():
    # Personal email → no third-party flag even with incomplete name.
    assert fsig.evaluate_third_party_submission(
        name_incomplete=True, email_personal=True) is None
    # Complete name → no flag even with a corporate email.
    assert fsig.evaluate_third_party_submission(
        name_incomplete=False, email_personal=False) is None
    # Foreign location alone is NEVER a standalone trigger.
    assert fsig.evaluate_third_party_submission(
        name_incomplete=False, email_personal=False, foreign_location=True) is None


def test_third_party_composite_bands_review():
    # incomplete-name (8) + third-party base (32) = 40 → Review.
    sigs = [
        fsig.evaluate_name_completeness("Jane", "D"),
        fsig.evaluate_third_party_submission(
            name_incomplete=True, email_personal=False),
    ]
    res = fsig.aggregate(sigs)
    assert res.risk_score == 40
    assert res.risk_band == fsig.FraudRiskBand.REVIEW


# ─────────────────────────────────────────────────────────────────────────────
# Verbatim JD-mirror
# ─────────────────────────────────────────────────────────────────────────────

def _jd(n_words: int) -> str:
    return " ".join(f"word{i}" for i in range(n_words))


def test_jd_mirror_no_signal_for_short_jd():
    assert fsig.evaluate_jd_mirror("anything", "too short jd") is None


def test_jd_mirror_keyword_overlap_does_not_fire():
    jd = _jd(60)
    # Resume shares scattered individual keywords but no long contiguous run.
    resume = "word3 banana word17 apple word40 orange word55 grape " * 2
    assert fsig.evaluate_jd_mirror(resume, jd) is None


def test_jd_mirror_graduated_weight():
    jd = _jd(60)
    light = " ".join(f"word{i}" for i in range(10))    # 10-word run → light
    moderate = " ".join(f"word{i}" for i in range(20))  # 20 → moderate
    heavy = " ".join(f"word{i}" for i in range(35))     # 35 → heavy
    s_light = fsig.evaluate_jd_mirror(light, jd)
    s_mod = fsig.evaluate_jd_mirror(moderate, jd)
    s_heavy = fsig.evaluate_jd_mirror(heavy, jd)
    assert s_light.points == fsig.POINTS_JD_MIRROR_LIGHT
    assert s_mod.points == fsig.POINTS_JD_MIRROR_MODERATE
    assert s_heavy.points == fsig.POINTS_JD_MIRROR_HEAVY
    assert s_heavy.code == "jd_mirror"


def test_jd_mirror_below_min_run_no_signal():
    jd = _jd(60)
    # Only a 5-word contiguous run (< 8 minimum).
    resume = "word0 word1 word2 word3 word4 zzz qqq"
    assert fsig.evaluate_jd_mirror(resume, jd) is None


def test_jd_mirror_captures_verbatim_passage():
    """The signal records the actual copied passage + surrounding context from
    BOTH the resume and the posting (original casing/punctuation preserved), so
    recruiters can drill into exactly what was lifted — even on a Clear band."""
    jd = ("We are seeking a Senior Cloud Engineer to design, build, and operate "
          "scalable distributed systems across our global platform. " + _jd(60))
    resume = ("Experienced professional. design, build, and operate scalable "
              "distributed systems across our global platform. Other resume text.")
    sig = fsig.evaluate_jd_mirror(resume, jd)
    assert sig is not None and sig.code == "jd_mirror"
    d = sig.details
    assert d["longest_run_words"] >= 8
    # Verbatim passage reconstructed with original punctuation intact.
    assert "design, build, and operate scalable" in d["copied_text"]
    # Context excerpts include the copied passage plus surrounding words.
    assert d["copied_text"] in d["resume_excerpt"]
    assert d["copied_text"] in d["jd_excerpt"]
    assert "Experienced professional" in d["resume_excerpt"]
    assert "Senior Cloud Engineer" in d["jd_excerpt"]


def test_jd_mirror_passage_capped():
    """A very long verbatim lift is truncated for display but still flagged."""
    run = " ".join(f"word{i}" for i in range(120))  # well over the char cap
    jd = run + " tail words here for length " + _jd(20)
    sig = fsig.evaluate_jd_mirror(run, jd)
    assert sig is not None
    assert len(sig.details["copied_text"]) <= fsig.JD_MIRROR_MAX_PASSAGE_CHARS + 1
    assert sig.details["copied_text"].endswith("…")


def test_jd_mirror_note_includes_copied_passage():
    """The Bullhorn note additively documents the copied passage for a jd_mirror
    hit, without altering existing note structure or gating."""
    from fraud_detection.engine import FraudSignalEngine

    jd = ("We are seeking a Senior Cloud Engineer to design, build, and operate "
          "scalable distributed systems across our global platform. " + _jd(60))
    resume = ("Experienced professional. design, build, and operate scalable "
              "distributed systems across our global platform. Other resume text.")
    sig = fsig.evaluate_jd_mirror(resume, jd)
    result = fsig.FraudAssessmentResult(
        risk_score=sig.points,
        risk_band=fsig.FraudRiskBand.CLEAR,
        signals=[sig],
    )
    note = FraudSignalEngine._build_note_text(result)
    assert "Copied passage:" in note
    assert "design, build, and operate scalable" in note
    assert "In job posting:" in note


def test_render_mirror_excerpts_html_highlights_and_escapes():
    """The email helper renders resume vs posting excerpts, highlights the copied
    span, and HTML-escapes candidate/posting text."""
    from screening.notification import NotificationMixin

    details = {
        "copied_text": "design & build <systems>",
        "resume_excerpt": "Intro. design & build <systems> tail.",
        "jd_excerpt": "Posting design & build <systems> more.",
    }
    html = NotificationMixin._render_mirror_excerpts_html(details)
    assert "In job posting" in html
    assert "<mark" in html  # copied span highlighted
    # Raw angle brackets from the copied text are escaped, not emitted as tags.
    assert "<systems>" not in html
    assert "&lt;systems&gt;" in html


def test_render_mirror_excerpts_html_empty_when_no_passage():
    from screening.notification import NotificationMixin
    assert NotificationMixin._render_mirror_excerpts_html({}) == ""
    assert NotificationMixin._render_mirror_excerpts_html(
        {"longest_run_words": 9}
    ) == ""


def test_jd_mirror_highlights_both_sides_with_casing_mismatch():
    """When the posting renders the copied run with different casing/punctuation
    than the resume, each side is captured separately so BOTH excerpts highlight
    their own verbatim passage."""
    from screening.notification import NotificationMixin

    jd = ("Overview: We Deliver Scalable Cloud Native Data Platforms At Global "
          "Scale for clients. " + _jd(60))
    resume = ("Summary - we deliver scalable, cloud-native data platforms at "
              "global scale daily. More text.")
    sig = fsig.evaluate_jd_mirror(resume, jd)
    assert sig is not None
    d = sig.details
    # Resume and posting passages differ in casing/punctuation.
    assert d["copied_text"] != d["jd_passage"]
    assert d["copied_text"] in d["resume_excerpt"]
    assert d["jd_passage"] in d["jd_excerpt"]
    html = NotificationMixin._render_mirror_excerpts_html(d)
    # Both the resume and the posting excerpts get their copied span highlighted.
    assert html.count("<mark") == 2


def test_jd_mirror_highlight_survives_truncated_passage():
    """An over-long passage is stored ellipsized for display; the email helper
    must still highlight it by falling back to the pre-ellipsis prefix."""
    from screening.notification import NotificationMixin

    run = " ".join(f"word{i}" for i in range(120))  # exceeds the char cap
    jd = run + " tail words here for length " + _jd(20)
    sig = fsig.evaluate_jd_mirror(run, jd)
    assert sig is not None
    d = sig.details
    assert d["copied_text"].endswith("…")  # display passage is truncated
    html = NotificationMixin._render_mirror_excerpts_html(d)
    assert "<mark" in html  # highlight still rendered despite truncation


# ─────────────────────────────────────────────────────────────────────────────
# AI-style markers (informational only, 0 points)
# ─────────────────────────────────────────────────────────────────────────────

def test_ai_style_markers_informational_zero_points():
    text = "I led teams \u2014 grew revenue \u2014 shipped products \u2014 fast."
    sig = fsig.evaluate_ai_style_markers(text)
    assert sig is not None
    assert sig.code == "ai_style_markers"
    assert sig.points == 0
    assert sig.details.get("informational") is True


def test_ai_style_markers_below_threshold():
    assert fsig.evaluate_ai_style_markers("one \u2014 dash only") is None
    assert fsig.evaluate_ai_style_markers(None) is None


def test_ai_style_never_affects_band():
    # Even alongside a real signal, the 0-point informational marker adds nothing.
    sigs = [
        fsig.evaluate_velocity(10),  # 15
        fsig.evaluate_ai_style_markers(
            "a \u2014 b \u2014 c \u2014 d \u2014 e"),  # 0
    ]
    res = fsig.aggregate(sigs)
    assert res.risk_score == 15
    assert res.risk_band == fsig.FraudRiskBand.CLEAR
    # But the informational signal is retained for surfacing.
    assert any(s.code == "ai_style_markers" for s in res.signals)


# ─────────────────────────────────────────────────────────────────────────────
# Engine tests (use shared SQLite test DB via `app` fixture)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def _fraud_db(app):
    """Yield (db, models) within app context; clean fraud + vetting rows."""
    from app import db
    from models import CandidateFraudAssessment, CandidateVettingLog, VettingConfig
    with app.app_context():
        CandidateFraudAssessment.query.delete()
        CandidateVettingLog.query.delete()
        db.session.commit()
        yield db, CandidateFraudAssessment, CandidateVettingLog, VettingConfig
        db.session.rollback()
        CandidateFraudAssessment.query.delete()
        CandidateVettingLog.query.delete()
        db.session.commit()


def _set_config(db, VettingConfig, **kwargs):
    for k, v in kwargs.items():
        VettingConfig.set_value(k, str(v))


def test_engine_persists_assessment(_fraud_db):
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    from fraud_detection.engine import FraudSignalEngine

    _set_config(db, VettingConfig,
                fraud_detection_enabled='true',
                fraud_bullhorn_note_enabled='false',
                fraud_review_threshold='40',
                fraud_high_risk_threshold='75')

    log = VettingLog(bullhorn_candidate_id=9001, candidate_name="Spammy URL",
                     candidate_email="x@mailinator.com", status="processing")
    db.session.add(log)
    db.session.commit()

    candidate = {"id": 9001, "firstName": "Click http://spam.ru",
                 "lastName": "", "email": "x@mailinator.com",
                 "phone": "1111111111"}
    engine = FraudSignalEngine(bullhorn_service=None)
    result = engine.assess(candidate, log)

    assert result is not None
    assert result.bullhorn_candidate_id == 9001
    # disposable(25) + name_anomaly(20) + phone(15) = 60 → review band
    assert result.risk_score >= 60
    assert result.risk_band == 'review'
    sigs = json.loads(result.signals_json)
    codes = {s['code'] for s in sigs}
    assert 'disposable_email' in codes
    assert 'name_anomaly' in codes
    assert 'phone_anomaly' in codes
    # Disposable email must NOT also fire the third-party composite (distinct
    # pattern — avoids double-counting the same address).
    assert 'third_party_submission' not in codes


def test_engine_third_party_requires_valid_nonpersonal_email(_fraud_db):
    """Incomplete name alone (missing/personal email) must NOT band via the
    third-party composite — it requires a present, valid, non-personal,
    non-disposable email."""
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    from fraud_detection.engine import FraudSignalEngine
    _set_config(db, VettingConfig,
                fraud_detection_enabled='true',
                fraud_bullhorn_note_enabled='false',
                fraud_review_threshold='40',
                fraud_high_risk_threshold='75')
    engine = FraudSignalEngine(bullhorn_service=None)

    # (a) Missing email + incomplete name → composite must NOT fire.
    log_a = VettingLog(bullhorn_candidate_id=9101, candidate_name="Jane",
                       candidate_email="", status="processing")
    db.session.add(log_a)
    db.session.commit()
    res_a = engine.assess(
        {"id": 9101, "firstName": "Jane", "lastName": "", "email": ""}, log_a)
    codes_a = {s['code'] for s in json.loads(res_a.signals_json)}
    assert 'third_party_submission' not in codes_a
    assert res_a.risk_band == 'clear'

    # (b) Personal email + incomplete name → composite must NOT fire.
    log_b = VettingLog(bullhorn_candidate_id=9102, candidate_name="Jane",
                       candidate_email="jane@gmail.com", status="processing")
    db.session.add(log_b)
    db.session.commit()
    res_b = engine.assess(
        {"id": 9102, "firstName": "Jane", "lastName": "",
         "email": "jane@gmail.com"}, log_b)
    codes_b = {s['code'] for s in json.loads(res_b.signals_json)}
    assert 'third_party_submission' not in codes_b

    # (c) Valid corporate (non-personal) email + incomplete name → fires.
    log_c = VettingLog(bullhorn_candidate_id=9103, candidate_name="Jane",
                       candidate_email="jane@acme-corp.com", status="processing")
    db.session.add(log_c)
    db.session.commit()
    res_c = engine.assess(
        {"id": 9103, "firstName": "Jane", "lastName": "",
         "email": "jane@acme-corp.com"}, log_c)
    codes_c = {s['code'] for s in json.loads(res_c.signals_json)}
    assert 'third_party_submission' in codes_c
    assert res_c.risk_band == 'review'


def test_engine_high_risk_writes_note_when_enabled(_fraud_db):
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    from fraud_detection.engine import FraudSignalEngine

    _set_config(db, VettingConfig,
                fraud_detection_enabled='true',
                fraud_bullhorn_note_enabled='true',
                fraud_review_threshold='40',
                fraud_high_risk_threshold='75')

    log = VettingLog(bullhorn_candidate_id=9002, candidate_name="Repeat Identity",
                     candidate_email="z@mailinator.com", status="processing")
    db.session.add(log)
    db.session.commit()

    # disposable(25) + name URL(20) + bad phone(15) + work future(25) = 85 → high
    future = (date.today() + timedelta(days=500)).isoformat()
    candidate = {
        "id": 9002, "firstName": "See www.scam.net", "lastName": "",
        "email": "z@mailinator.com", "phone": "0000000",
        "workHistory": [{"title": "Eng", "start": future}],
    }
    bh = MagicMock()
    bh.create_candidate_note.return_value = 55501
    engine = FraudSignalEngine(bullhorn_service=bh)
    result = engine.assess(candidate, log)

    assert result.risk_band == 'high_risk'
    assert result.note_created is True
    assert result.bullhorn_note_id == 55501
    bh.create_candidate_note.assert_called_once()
    # Vendor-neutral: note action must not name any vendor.
    _, kwargs = bh.create_candidate_note.call_args
    assert kwargs.get('action') == 'Candidate Risk Review'


def test_engine_no_note_when_note_disabled(_fraud_db):
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    from fraud_detection.engine import FraudSignalEngine

    _set_config(db, VettingConfig,
                fraud_detection_enabled='true',
                fraud_bullhorn_note_enabled='false',
                fraud_review_threshold='40',
                fraud_high_risk_threshold='75')

    log = VettingLog(bullhorn_candidate_id=9003, candidate_name="High Risk",
                     candidate_email="q@mailinator.com", status="processing")
    db.session.add(log)
    db.session.commit()

    future = (date.today() + timedelta(days=500)).isoformat()
    candidate = {
        "id": 9003, "firstName": "Visit http://x.ru", "lastName": "",
        "email": "q@mailinator.com", "phone": "1111111111",
        "workHistory": [{"title": "Eng", "start": future}],
    }
    bh = MagicMock()
    engine = FraudSignalEngine(bullhorn_service=bh)
    result = engine.assess(candidate, log)

    assert result.risk_band == 'high_risk'
    assert result.note_created is False
    bh.create_candidate_note.assert_not_called()


def test_engine_no_note_for_review_when_all_bands_off(_fraud_db):
    """Review band must NOT write a note while the all-bands toggle is OFF."""
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    from fraud_detection.engine import FraudSignalEngine

    _set_config(db, VettingConfig,
                fraud_detection_enabled='true',
                fraud_bullhorn_note_enabled='true',
                fraud_note_all_bands_enabled='false')

    log = VettingLog(bullhorn_candidate_id=9401, candidate_name="Review Only",
                     candidate_email="r@mailinator.com", status="processing")
    db.session.add(log)
    db.session.commit()

    # disposable(25) + placeholder phone(15) = 40 → review (not high-risk)
    candidate = {"id": 9401, "firstName": "Jane", "lastName": "Roe",
                 "email": "r@mailinator.com", "phone": "1111111111"}
    bh = MagicMock()
    result = FraudSignalEngine(bullhorn_service=bh).assess(candidate, log)

    assert result.risk_band == 'review'
    assert result.note_created is False
    bh.create_candidate_note.assert_not_called()


def test_engine_writes_review_note_when_all_bands_on(_fraud_db):
    """With the all-bands toggle ON, a Review candidate gets a vendor-neutral note."""
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    from fraud_detection.engine import FraudSignalEngine

    _set_config(db, VettingConfig,
                fraud_detection_enabled='true',
                fraud_bullhorn_note_enabled='true',
                fraud_note_all_bands_enabled='true')

    log = VettingLog(bullhorn_candidate_id=9402, candidate_name="Review Note",
                     candidate_email="r2@mailinator.com", status="processing")
    db.session.add(log)
    db.session.commit()

    candidate = {"id": 9402, "firstName": "Jane", "lastName": "Roe",
                 "email": "r2@mailinator.com", "phone": "1111111111"}
    bh = MagicMock()
    bh.create_candidate_note.return_value = 66602
    result = FraudSignalEngine(bullhorn_service=bh).assess(candidate, log)

    assert result.risk_band == 'review'
    assert result.note_created is True
    assert result.bullhorn_note_id == 66602
    bh.create_candidate_note.assert_called_once()
    _, kwargs = bh.create_candidate_note.call_args
    assert kwargs.get('action') == 'Candidate Risk Review'


def test_engine_writes_clear_note_when_all_bands_on(_fraud_db):
    """With the all-bands toggle ON, even a Clear candidate gets a note."""
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    from fraud_detection.engine import FraudSignalEngine

    _set_config(db, VettingConfig,
                fraud_detection_enabled='true',
                fraud_bullhorn_note_enabled='true',
                fraud_note_all_bands_enabled='true')

    log = VettingLog(bullhorn_candidate_id=9403, candidate_name="Clean Person",
                     candidate_email="clean@company.com", status="processing")
    db.session.add(log)
    db.session.commit()

    candidate = {"id": 9403, "firstName": "Clean", "lastName": "Person",
                 "email": "clean@company.com", "phone": "4165551234"}
    bh = MagicMock()
    bh.create_candidate_note.return_value = 66603
    result = FraudSignalEngine(bullhorn_service=bh).assess(candidate, log)

    assert result.risk_band == 'clear'
    assert result.note_created is True
    bh.create_candidate_note.assert_called_once()


def test_engine_all_bands_requires_note_enabled(_fraud_db):
    """all-bands ON but the master note toggle OFF → still no note."""
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    from fraud_detection.engine import FraudSignalEngine

    _set_config(db, VettingConfig,
                fraud_detection_enabled='true',
                fraud_bullhorn_note_enabled='false',
                fraud_note_all_bands_enabled='true')

    log = VettingLog(bullhorn_candidate_id=9404, candidate_name="No Note",
                     candidate_email="n@mailinator.com", status="processing")
    db.session.add(log)
    db.session.commit()

    candidate = {"id": 9404, "firstName": "Jane", "lastName": "Roe",
                 "email": "n@mailinator.com", "phone": "1111111111"}
    bh = MagicMock()
    result = FraudSignalEngine(bullhorn_service=bh).assess(candidate, log)

    assert result.note_created is False
    bh.create_candidate_note.assert_not_called()


def test_build_note_text_is_band_aware(_fraud_db):
    """Note body wording reflects the band; Clear states no indicators."""
    from fraud_detection.engine import FraudSignalEngine
    from fraud_detection import signals as fsig

    sig = fsig.FraudSignal(code="disposable_email",
                           label="Disposable email domain",
                           points=25, evidence="mailinator.com")

    clear = fsig.FraudAssessmentResult(
        risk_score=0, risk_band=fsig.FraudRiskBand.CLEAR, signals=[])
    ctxt = FraudSignalEngine._build_note_text(clear)
    assert 'Clear' in ctxt
    assert 'No risk indicators were detected' in ctxt
    assert 'advisory' in ctxt.lower()

    review = fsig.FraudAssessmentResult(
        risk_score=55, risk_band=fsig.FraudRiskBand.REVIEW, signals=[sig])
    rtxt = FraudSignalEngine._build_note_text(review)
    assert 'REVIEW' in rtxt
    assert 'Disposable email domain' in rtxt

    high = fsig.FraudAssessmentResult(
        risk_score=85, risk_band=fsig.FraudRiskBand.HIGH_RISK, signals=[sig])
    htxt = FraudSignalEngine._build_note_text(high)
    assert 'HIGH RISK' in htxt
    assert 'Disposable email domain' in htxt


def test_engine_resume_reuse_across_identities(_fraud_db):
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    from fraud_detection.engine import FraudSignalEngine

    _set_config(db, VettingConfig, fraud_detection_enabled='true')

    shared_resume = "EXPERIENCED SOFTWARE ENGINEER " * 20  # > 200 chars
    # Two prior logs from different candidate IDs with identical resume text.
    for cid, nm in ((7001, "Alice Prior"), (7002, "Bob Prior")):
        db.session.add(VettingLog(
            bullhorn_candidate_id=cid, candidate_name=nm,
            candidate_email="prior@x.com", status="completed",
            resume_text=shared_resume))
    db.session.commit()

    log = VettingLog(bullhorn_candidate_id=7003, candidate_name="Carol Latest",
                     candidate_email="carol@x.com", status="processing",
                     resume_text=shared_resume)
    db.session.add(log)
    db.session.commit()

    candidate = {"id": 7003, "firstName": "Carol", "lastName": "Latest",
                 "email": "carol@x.com"}
    engine = FraudSignalEngine()
    result = engine.assess(candidate, log)
    codes = {s['code'] for s in json.loads(result.signals_json)}
    assert 'resume_reuse' in codes


def test_engine_resume_duplicate_record_not_scored(_fraud_db):
    """Same person on two candidate records (same name + email, identical résumé)
    must NOT score as fraud — only surface an informational 'consider merging'
    note. This is the Edmond Vartanian false-positive fix."""
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    from fraud_detection.engine import FraudSignalEngine

    _set_config(db, VettingConfig, fraud_detection_enabled='true')

    shared_resume = "EXPERIENCED SOFTWARE ENGINEER " * 20  # > 200 chars
    # A prior record for the SAME person (same name + same email).
    db.session.add(VettingLog(
        bullhorn_candidate_id=8001, candidate_name="Edmond Vartanian",
        candidate_email="edmondv1961@gmail.com", status="completed",
        resume_text=shared_resume))
    db.session.commit()

    log = VettingLog(bullhorn_candidate_id=8002, candidate_name="Edmond Vartanian",
                     candidate_email="edmondv1961@gmail.com", status="processing",
                     resume_text=shared_resume)
    db.session.add(log)
    db.session.commit()

    candidate = {"id": 8002, "firstName": "Edmond", "lastName": "Vartanian",
                 "email": "edmondv1961@gmail.com"}
    engine = FraudSignalEngine()
    result = engine.assess(candidate, log)

    signals = json.loads(result.signals_json)
    codes = {s['code'] for s in signals}
    # Not scored as fraud...
    assert 'resume_reuse' not in codes
    # ...but the informational merge hint IS surfaced (user wants this line).
    assert 'resume_duplicate_record' in codes
    dup = next(s for s in signals if s['code'] == 'resume_duplicate_record')
    assert dup['points'] == 0
    # The benign duplicate must not push the candidate into a Review band.
    assert result.risk_band == fsig.FraudRiskBand.CLEAR


def test_engine_resume_duplicate_coherent_row_selection(_fraud_db):
    """Duplicate suppression must read name+email from ONE coherent (most-recent)
    record per other candidate id — even when that candidate has older logs with
    different historical name/email values (guards against mixing fields)."""
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    from fraud_detection.engine import FraudSignalEngine

    _set_config(db, VettingConfig, fraud_detection_enabled='true')

    shared_resume = "SENIOR DATA ENGINEER PROFILE " * 20  # > 200 chars
    # Other candidate id 8101 has TWO logs: an OLD one with stale name/email and
    # a NEWER one that matches the current candidate exactly.
    db.session.add(VettingLog(
        bullhorn_candidate_id=8101, candidate_name="Stale Oldname",
        candidate_email="stale.old@gmail.com", status="completed",
        resume_text=shared_resume, created_at=datetime(2026, 1, 1, 9, 0, 0)))
    db.session.add(VettingLog(
        bullhorn_candidate_id=8101, candidate_name="Maria Gomez",
        candidate_email="maria.gomez@gmail.com", status="completed",
        resume_text=shared_resume, created_at=datetime(2026, 5, 20, 9, 0, 0)))
    db.session.commit()

    log = VettingLog(bullhorn_candidate_id=8102, candidate_name="Maria Gomez",
                     candidate_email="maria.gomez@gmail.com", status="processing",
                     resume_text=shared_resume, created_at=datetime(2026, 6, 1, 9, 0, 0))
    db.session.add(log)
    db.session.commit()

    candidate = {"id": 8102, "firstName": "Maria", "lastName": "Gomez",
                 "email": "maria.gomez@gmail.com"}
    engine = FraudSignalEngine()
    result = engine.assess(candidate, log)

    codes = {s['code'] for s in json.loads(result.signals_json)}
    # Most-recent row matches → duplicate suppression holds, not scored as fraud.
    assert 'resume_reuse' not in codes
    assert 'resume_duplicate_record' in codes
    assert result.risk_band == fsig.FraudRiskBand.CLEAR


def test_engine_phone_reuse_across_identities(_fraud_db):
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    from fraud_detection.engine import FraudSignalEngine

    _set_config(db, VettingConfig, fraud_detection_enabled='true')

    # Same normalized phone (digits-only) across several distinct names.
    shared_phone = "15551112222"
    for cid, nm in ((9101, "Alice One"), (9102, "Bob Two"),
                    (9103, "Carol Three")):
        db.session.add(VettingLog(
            bullhorn_candidate_id=cid, candidate_name=nm,
            candidate_email=f"u{cid}@x.com", candidate_phone=shared_phone,
            status="completed"))
    db.session.commit()

    log = VettingLog(bullhorn_candidate_id=9104, candidate_name="Dan Four",
                     candidate_email="dan@x.com", candidate_phone=shared_phone,
                     status="processing")
    db.session.add(log)
    db.session.commit()

    # Candidate dict carries the raw (formatted) phone; engine normalizes it.
    candidate = {"id": 9104, "firstName": "Dan", "lastName": "Four",
                 "email": "dan@x.com", "phone": "+1 (555) 111-2222"}
    engine = FraudSignalEngine()
    result = engine.assess(candidate, log)
    codes = {s['code'] for s in json.loads(result.signals_json)}
    assert 'identity_reuse_phone' in codes


def test_engine_short_phone_does_not_trigger_reuse(_fraud_db):
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    from fraud_detection.engine import FraudSignalEngine

    _set_config(db, VettingConfig, fraud_detection_enabled='true')

    # Sub-10-digit numbers are ignored to avoid junk over-matching.
    short_phone = "12345"
    for cid, nm in ((9201, "Eve A"), (9202, "Frank B"), (9203, "Gina C")):
        db.session.add(VettingLog(
            bullhorn_candidate_id=cid, candidate_name=nm,
            candidate_email=f"u{cid}@x.com", candidate_phone=short_phone,
            status="completed"))
    db.session.commit()

    log = VettingLog(bullhorn_candidate_id=9204, candidate_name="Hank D",
                     candidate_email="hank@x.com", candidate_phone=short_phone,
                     status="processing")
    db.session.add(log)
    db.session.commit()

    candidate = {"id": 9204, "firstName": "Hank", "lastName": "D",
                 "email": "hank@x.com", "phone": "12345"}
    engine = FraudSignalEngine()
    result = engine.assess(candidate, log)
    codes = {s['code'] for s in json.loads(result.signals_json)}
    assert 'identity_reuse_phone' not in codes


def test_engine_clean_candidate_is_clear(_fraud_db):
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    from fraud_detection.engine import FraudSignalEngine

    _set_config(db, VettingConfig, fraud_detection_enabled='true')

    log = VettingLog(bullhorn_candidate_id=8001, candidate_name="Jane Doe",
                     candidate_email="jane@company.com", status="processing")
    db.session.add(log)
    db.session.commit()

    candidate = {"id": 8001, "firstName": "Jane", "lastName": "Doe",
                 "email": "jane@company.com", "phone": "4165551234"}
    engine = FraudSignalEngine()
    result = engine.assess(candidate, log)
    assert result.risk_band == 'clear'
    assert result.risk_score == 0


def test_engine_failsoft_on_note_error(_fraud_db):
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    from fraud_detection.engine import FraudSignalEngine

    _set_config(db, VettingConfig,
                fraud_detection_enabled='true',
                fraud_bullhorn_note_enabled='true')

    log = VettingLog(bullhorn_candidate_id=9009, candidate_name="Err Case",
                     candidate_email="e@mailinator.com", status="processing")
    db.session.add(log)
    db.session.commit()

    future = (date.today() + timedelta(days=500)).isoformat()
    candidate = {
        "id": 9009, "firstName": "Go http://x.ru", "lastName": "",
        "email": "e@mailinator.com", "phone": "1111111111",
        "workHistory": [{"title": "Eng", "start": future}],
    }
    bh = MagicMock()
    bh.create_candidate_note.side_effect = RuntimeError("bullhorn down")
    engine = FraudSignalEngine(bullhorn_service=bh)
    # Must not raise — the assessment row should still persist.
    result = engine.assess(candidate, log)
    assert result is not None
    assert result.risk_band == 'high_risk'
    assert result.note_created is False


def test_engine_db_error_does_not_rollback_caller_session(_fraud_db):
    """A fraud-path DB error must NEVER roll back the caller's shared session.

    The engine does all DB work in ISOLATED sessions and contains no
    ``db.session.rollback()`` calls, so even when a fraud query blows up the
    caller's vetting transaction (db.session) is never disturbed. This is the
    core guarantee behind the "advisory only / never blocks screening" contract.
    """
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    from fraud_detection.engine import FraudSignalEngine

    _set_config(db, VettingConfig, fraud_detection_enabled='true')

    log = VettingLog(bullhorn_candidate_id=9100, candidate_name="Tx Guard",
                     candidate_email="tx@company.com", status="processing")
    db.session.add(log)
    db.session.commit()

    engine = FraudSignalEngine()
    # Force a fraud DB read to explode, and spy on the SHARED session's rollback.
    with patch.object(engine, '_count_resume_reuse', side_effect=RuntimeError("boom")):
        with patch.object(db.session, 'rollback',
                          wraps=db.session.rollback) as spy_rollback:
            # assess() must not raise...
            result = engine.assess({"id": 9100, "email": "tx@company.com"}, log)
            # ...and must NEVER roll back the caller's shared session.
            spy_rollback.assert_not_called()

    # A degraded assessment still persisted (in its own isolated session).
    assert result is not None


def test_badge_latest_clear_supersedes_old_high_risk(_fraud_db):
    """Newest assessment wins: a later 'clear' hides an older 'high_risk'."""
    db, Assessment, VettingLog, VettingConfig = _fraud_db

    old = Assessment(
        bullhorn_candidate_id=9200, candidate_name="Flipper",
        risk_score=90, risk_band='high_risk', signals_json='[]',
        trigger='screening', note_created=False,
        created_at=datetime(2026, 5, 1, 10, 0, 0))
    new = Assessment(
        bullhorn_candidate_id=9200, candidate_name="Flipper",
        risk_score=10, risk_band='clear', signals_json='[]',
        trigger='screening', note_created=False,
        created_at=datetime(2026, 5, 28, 10, 0, 0))
    db.session.add_all([old, new])
    db.session.commit()

    # Mirror the route's selection logic: latest-per-candidate, then band filter.
    rows = (Assessment.query
            .filter(Assessment.bullhorn_candidate_id.in_([9200]))
            .order_by(Assessment.created_at.desc())
            .all())
    fraud_map = {}
    seen = set()
    for r in rows:
        if r.bullhorn_candidate_id in seen:
            continue
        seen.add(r.bullhorn_candidate_id)
        if r.risk_band and r.risk_band != 'clear':
            fraud_map[r.bullhorn_candidate_id] = {'band': r.risk_band, 'score': r.risk_score}

    # Latest is 'clear' → no badge should surface.
    assert 9200 not in fraud_map


# ─────────────────────────────────────────────────────────────────────────────
# Recruiter-email fraud banner (NotificationMixin._build_fraud_banner_html)
# ─────────────────────────────────────────────────────────────────────────────

def _make_assessment(db, Assessment, candidate_id, band, score, signals):
    row = Assessment(
        bullhorn_candidate_id=candidate_id,
        candidate_name="Test Cand",
        risk_band=band,
        risk_score=score,
        signals_json=json.dumps(signals),
        trigger="screening",
    )
    db.session.add(row)
    db.session.commit()
    return row


def _banner_service():
    from screening.notification import NotificationMixin
    return NotificationMixin()


def test_fraud_banner_high_risk_renders_band_score_reasons(_fraud_db):
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    _set_config(db, VettingConfig, fraud_detection_enabled='true')
    _make_assessment(db, Assessment, 9301, 'high_risk', 82, [
        {"code": "disposable_email", "label": "Disposable email domain",
         "points": 25, "evidence": "mailinator.com"},
        {"code": "identity_reuse", "label": "Email reused across identities",
         "points": 30, "evidence": "2 other names"},
        {"code": "noise", "label": "Zero-point signal", "points": 0},
    ])

    html = _banner_service()._build_fraud_banner_html(9301)

    assert 'High Fraud Risk' in html
    assert '82/100' in html
    # Reasons present, ordered highest-impact first (identity 30 before disposable 25).
    assert html.index('Email reused across identities') < html.index('Disposable email domain')
    # Zero-point signal is surfaced separately as informational — never scored.
    assert 'Informational (not scored)' in html
    assert 'Zero-point signal' in html
    # ...and it renders AFTER the scored reasons block.
    assert html.index('Disposable email domain') < html.index('Zero-point signal')
    # Advisory disclaimer present.
    assert 'Advisory only' in html


def test_fraud_banner_review_renders(_fraud_db):
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    _set_config(db, VettingConfig, fraud_detection_enabled='true')
    _make_assessment(db, Assessment, 9302, 'review', 55, [
        {"code": "phone_anomaly", "label": "Suspicious phone", "points": 15,
         "evidence": "1111111111"},
    ])
    html = _banner_service()._build_fraud_banner_html(9302)
    assert 'Review Recommended' in html
    assert '55/100' in html
    assert 'Suspicious phone' in html


def test_fraud_banner_clear_band_renders_checklist(_fraud_db):
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    _set_config(db, VettingConfig, fraud_detection_enabled='true')
    _make_assessment(db, Assessment, 9303, 'clear', 10, [])
    html = _banner_service()._build_fraud_banner_html(9303)
    # Green "passed" banner now renders for clear candidates.
    assert 'Integrity Check Passed' in html
    assert '10/100' in html
    # Clear candidates have no fired signals → static checks checklist instead.
    assert 'Work-history timeline' in html
    assert 'no risk indicators detected' in html.lower()


def test_fraud_banner_feature_disabled_empty(_fraud_db):
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    _set_config(db, VettingConfig, fraud_detection_enabled='false')
    _make_assessment(db, Assessment, 9304, 'high_risk', 90, [
        {"code": "x", "label": "y", "points": 10}])
    assert _banner_service()._build_fraud_banner_html(9304) == ''


def test_fraud_banner_no_assessment_empty(_fraud_db):
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    _set_config(db, VettingConfig, fraud_detection_enabled='true')
    assert _banner_service()._build_fraud_banner_html(9999) == ''


def test_fraud_banner_latest_wins(_fraud_db):
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    _set_config(db, VettingConfig, fraud_detection_enabled='true')
    # Older high-risk, newer clear → latest (clear) wins → green banner, not red.
    old = _make_assessment(db, Assessment, 9305, 'high_risk', 90, [
        {"code": "x", "label": "y", "points": 10}])
    old.created_at = datetime(2026, 1, 1)
    db.session.commit()
    _make_assessment(db, Assessment, 9305, 'clear', 5, [])
    html = _banner_service()._build_fraud_banner_html(9305)
    assert 'Integrity Check Passed' in html
    assert 'High Fraud Risk' not in html


def test_fraud_banner_escapes_evidence(_fraud_db):
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    _set_config(db, VettingConfig, fraud_detection_enabled='true')
    _make_assessment(db, Assessment, 9306, 'review', 50, [
        {"code": "x", "label": "Resume reuse",
         "points": 20, "evidence": "<script>alert(1)</script>"}])
    html = _banner_service()._build_fraud_banner_html(9306)
    assert '<script>' not in html
    assert '&lt;script&gt;' in html


def test_fraud_banner_tiebreak_by_id_when_same_created_at(_fraud_db):
    """Equal created_at → highest id wins (matches dashboard ordering)."""
    db, Assessment, VettingLog, VettingConfig = _fraud_db
    _set_config(db, VettingConfig, fraud_detection_enabled='true')
    ts = datetime(2026, 5, 30, 12, 0, 0)
    a = _make_assessment(db, Assessment, 9307, 'high_risk', 90, [
        {"code": "x", "label": "Older signal", "points": 10}])
    b = _make_assessment(db, Assessment, 9307, 'review', 50, [
        {"code": "y", "label": "Newer signal", "points": 20}])
    a.created_at = ts
    b.created_at = ts
    db.session.commit()
    assert b.id > a.id
    html = _banner_service()._build_fraud_banner_html(9307)
    # Higher id (b, review) wins the tie.
    assert 'Review Recommended' in html
    assert 'Newer signal' in html
    assert 'High Fraud Risk' not in html
