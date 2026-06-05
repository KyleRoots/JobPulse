"""Regression tests for mini-induced null/prose numeric fields outside the
years gate.

gpt-4.1-mini (cheap-first router first-pass) sometimes emits an explicit JSON
null (or prose) for fields where gpt-5.4 always sent a number. Because
``dict.get(key, default)`` returns ``None`` when the key is PRESENT but null
(the default only applies to a MISSING key), code like
``recency_analysis.get('months_since_relevant_work', 0)`` returned ``None`` and
then ``months_since > 24`` crashed with
``'>' not supported between instances of 'NoneType' and 'int'`` — zeroing the
whole analysis. Same exposure in ``coerce_scores`` via ``int(None)``.

These lock in graceful coercion for the recency hard gate and the score coercer.
"""

from screening.post_processing import coerce_scores, enforce_recency_hard_gate


# ---- coerce_scores -------------------------------------------------------

def test_coerce_scores_handles_null_match_score():
    result = {"match_score": None}
    coerce_scores(result, job_id=1)
    assert result["match_score"] == 0
    assert isinstance(result["match_score"], int)
    assert result["technical_score"] == 0


def test_coerce_scores_handles_prose_match_score():
    result = {"match_score": "Not explicitly stated"}
    coerce_scores(result, job_id=1)
    assert result["match_score"] == 0


def test_coerce_scores_handles_null_technical_score():
    result = {"match_score": 60, "technical_score": None}
    coerce_scores(result, job_id=1)
    assert result["match_score"] == 60
    # Null technical_score falls back to match_score.
    assert result["technical_score"] == 60


def test_coerce_scores_handles_prose_technical_score():
    result = {"match_score": 55, "technical_score": "high"}
    coerce_scores(result, job_id=1)
    assert result["match_score"] == 55
    # Unparseable technical_score falls back to match_score (the _safe_float default).
    assert result["technical_score"] == 55


def test_coerce_scores_normal_numeric_strings():
    result = {"match_score": "82", "technical_score": "70"}
    coerce_scores(result, job_id=1)
    assert result["match_score"] == 82
    assert result["technical_score"] == 70


# ---- enforce_recency_hard_gate ------------------------------------------

def test_recency_gate_survives_null_months_since():
    """Null months_since_relevant_work must not crash the '>' comparison."""
    result = {
        "match_score": 80,
        "recency_analysis": {
            "most_recent_role_relevant": False,
            "second_recent_role_relevant": True,
            "months_since_relevant_work": None,
            "penalty_applied": None,
            "relevance_justification": "Candidate has not worked in this domain recently at all.",
        },
    }
    enforce_recency_hard_gate(result, job_id=1)
    # months_since coerces to 0 → < 12 → no penalty, but crucially no crash.
    assert isinstance(result["match_score"], int)


def test_recency_gate_survives_null_penalty_applied():
    """Null penalty_applied must not crash max(target, ai_penalty)."""
    result = {
        "match_score": 80,
        "recency_analysis": {
            "most_recent_role_relevant": False,
            "second_recent_role_relevant": False,
            "months_since_relevant_work": 30,
            "penalty_applied": None,
            "relevance_justification": "Trajectory shifted away from this domain in last two roles.",
        },
    }
    enforce_recency_hard_gate(result, job_id=1)
    # Both roles not relevant → target penalty 20; null ai_penalty coerces to 0.
    assert result["match_score"] == 60


def test_recency_gate_null_relevance_flag_defaults_to_relevant():
    """A null most_recent_role_relevant must NOT be treated as not-relevant
    (would manufacture a false penalty under enforce)."""
    result = {
        "match_score": 80,
        "recency_analysis": {
            "most_recent_role_relevant": None,
            "second_recent_role_relevant": None,
            "months_since_relevant_work": 40,
            "penalty_applied": 0,
            "relevance_justification": "Currently a Senior Data Engineer building Azure pipelines daily.",
        },
    }
    enforce_recency_hard_gate(result, job_id=1)
    # Null relevance → benefit of the doubt (relevant) → no recency penalty.
    assert result["match_score"] == 80
