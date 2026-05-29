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
