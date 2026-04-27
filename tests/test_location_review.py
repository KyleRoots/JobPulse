"""
Unit tests for the LOCATION REVIEW tier detection helper.

Covers the conditions under which a not-qualified candidate-job match should
be surfaced to the recruiter for a location-related judgment call rather than
silently rejected.
"""
from types import SimpleNamespace

from screening.location_review import (
    LOCATION_NEAR_MISS_PENALTY_CAP,
    is_location_review_match,
    resolve_match_threshold,
)


def _make_match(
    *,
    is_qualified=False,
    technical_score=None,
    match_score=None,
    gaps_identified=None,
    bullhorn_job_id=None,
):
    return SimpleNamespace(
        is_qualified=is_qualified,
        technical_score=technical_score,
        match_score=match_score,
        gaps_identified=gaps_identified,
        bullhorn_job_id=bullhorn_job_id,
    )


# ── PATH A: small soft location penalty ──────────────────────────────────────


def test_lorraine_canonical_case_qualifies():
    """Tech 82%, final 77%, threshold 80%, 5pt location penalty — the case the
    user surfaced. Should be flagged for recruiter Location Review."""
    match = _make_match(
        technical_score=82,
        match_score=77,
        gaps_identified="Talent assessment evidence is partial. Location: candidate "
                        "in Mississauga, ~30 km from Toronto, within commuting range.",
    )
    assert is_location_review_match(match, threshold=80) is True


def test_exactly_at_threshold_qualifies():
    """Tech score equal to threshold should still trigger (boundary inclusion)."""
    match = _make_match(
        technical_score=80,
        match_score=75,
        gaps_identified="Location penalty applied: candidate is 25 km from job site.",
    )
    assert is_location_review_match(match, threshold=80) is True


def test_penalty_at_cap_qualifies():
    """A penalty exactly at the cap should still qualify."""
    match = _make_match(
        technical_score=85,
        match_score=85 - LOCATION_NEAR_MISS_PENALTY_CAP,
        gaps_identified="Location adjustment for relocation.",
    )
    assert is_location_review_match(match, threshold=80) is True


def test_penalty_exceeds_cap_disqualifies():
    """Tech 82, final 65 (penalty 17) is too large a location gap — treat as
    a true Not-Recommended, not a near-miss."""
    match = _make_match(
        technical_score=82,
        match_score=65,
        gaps_identified="Location: candidate in Vancouver, job is on-site Toronto.",
    )
    assert is_location_review_match(match, threshold=80) is False


def test_below_technical_threshold_disqualifies():
    """Tech score below threshold should NOT qualify even with a tiny penalty —
    the candidate isn't technically qualified to begin with."""
    match = _make_match(
        technical_score=75,
        match_score=72,
        gaps_identified="Some skill gaps. Location: short commute.",
    )
    assert is_location_review_match(match, threshold=80) is False


def test_no_penalty_disqualifies():
    """If technical_score == match_score, there's no penalty to review."""
    match = _make_match(
        technical_score=85,
        match_score=85,
        gaps_identified="Some location notes but no penalty.",
    )
    assert is_location_review_match(match, threshold=80) is False


def test_no_location_in_gaps_disqualifies():
    """Tech-qualified + small gap but no location signal — should NOT trigger
    the location review tier (could be some other adjustment)."""
    match = _make_match(
        technical_score=82,
        match_score=77,
        gaps_identified="Missing one nice-to-have skill: Snowflake.",
    )
    assert is_location_review_match(match, threshold=80) is False


def test_missing_technical_score_disqualifies():
    """Older records without technical_score can't be evaluated for path A."""
    match = _make_match(
        technical_score=None,
        match_score=77,
        gaps_identified="Location penalty applied.",
    )
    assert is_location_review_match(match, threshold=80) is False


def test_already_qualified_returns_false():
    """If the candidate is_qualified=True, this path is irrelevant."""
    match = _make_match(
        is_qualified=True,
        technical_score=85,
        match_score=82,
        gaps_identified="Location penalty applied.",
    )
    assert is_location_review_match(match, threshold=80) is False


def test_none_match_returns_false():
    assert is_location_review_match(None, threshold=80) is False


# ── PATH B: legacy "location mismatch" hard barrier (preserved) ─────────────


def test_legacy_location_mismatch_within_buffer_qualifies():
    """AI flagged 'location mismatch' (hard barrier) and tech is within
    threshold-15 buffer — preserves the prior STRONG FIT / LOCATION BARRIER
    behavior so existing on-site/hybrid hard-barrier cases still surface."""
    match = _make_match(
        technical_score=70,
        match_score=45,
        gaps_identified="Location mismatch: candidate in NY, role is on-site Toronto.",
    )
    assert is_location_review_match(match, threshold=80) is True


def test_legacy_location_mismatch_outside_buffer_disqualifies():
    """If technical fit is more than 15 pts below threshold, the legacy path
    should NOT trigger — the candidate is genuinely not qualified."""
    match = _make_match(
        technical_score=50,
        match_score=25,
        gaps_identified="Location mismatch: different country.",
    )
    assert is_location_review_match(match, threshold=80) is False


def test_legacy_path_uses_match_score_when_tech_missing():
    """Legacy path falls back to match_score if technical_score is None."""
    match = _make_match(
        technical_score=None,
        match_score=68,
        gaps_identified="Location mismatch on hybrid role.",
    )
    assert is_location_review_match(match, threshold=80) is True


# ── Per-job threshold resolution ────────────────────────────────────────────


def test_resolve_threshold_returns_per_job_when_present():
    match = _make_match(bullhorn_job_id=12345)
    assert resolve_match_threshold(match, {12345: 75.0}, global_threshold=80) == 75.0


def test_resolve_threshold_falls_back_to_global_when_no_override():
    match = _make_match(bullhorn_job_id=99999)
    assert resolve_match_threshold(match, {12345: 75.0}, global_threshold=80) == 80


def test_resolve_threshold_falls_back_to_global_when_map_empty():
    match = _make_match(bullhorn_job_id=12345)
    assert resolve_match_threshold(match, {}, global_threshold=80) == 80
    assert resolve_match_threshold(match, None, global_threshold=80) == 80


def test_resolve_threshold_handles_missing_job_id():
    match = _make_match(bullhorn_job_id=None)
    assert resolve_match_threshold(match, {12345: 75.0}, global_threshold=80) == 80


def test_per_job_lower_threshold_qualifies_candidate_who_misses_global():
    """A candidate who misses the global 80% threshold may still hit a per-job
    custom 70% threshold — verifying location-review uses the right threshold."""
    match = _make_match(
        technical_score=72,
        match_score=68,
        gaps_identified="Location penalty: short commute applies.",
        bullhorn_job_id=12345,
    )
    per_job_threshold = resolve_match_threshold(match, {12345: 70.0}, global_threshold=80)
    assert is_location_review_match(match, per_job_threshold) is True
    assert is_location_review_match(match, 80) is False


# ── Sanity: the constant is what we documented to the user ──────────────────


def test_penalty_cap_constant_is_ten():
    assert LOCATION_NEAR_MISS_PENALTY_CAP == 10
