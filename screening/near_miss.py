from __future__ import annotations
"""
Near Miss / Validate tier - candidates just under the match threshold.

These are not auto-Qualified. They are surfaced for recruiter judgment when
the final match score sits within a small band below the effective threshold
(per-job override when set, otherwise global).

Example (threshold 80, band 5): scores in [75, 80) → Near Miss.
Example (threshold 75, band 5): scores in [70, 75) → Near Miss.

Used by:
  - screening/note_builder.py  (writes "NEAR MISS — VALIDATE" Bullhorn note)
  - screening/notification.py  (sends a distinct recruiter email)
"""

from screening.location_review import resolve_match_threshold

# Points below the effective threshold that still warrant a Validate alert.
# Hard-coded for v1; promote to VettingConfig if recruiters want to tune it.
NEAR_MISS_BAND_POINTS = 5


def is_near_miss_match(match, threshold: float, band: float = NEAR_MISS_BAND_POINTS) -> bool:
    """
    Return True if a CandidateJobMatch is a near-miss for recruiter validation.

    Requires:
      - match is not already qualified
      - match_score is set
      - (threshold - band) <= match_score < threshold
    """
    if match is None:
        return False
    if getattr(match, 'is_qualified', False):
        return False
    if band is None or band <= 0:
        return False

    score = getattr(match, 'match_score', None)
    if score is None:
        return False

    try:
        score_f = float(score)
        threshold_f = float(threshold)
        band_f = float(band)
    except (TypeError, ValueError):
        return False

    floor = threshold_f - band_f
    return floor <= score_f < threshold_f


__all__ = [
    'NEAR_MISS_BAND_POINTS',
    'is_near_miss_match',
    'resolve_match_threshold',
]
