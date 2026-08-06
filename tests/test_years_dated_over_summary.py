"""Years-of-experience: dated work history over résumé summary claims.

Nirav Patel regression: résumé summary said "3+ years shipping production AI"
while the only dated AI role was ~16 months. Scout must score from dated tenure
and must not write "resume explicitly shows 3+ years…" as if that were proof.
"""

from screening.post_processing import (
    enforce_years_hard_gate,
    sanitize_years_claim_language,
)
from screening.system_prompt import build_system_message
from screening.compliance import SCREENING_RULES_VERSION


def _nirav_like_years_analysis():
    """Dated AI role ~16 months (~1.3yr) vs 3yr AI requirement; summary claims 3+."""
    return {
        "AI": {
            "required_years": 3,
            "estimated_years": 1.3,
            "meets_requirement": False,
            "calculation": (
                "Summary claims 3+yr shipping production AI; "
                "dated relevant roles = 1.3yr (AI Engineer Apr 2025–Present ≈ 16 mo). "
                "Claim not used for estimated_years."
            ),
        }
    }


def test_rules_version_bumped_for_dated_tenure():
    assert SCREENING_RULES_VERSION == '2026.08.06'


def test_system_prompt_requires_dated_tenure_over_summary_claims():
    prompt = build_system_message(global_reqs_section='', related_job_brief=False)
    assert 'DATED WORK HISTORY IS AUTHORITATIVE' in prompt
    assert 'CLAIMS, not proof' in prompt
    assert 'summary_claim' in prompt
    assert 'resume explicitly shows' in prompt
    assert 'Domain scope' in prompt


def test_nirav_like_shortfall_applies_years_penalty():
    """Dated ~1.3yr vs 3yr → 1.7yr shortfall → −15 pts (1–2yr band), not summary meet."""
    result = {
        "match_score": 82,
        "match_summary": (
            "Strong AI match — resume explicitly shows 3+ years of production AI experience."
        ),
        "experience_match": "Resume explicitly shows 3+ years shipping production AI.",
        "gaps_identified": "",
        "years_analysis": _nirav_like_years_analysis(),
    }

    def _recheck_fn(*_a, **_k):
        return None

    enforce_years_hard_gate(result, 99001, "AI Engineer", "resume text", _recheck_fn)

    assert result["match_score"] == 67  # 82 − 15 (1–2yr shortfall band)
    assert "CRITICAL: AI requires 3yr, candidate has ~1.3yr" in result["gaps_identified"]
    assert "explicitly shows" not in result["match_summary"].lower()
    assert "dated roles show ~1.3yr" in result["match_summary"].lower() or (
        "dated ai roles total ~1.3yr" in result["match_summary"].lower()
    )


def test_meets_requirement_true_cannot_bypass_dated_shortfall():
    """If the model marks meets=true but estimated_years is still low, force the gate."""
    result = {
        "match_score": 88,
        "match_summary": "Meets years bar.",
        "experience_match": "3+ years AI per summary.",
        "gaps_identified": "",
        "years_analysis": {
            "AI": {
                "required_years": 3,
                "estimated_years": 1.3,
                "meets_requirement": True,  # wrong — summary-driven
                "calculation": "Used summary claim of 3+ years",
            }
        },
    }

    def _recheck_fn(*_a, **_k):
        return None

    enforce_years_hard_gate(result, 99002, "AI Engineer", "resume", _recheck_fn)

    assert result["years_analysis"]["AI"]["meets_requirement"] is False
    assert result["match_score"] == 73  # 88 − 15
    assert "CRITICAL: AI requires 3yr" in result["gaps_identified"]


def test_large_dated_shortfall_still_caps_at_60():
    """≥2yr dated shortfall still hits the hard cap (unchanged band)."""
    result = {
        "match_score": 85,
        "match_summary": "Resume explicitly shows 5+ years of AI.",
        "gaps_identified": "",
        "years_analysis": {
            "AI": {
                "required_years": 5,
                "estimated_years": 1.3,
                "meets_requirement": False,
                "calculation": (
                    "Summary claims 5+yr; dated relevant roles = 1.3yr. Claim not used."
                ),
            }
        },
    }

    def _recheck_fn(*_a, **_k):
        return None

    enforce_years_hard_gate(result, 99003, "Senior AI", "resume", _recheck_fn)
    assert result["match_score"] == 60
    assert "explicitly shows" not in result["match_summary"].lower()


def test_sanitize_explicitly_shows_when_shortfall_present():
    result = {
        "match_summary": "The resume explicitly shows 3+ years of AI delivery.",
        "experience_match": "CV explicitly shows 3 years of ML.",
        "gaps_identified": "None",
        "years_analysis": _nirav_like_years_analysis(),
    }
    sanitize_years_claim_language(result, job_id=42)
    assert "explicitly shows" not in result["match_summary"].lower()
    assert "1.3" in result["match_summary"]
    assert "explicitly shows" not in result["experience_match"].lower()


def test_sanitize_noop_when_dated_years_meet_requirement():
    result = {
        "match_summary": "The resume explicitly shows 5+ years of Python.",
        "years_analysis": {
            "Python": {
                "required_years": 3,
                "estimated_years": 5.0,
                "meets_requirement": True,
                "calculation": "Jan 2019–Jan 2024 = 60 mo = 5.0yr",
            }
        },
    }
    sanitize_years_claim_language(result, job_id=43)
    assert "explicitly shows 5+ years" in result["match_summary"].lower()
