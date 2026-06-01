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
    assert fsig.evaluate_resume_reuse(0) is None
    sig = fsig.evaluate_resume_reuse(2)
    assert sig is not None and sig.points == fsig.POINTS_RESUME_REUSE


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
        fsig.evaluate_resume_reuse(1),                  # 40
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
    # Zero-point signal suppressed.
    assert 'Zero-point signal' not in html
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
