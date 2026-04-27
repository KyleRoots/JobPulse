from __future__ import annotations
"""
Location Review tier - shared logic for detecting candidates who are
technically qualified for a job but were knocked just below the match
threshold by a small location penalty.

These candidates should NOT be silently rejected. They are surfaced to
the recruiter as a judgment call: the underlying technical fit meets or
exceeds the threshold, and only a soft location deduction (e.g. a short
commute or near-by metro area) brought the final score down.

Used by:
  - screening/note_builder.py  (writes a "LOCATION REVIEW REQUIRED" Bullhorn note)
  - screening/notification.py  (sends a distinct recruiter email)
"""


# Maximum location penalty (in points) for a not-qualified candidate to
# enter the Location Review tier.
#
# If technical_score >= threshold but match_score falls below threshold
# by MORE than this many points, the candidate is treated as a genuine
# Not-Recommended result (the location gap is a hard barrier rather than
# a soft penalty worth a recruiter's judgment call).
#
# Hard-coded for v1. If recruiters want to tune this without a code
# change in the future, promote to a VettingConfig setting.
LOCATION_NEAR_MISS_PENALTY_CAP = 10


def resolve_match_threshold(match, job_threshold_map, global_threshold: float) -> float:
    """
    Return the per-job custom vetting threshold for a match if one is defined,
    otherwise return the global threshold.

    Used so that the Location Review tier evaluates each match against the
    same threshold that determined its is_qualified status — keeping the new
    notification path consistent with the per-job qualification logic in
    candidate_vetting_service.py.

    Args:
        match: a CandidateJobMatch (must have bullhorn_job_id attribute)
        job_threshold_map: dict mapping bullhorn_job_id -> custom threshold,
            or empty/None if no per-job thresholds are configured
        global_threshold: the global VettingConfig match_threshold to fall
            back to when no per-job override exists
    """
    if not job_threshold_map:
        return global_threshold
    job_id = getattr(match, 'bullhorn_job_id', None)
    if job_id is None:
        return global_threshold
    return job_threshold_map.get(job_id, global_threshold)


def is_location_review_match(match, threshold: float) -> bool:
    """
    Return True if a CandidateJobMatch qualifies for the Location Review tier.

    Two qualifying paths (either is sufficient):

    Path A - small soft location penalty (the new common case):
        - match.is_qualified is False
        - match.technical_score is set and >= threshold
        - 0 < (technical_score - match_score) <= LOCATION_NEAR_MISS_PENALTY_CAP
        - match.gaps_identified mentions "location"

    Path B - legacy AI-flagged hard barrier (preserved for backwards
    compatibility with the previous "STRONG FIT / LOCATION BARRIER" path):
        - match.is_qualified is False
        - match.gaps_identified contains the literal phrase "location mismatch"
        - technical_score (or match_score as a fallback) >= (threshold - 15)

    Args:
        match: a CandidateJobMatch instance (or any object with the
               attributes is_qualified, technical_score, match_score,
               gaps_identified)
        threshold: the qualifying match-score threshold (typically the
                   global VettingConfig match_threshold or a per-job override)

    Returns:
        True if the match should be flagged for recruiter Location Review.
    """
    if match is None:
        return False
    if getattr(match, 'is_qualified', False):
        return False

    tech = getattr(match, 'technical_score', None)
    final = getattr(match, 'match_score', None) or 0
    gaps_lower = (getattr(match, 'gaps_identified', '') or '').lower()

    # Path A: small soft location penalty knocked a tech-qualified candidate below threshold
    if tech is not None and tech >= threshold:
        penalty = tech - final
        if 0 < penalty <= LOCATION_NEAR_MISS_PENALTY_CAP and 'location' in gaps_lower:
            return True

    # Path B: legacy "location mismatch" literal-string path (AI-flagged hard barrier
    # that still leaves the candidate within striking distance of the threshold)
    if 'location mismatch' in gaps_lower:
        tech_or_final = tech if tech is not None else final
        if tech_or_final >= (threshold - 15):
            return True

    return False
