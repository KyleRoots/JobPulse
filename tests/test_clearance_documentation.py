"""
Regression tests for the clearance/work-authorization documentation enforcer.

`enforce_clearance_documentation` guarantees that for any job whose requirements
trigger a security-clearance rule, an explicit clearance verdict is present in
BOTH `match_summary` (rendered in the recruiter email) AND `gaps_identified`
(rendered in the Bullhorn note). It is documentation-only: it MUST NEVER mutate
`match_score` or `technical_score`.
"""
from screening.post_processing import (
    enforce_clearance_documentation,
    enforce_work_authorization_documentation,
)


CA_JD = "Senior role. Active Secret clearance or eligibility required. Ottawa, ON."
US_JD = "DoD program. Must hold an active US Secret clearance (DoD Secret). Virginia."
NO_CLEARANCE_JD = "Backend engineer. Python, PostgreSQL, 5+ years experience. Remote."
US_AUTH_JD = "Backend role. Must be a US Citizen or Green Card holder. No sponsorship."
NEUTRAL_JD = "Frontend engineer. React, TypeScript, 4+ years. Remote within Canada."


def _base_result(match_summary="", gaps="", score=42):
    return {
        "match_score": score,
        "technical_score": score,
        "match_summary": match_summary,
        "gaps_identified": gaps,
        "key_requirements": "",
    }


def test_trigger_both_missing_injects_both():
    result = _base_result(
        match_summary="Strong technical match across the core stack.",
        gaps="Limited cloud experience.",
    )
    enforce_clearance_documentation(result, 1, None, CA_JD)
    assert "clearance" in result["match_summary"].lower()
    assert "clearance" in result["gaps_identified"].lower()
    # Score untouched (documentation-only).
    assert result["match_score"] == 42
    assert result["technical_score"] == 42


def test_trigger_only_gaps_backfills_summary():
    result = _base_result(
        match_summary="Solid match on the required tech stack.",
        gaps="Does not hold and is not inferable as eligible for Secret clearance — 2 years Canadian experience vs the 10-year bar.",
    )
    enforce_clearance_documentation(result, 2, None, CA_JD)
    # Summary now carries the clearance verdict copied from gaps.
    assert "clearance" in result["match_summary"].lower()
    assert "clearance" in result["gaps_identified"].lower()
    assert result["match_score"] == 42


def test_trigger_only_summary_backfills_gaps():
    result = _base_result(
        match_summary="Candidate is likely eligible to obtain Secret clearance based on 12 years of Canadian experience.",
        gaps="Minor location flag.",
    )
    enforce_clearance_documentation(result, 3, None, CA_JD)
    assert "clearance" in result["match_summary"].lower()
    assert "clearance" in result["gaps_identified"].lower()
    assert result["match_score"] == 42


def test_no_trigger_is_noop():
    result = _base_result(
        match_summary="Great Python and PostgreSQL background.",
        gaps="No major gaps.",
    )
    before_summary = result["match_summary"]
    before_gaps = result["gaps_identified"]
    enforce_clearance_documentation(result, 4, None, NO_CLEARANCE_JD)
    assert result["match_summary"] == before_summary
    assert result["gaps_identified"] == before_gaps
    assert result["match_score"] == 42


def test_already_documented_both_is_noop():
    summary = "Likely eligible for Secret clearance per Canadian tenure."
    gaps = "No active Secret clearance evidenced on resume."
    result = _base_result(match_summary=summary, gaps=gaps)
    enforce_clearance_documentation(result, 5, None, CA_JD)
    assert result["match_summary"] == summary
    assert result["gaps_identified"] == gaps
    assert result["match_score"] == 42


def test_us_clearance_triggers_documentation():
    result = _base_result(
        match_summary="Strong DevSecOps background matching the role.",
        gaps="No US clearance context provided.",
    )
    enforce_clearance_documentation(result, 6, None, US_JD)
    assert "clearance" in result["match_summary"].lower()
    assert "clearance" in result["gaps_identified"].lower()
    assert result["match_score"] == 42


def test_trigger_via_custom_requirements():
    result = _base_result(
        match_summary="Good infra match.",
        gaps="Some gaps in monitoring tooling.",
    )
    enforce_clearance_documentation(
        result, 7, "Must obtain Enhanced Reliability status.", "Generic JD text."
    )
    assert "clearance" in result["match_summary"].lower()
    assert "clearance" in result["gaps_identified"].lower()


