"""Per-job vetting thresholds vs the global 80% baseline."""
from types import SimpleNamespace

from screening.job_threshold import (
    coerce_job_id,
    custom_job_threshold,
    resolve_job_threshold,
    score_clears_job_threshold,
    threshold_map_from_requirements,
)
from screening.location_review import resolve_match_threshold


def test_coerce_job_id_int_and_str():
    assert coerce_job_id(35135) == 35135
    assert coerce_job_id('35135') == 35135
    assert coerce_job_id(None) is None
    assert coerce_job_id('Network Engineer') is None


def test_string_cache_key_still_resolves():
    mapping = {'35135': 60.0}
    assert resolve_job_threshold(mapping, 35135, 80.0) == 60.0
    assert resolve_job_threshold(mapping, '35135', 80.0) == 60.0


def test_missing_custom_falls_back_to_global():
    assert resolve_job_threshold({35135: 60.0}, 99999, 80.0) == 80.0
    assert resolve_job_threshold({}, 35135, 80.0) == 80.0


def test_score_meets_custom_floor_at_equality():
    assert score_clears_job_threshold(60, 60) is True
    assert score_clears_job_threshold(59.9, 60) is False
    assert score_clears_job_threshold(85, 90) is False
    assert score_clears_job_threshold(90, 90) is True
    assert score_clears_job_threshold(81, 80) is True


def test_threshold_map_from_requirements_coerces_ids():
    rows = [
        SimpleNamespace(bullhorn_job_id='35135', vetting_threshold=60),
        SimpleNamespace(bullhorn_job_id=36000, vetting_threshold=90),
        SimpleNamespace(bullhorn_job_id=1, vetting_threshold=None),
    ]
    mapping = threshold_map_from_requirements(rows)
    assert mapping[35135] == 60.0
    assert mapping[36000] == 90.0
    assert 1 not in mapping


def test_custom_job_threshold_none_when_global():
    assert custom_job_threshold({35135: 60.0}, 999) is None
    assert custom_job_threshold({35135: 60.0}, '35135') == 60.0


def test_resolve_match_threshold_accepts_string_job_id():
    match = SimpleNamespace(bullhorn_job_id='35135')
    assert resolve_match_threshold(match, {35135: 60.0}, 80.0) == 60.0
    assert resolve_match_threshold(match, {}, 80.0) == 80.0
