"""Regression tests for mini-induced non-numeric years values.

gpt-4.1-mini (cheap-first router first-pass) sometimes returns the
years-of-experience fields as prose (e.g. "Not explicitly stated but
strong hands-on required") or null where gpt-5.4 always returned a
number. The post-processing math used to call float()/'>' directly on
those fields, crashing the whole analysis ("AI analysis error ...") and
returning match_score=0. These tests lock in graceful coercion plus the
"never manufacture a shortfall from an unparseable value" contract.
"""

from screening.post_processing import (
    _safe_float,
    _compute_shortfalls,
    enforce_years_hard_gate,
    enforce_experience_floor,
)


# ---- _safe_float ---------------------------------------------------------

def test_safe_float_passes_through_numbers():
    assert _safe_float(5) == 5.0
    assert _safe_float(5.5) == 5.5
    assert _safe_float(0) == 0.0


def test_safe_float_parses_numeric_strings():
    assert _safe_float("7") == 7.0
    assert _safe_float(" 3.5 ") == 3.5


def test_safe_float_returns_default_for_prose():
    assert _safe_float("Not explicitly stated but strong hands-on required") == 0.0
    assert _safe_float("Not explicitly stated", default=None) is None


def test_safe_float_returns_default_for_none():
    assert _safe_float(None) == 0.0
    assert _safe_float(None, default=None) is None


def test_safe_float_rejects_bool():
    assert _safe_float(True, default=2.0) == 2.0


# ---- _compute_shortfalls (the hot-path crash site) -----------------------

def test_compute_shortfalls_handles_prose_estimated():
    """Prose estimated_years must not crash and must NOT manufacture a shortfall."""
    ya = {
        "Python": {
            "meets_requirement": False,
            "required_years": 5,
            "estimated_years": "Not explicitly stated but strong hands-on required",
        }
    }
    max_shortfall, details = _compute_shortfalls(ya, job_id=1)
    assert max_shortfall == 0.0
    assert details == []


def test_compute_shortfalls_handles_none_required():
    ya = {
        "Python": {
            "meets_requirement": False,
            "required_years": None,
            "estimated_years": 2,
        }
    }
    max_shortfall, details = _compute_shortfalls(ya, job_id=1)
    assert max_shortfall == 0.0
    assert details == []


def test_compute_shortfalls_real_shortfall_preserved():
    """A genuine 0-years estimate is a real shortfall, not an unparseable skip."""
    ya = {
        "Python": {
            "meets_requirement": False,
            "required_years": 5,
            "estimated_years": 0,
        }
    }
    max_shortfall, details = _compute_shortfalls(ya, job_id=1)
    assert max_shortfall == 5.0
    assert len(details) == 1


def test_compute_shortfalls_normal_numeric():
    ya = {
        "Python": {
            "meets_requirement": False,
            "required_years": "5",
            "estimated_years": "2",
        }
    }
    max_shortfall, details = _compute_shortfalls(ya, job_id=1)
    assert max_shortfall == 3.0
    assert len(details) == 1


# ---- enforce_years_hard_gate (end-to-end, mini-style payload) ------------

def test_hard_gate_survives_mini_prose():
    result = {
        "match_score": 82,
        "years_analysis": {
            "Java": {
                "meets_requirement": False,
                "required_years": 5,
                "estimated_years": "Not explicitly stated",
            }
        },
    }

    def _recheck_fn(*_args, **_kwargs):
        return None

    enforce_years_hard_gate(result, 1, "Engineer", "resume text", _recheck_fn)
    # No crash, and an unparseable estimate must not drag a qualifying score down.
    assert result["match_score"] == 82


# ---- enforce_experience_floor (intern-override crash site) ---------------

def test_experience_floor_survives_mini_prose():
    result = {
        "match_score": 70,
        "experience_level_classification": {
            "classification": "FRESH_GRAD",
            "highest_role_type": "INTERNSHIP_ONLY",
            "total_professional_years": "Not explicitly stated",
        },
        "years_analysis": {
            "Java": {
                "meets_requirement": True,
                "required_years": 5,
                "estimated_years": "Not explicitly stated",
            }
        },
        "gaps_identified": "",
    }
    # Must not raise despite prose in both total_professional_years and years_analysis.
    enforce_experience_floor(result, 1, "", "")
    assert isinstance(result["match_score"], int)