def test_empty_fields_get_generic_line():
    result = _base_result(match_summary="", gaps="")
    enforce_clearance_documentation(result, 8, None, CA_JD)
    assert "clearance" in result["match_summary"].lower()
    assert "clearance" in result["gaps_identified"].lower()
    assert "recruiter verification" in result["match_summary"].lower()


def test_failsoft_on_malformed_result():
    # Missing match_summary/gaps keys entirely — must not raise. Trigger comes
    # from the JD (key_requirements is intentionally NOT used for triggering).
    result = {"match_score": 10}
    enforce_clearance_documentation(result, 9, None, "Secret clearance required.")
    # Enforcer injected into the absent fields without raising.
    assert "clearance" in (result.get("match_summary") or "").lower()
    assert "clearance" in (result.get("gaps_identified") or "").lower()
    assert result["match_score"] == 10


def test_clearance_trigger_ignores_hallucinated_key_requirements():
    # A non-clearance JD where only the model-generated key_requirements mentions
    # clearance must NOT activate the enforcer (no false-positive injection).
    result = _base_result(
        match_summary="Strong Python and data background.",
        gaps="Limited Kafka experience.",
    )
    result["key_requirements"] = "Secret clearance required"
    enforce_clearance_documentation(result, 10, None, NO_CLEARANCE_JD)
    assert "clearance" not in result["match_summary"].lower()
    assert "clearance" not in result["gaps_identified"].lower()


def test_active_secret_phrasing_triggers():
    result = _base_result(
        match_summary="Excellent infra match.",
        gaps="Minor tooling gaps.",
    )
    enforce_clearance_documentation(
        result, 11, None, "Role requires an Active Secret in place."
    )
    assert "clearance" in result["match_summary"].lower()
    assert "clearance" in result["gaps_identified"].lower()


# ── US work-authorization documentation enforcer ──

def test_work_auth_trigger_both_missing_injects_both():
    result = _base_result(
        match_summary="Strong backend match on the core stack.",
        gaps="Limited messaging-queue depth.",
    )
    enforce_work_authorization_documentation(result, 20, None, US_AUTH_JD)
    s, g = result["match_summary"].lower(), result["gaps_identified"].lower()
    assert any(t in s for t in ("authoriz", "citizen", "sponsor"))
    assert any(t in g for t in ("authoriz", "citizen", "sponsor"))
    assert result["match_score"] == 42


def test_work_auth_only_summary_backfills_gaps():
    result = _base_result(
        match_summary="Scout Screening infers strong likelihood of US work authorization based on 8 years of US experience.",
        gaps="Minor location note.",
    )
    enforce_work_authorization_documentation(result, 21, None, US_AUTH_JD)
    assert "authoriz" in result["gaps_identified"].lower()
    assert result["match_score"] == 42


def test_work_auth_no_trigger_is_noop():
    result = _base_result(
        match_summary="Great React match.",
        gaps="No major gaps.",
    )
    before = (result["match_summary"], result["gaps_identified"])
    enforce_work_authorization_documentation(result, 22, None, NEUTRAL_JD)
    assert (result["match_summary"], result["gaps_identified"]) == before


def test_work_auth_already_documented_is_noop():
    summary = "Candidate appears US-authorized (US citizen stated)."
    gaps = "No sponsorship concerns; citizenship evidenced."
    result = _base_result(match_summary=summary, gaps=gaps)
    enforce_work_authorization_documentation(result, 23, None, US_AUTH_JD)
    assert result["match_summary"] == summary
    assert result["gaps_identified"] == gaps


def test_clearance_enforcer_does_not_fire_on_auth_only_job():
    # A US work-auth job with NO clearance language must not get a clearance line.
    result = _base_result(
        match_summary="Solid match.",
        gaps="Some gaps.",
    )
    enforce_clearance_documentation(result, 24, None, US_AUTH_JD)
    assert "clearance" not in result["match_summary"].lower()
    assert "clearance" not in result["gaps_identified"].lower()


# ── Verdict-based detection (requirement-echo must NOT suppress backfill) ──

def test_clearance_requirement_echo_only_still_backfills_verdict():
    # Both fields merely RESTATE the job requirement (topic word, no candidate
    # verdict). The enforcer must inject a real verdict, not no-op.
    result = _base_result(
        match_summary="This role requires an active Secret clearance.",
        gaps="Secret clearance is required for this position.",
    )
    enforce_clearance_documentation(result, 30, None, CA_JD)
    # A genuine verdict (recruiter verification line) is now present in both.
    assert "recruiter verification" in result["match_summary"].lower()
    assert "recruiter verification" in result["gaps_identified"].lower()
    assert result["match_score"] == 42


