"""Unit tests for Near Miss / Validate tier detection."""
from types import SimpleNamespace

from screening.near_miss import NEAR_MISS_BAND_POINTS, is_near_miss_match
from screening.note_builder import (
    classify_scout_note_action,
    intended_scout_note_outcome,
    _NOTE_OUTCOME_NEAR_MISS,
    _NOTE_OUTCOME_NOT_QUALIFIED,
    _NOTE_OUTCOME_LOCATION_REVIEW,
)


def _match(**kwargs):
    defaults = dict(
        is_qualified=False,
        match_score=76.0,
        technical_score=76.0,
        gaps_identified='',
        match_summary='Close fit',
        bullhorn_job_id=1,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestIsNearMissMatch:
    def test_within_band_under_threshold(self):
        assert is_near_miss_match(_match(match_score=76), threshold=80) is True
        assert is_near_miss_match(_match(match_score=75), threshold=80) is True
        assert is_near_miss_match(_match(match_score=79.9), threshold=80) is True

    def test_at_or_above_threshold_not_near_miss(self):
        assert is_near_miss_match(_match(match_score=80), threshold=80) is False
        assert is_near_miss_match(_match(match_score=85), threshold=80) is False

    def test_below_band_not_near_miss(self):
        assert is_near_miss_match(_match(match_score=74.9), threshold=80) is False
        assert is_near_miss_match(_match(match_score=69), threshold=80) is False

    def test_tracks_lower_threshold(self):
        # Global/job at 75% → band is 70–75
        assert is_near_miss_match(_match(match_score=72), threshold=75) is True
        assert is_near_miss_match(_match(match_score=70), threshold=75) is True
        assert is_near_miss_match(_match(match_score=69.9), threshold=75) is False
        assert is_near_miss_match(_match(match_score=75), threshold=75) is False

    def test_qualified_excluded(self):
        assert is_near_miss_match(
            _match(is_qualified=True, match_score=78), threshold=80
        ) is False

    def test_missing_score(self):
        assert is_near_miss_match(_match(match_score=None), threshold=80) is False

    def test_band_constant(self):
        assert NEAR_MISS_BAND_POINTS == 5


class TestNearMissNoteOutcome:
    def test_classify_near_miss_action(self):
        assert classify_scout_note_action('Scout Screen - Near Miss') == _NOTE_OUTCOME_NEAR_MISS

    def test_intended_near_miss(self):
        matches = [_match(match_score=78, is_qualified=False)]
        assert intended_scout_note_outcome(matches, global_threshold=80) == _NOTE_OUTCOME_NEAR_MISS

    def test_intended_not_qualified_below_band(self):
        matches = [_match(match_score=60, is_qualified=False)]
        assert intended_scout_note_outcome(matches, global_threshold=80) == _NOTE_OUTCOME_NOT_QUALIFIED

    def test_location_review_beats_near_miss(self):
        # Tech above threshold, small location penalty → location review wins
        matches = [
            _match(
                match_score=78,
                technical_score=85,
                gaps_identified='Location: candidate is 40 miles from office',
                is_qualified=False,
            )
        ]
        assert (
            intended_scout_note_outcome(matches, global_threshold=80)
            == _NOTE_OUTCOME_LOCATION_REVIEW
        )

    def test_per_job_threshold_shifts_band(self):
        matches = [_match(match_score=72, bullhorn_job_id=99, is_qualified=False)]
        assert (
            intended_scout_note_outcome(
                matches,
                job_threshold_map={99: 75.0},
                global_threshold=80.0,
            )
            == _NOTE_OUTCOME_NEAR_MISS
        )
        assert (
            intended_scout_note_outcome(
                matches,
                job_threshold_map={},
                global_threshold=80.0,
            )
            == _NOTE_OUTCOME_NOT_QUALIFIED
        )
