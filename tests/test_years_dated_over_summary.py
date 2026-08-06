"""Years-of-experience: dated work history over résumé summary claims.

Nirav Patel regression: résumé summary said "3+ years shipping production AI"
while the only dated AI role was ~16 months. Scout must score from dated tenure,
must not write "resume explicitly shows 3+ years…" as if that were proof, and
must not Qualify / email recruiters on a clear dated shortfall.
"""

from screening.post_processing import (
    YEARS_CLOSE_MAX_SHORTFALL_YEARS,
    YEARS_CLOSE_MIN_RATIO,
    apply_years_tenure_qualify_gate,
    classify_years_tenure,
    enforce_years_hard_gate,
    sanitize_years_claim_language,
    years_tenure_allows_qualify,
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
    assert SCREENING_RULES_VERSION == '2026.08.06b'


def test_close_band_thresholds_documented():
    assert YEARS_CLOSE_MAX_SHORTFALL_YEARS == 0.75
    assert YEARS_CLOSE_MIN_RATIO == 0.85


def test_classify_years_tenure_bands():
    assert classify_years_tenure(3, 3.0) == 'meets'
    assert classify_years_tenure(3, 3.5) == 'meets'
    # Close via shortfall ≤ 0.75yr
    assert classify_years_tenure(3, 2.4) == 'close'
    # Close via ≥85% of required (3 * 0.85 = 2.55)
    assert classify_years_tenure(3, 2.55) == 'close'
    # Nirav-like clear shortfall
    assert classify_years_tenure(3, 1.3) == 'clear_shortfall'
    assert classify_years_tenure(5, 1.3) == 'clear_shortfall'


def test_system_prompt_requires_dated_tenure_over_summary_claims():
    prompt = build_system_message(global_reqs_section='', related_job_brief=False)
    assert 'DATED WORK HISTORY IS AUTHORITATIVE' in prompt
    assert 'CLAIMS, not proof' in prompt
    assert 'summary_claim' in prompt
    assert 'resume explicitly shows' in prompt
    assert 'Domain scope' in prompt


def test_nirav_like_shortfall_applies_years_penalty_and_blocks_qualify():
    """Dated ~1.3yr vs 3yr → score penalty + blocks is_qualified path."""
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
    assert result["_years_tenure_blocks_qualify"] is True
    assert result["_years_tenure_status"] == "clear_shortfall"
    assert years_tenure_allows_qualify(result) is False


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
    assert years_tenure_allows_qualify(result) is False


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
    assert years_tenure_allows_qualify(result) is False


def test_meets_dated_years_allows_qualify():
    result = {
        "match_score": 88,
        "match_summary": "Strong Python match with dated tenure.",
        "gaps_identified": "",
        "years_analysis": {
            "Python": {
                "required_years": 3,
                "estimated_years": 5.0,
                "meets_requirement": True,
                "calculation": "Jan 2019–Jan 2024 = 60 mo = 5.0yr",
            }
        },
    }
    apply_years_tenure_qualify_gate(result, job_id=1)
    assert result["_years_tenure_status"] == "meets"
    assert result["_years_tenure_blocks_qualify"] is False
    assert years_tenure_allows_qualify(result) is True
    assert "YEARS CLOSE" not in (result.get("gaps_identified") or "")


def test_close_dated_years_allows_qualify_with_caveat():
    result = {
        "match_score": 84,
        "match_summary": "Solid AI engineer for the role.",
        "gaps_identified": "",
        "years_analysis": {
            "AI": {
                "required_years": 3,
                "estimated_years": 2.5,  # shortfall 0.5 ≤ 0.75 → close
                "meets_requirement": False,
                "calculation": "Dated AI roles = 2.5yr",
            }
        },
    }
    apply_years_tenure_qualify_gate(result, job_id=2)
    assert result["_years_tenure_status"] == "close"
    assert result["_years_tenure_blocks_qualify"] is False
    assert years_tenure_allows_qualify(result) is True
    assert "YEARS CLOSE:" in result["gaps_identified"]
    assert "dated history" in result["match_summary"].lower()


def test_clear_shortfall_blocks_qualify_even_without_score_gate():
    """Qualify block is independent of score — high score still blocked."""
    result = {
        "match_score": 90,
        "match_summary": "Looks strong overall.",
        "gaps_identified": "",
        "years_analysis": _nirav_like_years_analysis(),
    }
    apply_years_tenure_qualify_gate(result, job_id=3)
    assert result["_years_tenure_blocks_qualify"] is True
    assert years_tenure_allows_qualify(result) is False
    # Score untouched by qualify gate alone
    assert result["match_score"] == 90


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