def test_clearance_requirement_echo_in_one_field_copies_real_verdict():
    # Summary echoes the requirement only; gaps has the real candidate verdict.
    # The summary must receive the VERDICT sentence, not stay requirement-only.
    result = _base_result(
        match_summary="This role requires Secret clearance.",
        gaps="Candidate holds an active Secret clearance per resume.",
    )
    enforce_clearance_documentation(result, 31, None, CA_JD)
    assert "holds" in result["match_summary"].lower()
    assert "clearance" in result["gaps_identified"].lower()
    assert result["match_score"] == 42


def test_work_auth_requirement_echo_only_still_backfills_verdict():
    result = _base_result(
        match_summary="Must be a US citizen for this role.",
        gaps="US citizenship is required.",
    )
    enforce_work_authorization_documentation(result, 32, None, US_AUTH_JD)
    assert "recruiter verification" in result["match_summary"].lower()
    assert "recruiter verification" in result["gaps_identified"].lower()
    assert result["match_score"] == 42


def test_clearance_requirement_echo_with_cue_word_still_backfills():
    # Requirement-echo text that CONTAINS a cue word ('eligible'/'eligibility')
    # but states no candidate status must NOT be treated as documented.
    result = _base_result(
        match_summary="Role requires Secret clearance; candidate must be clearance eligible.",
        gaps="Secret clearance eligibility required for this role.",
    )
    enforce_clearance_documentation(result, 33, None, CA_JD)
    assert "recruiter verification" in result["match_summary"].lower()
    assert "recruiter verification" in result["gaps_identified"].lower()
    assert result["match_score"] == 42


def test_work_auth_requirement_echo_with_cue_word_still_backfills():
    # 'authorized to work' appears, but only as a requirement — not a verdict.
    result = _base_result(
        match_summary="Role requires candidate to be authorized to work in the US.",
        gaps="US work authorization required for this position.",
    )
    enforce_work_authorization_documentation(result, 34, None, US_AUTH_JD)
    assert "recruiter verification" in result["match_summary"].lower()
    assert "recruiter verification" in result["gaps_identified"].lower()
    assert result["match_score"] == 42


def test_clearance_enforcer_is_idempotent():
    # Running twice must not duplicate/keep mutating once a verdict is present.
    result = _base_result(
        match_summary="Role requires Secret clearance; candidate must be clearance eligible.",
        gaps="Secret clearance eligibility required.",
    )
    enforce_clearance_documentation(result, 35, None, CA_JD)
    first_summary = result["match_summary"]
    first_gaps = result["gaps_identified"]
    enforce_clearance_documentation(result, 35, None, CA_JD)
    assert result["match_summary"] == first_summary
    assert result["gaps_identified"] == first_gaps


def test_work_auth_enforcer_is_idempotent():
    result = _base_result(
        match_summary="Role requires candidate to be authorized to work in the US.",
        gaps="US work authorization required.",
    )
    enforce_work_authorization_documentation(result, 36, None, US_AUTH_JD)
    first_summary = result["match_summary"]
    first_gaps = result["gaps_identified"]
    enforce_work_authorization_documentation(result, 36, None, US_AUTH_JD)
    assert result["match_summary"] == first_summary
    assert result["gaps_identified"] == first_gaps


def test_clearance_verdict_without_literal_word_is_recognized():
    # Canadian verdict that omits the literal word "clearance" (uses "Reliability
    # Status") must be recognized as documented and copied, not redundantly
    # replaced by a generic fallback line.
    result = _base_result(
        match_summary="Candidate is likely eligible for Reliability Status based on 12 years of Canadian experience.",
        gaps="Minor location note.",
    )
    enforce_clearance_documentation(result, 38, None, CA_JD)
    assert "reliability status" in result["gaps_identified"].lower()
    assert "recruiter verification" not in result["gaps_identified"].lower()
    assert result["match_score"] == 42


def test_work_auth_does_not_require_sponsorship_is_verdict():
    # "does not require sponsorship" is a candidate verdict, NOT requirement echo.
    result = _base_result(
        match_summary="Candidate does not require sponsorship and is authorized to work in the US.",
        gaps="Minor location note.",
    )
    enforce_work_authorization_documentation(result, 37, None, US_AUTH_JD)
    # Summary is a real verdict; gaps gets the verdict copied in (not the fallback).
    assert "sponsorship" in result["gaps_identified"].lower()
    assert "recruiter verification" not in result["match_summary"].lower()
    assert result["match_score"] == 42
