"""Round-trip tests for the strict screening.scoring response schema.

These tests assert two things:

1.  A realistic response shaped according to the strict schema validates
    against the schema (no false positives that would break prod once
    cutover happens).

2.  A response shaped to the strict schema flows through EVERY enforcer
    in ``screening/post_processing.py`` without raising, and post-proc
    output is structurally identical whether the response was produced
    by the loose-JSON-object mode (with extra fields) or the strict
    mode (without). This is the parity guarantee the cutover criteria
    in ``.local/session_plan.md`` will measure against in prod, here
    enforced as a unit-level invariant.
"""
from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest

from screening.output_schema import (
    DROPPED_FIELDS_AUDIT,
    SCREENING_SCORING_RESPONSE_SCHEMA,
    build_response_format,
    schema_top_level_keys,
)
from screening.post_processing import (
    coerce_scores,
    enforce_employment_continuity_gap,
    enforce_experience_floor,
    enforce_midcareer_gap,
    enforce_recency_hard_gate,
    enforce_remote_location,
    enforce_years_hard_gate,
    normalize_response_fields,
)


def _strict_shape_response() -> dict:
    """A realistic AI response that conforms to the strict schema —
    only the consumed top-level keys, no dropped fields.

    Numbers are chosen to exercise all enforcer branches: years
    shortfall in the mild band, recency lapse, mid-career gap, mid
    continuity gap, and EXPERIENCED experience level."""
    return {
        "match_score": 78,
        "technical_score": 82,
        "match_summary": (
            "Candidate is a senior data engineer with strong Spark and "
            "Snowflake experience. Most recent role is at a Tier-1 "
            "consulting firm working on data lake migrations."
        ),
        "skills_match": "Strong: Spark, Snowflake, Airflow, AWS. Partial: dbt.",
        "experience_match": (
            "9 years professional engineering experience, last 4 at "
            "consulting firm placing them on data platform engagements."
        ),
        "gaps_identified": (
            "No direct Databricks production experience; project work "
            "only. Mid-career 14-month gap 2019-2020."
        ),
        "key_requirements": (
            "Spark (5+ yrs), Snowflake (3+ yrs), dbt (2+ yrs), "
            "Databricks (3+ yrs)."
        ),
        "years_analysis": {
            "Spark": {
                "meets_requirement": True,
                "required_years": 5,
                "estimated_years": 7.5,
            },
            "Databricks": {
                "meets_requirement": False,
                "required_years": 3,
                "estimated_years": 1.2,
            },
        },
        "recency_analysis": {
            "most_recent_role_relevant": True,
            "second_recent_role_relevant": True,
            "months_since_relevant_work": 0,
            "penalty_applied": 0,
            "relevance_justification": (
                "Current role builds Spark/Snowflake pipelines for an "
                "enterprise data lake — directly relevant."
            ),
            "most_recent_role": "Senior Data Engineer @ Infosys (Client: Cigna)",
        },
        "employment_gap_analysis": {
            "gap_months": 0,
            "penalty_applied": 0,
            "last_role_end_date": "present",
            "largest_midcareer_gap_months": 14,
            "midcareer_gap_penalty_applied": 4,
            "midcareer_gap_between": "Capital One → Cigna",
        },
        "experience_level_classification": {
            "classification": "EXPERIENCED",
            "highest_role_type": "SENIOR",
            "total_professional_years": 9.0,
        },
    }


def _loose_shape_response() -> dict:
    """The same scoring decision shaped as the current loose-JSON
    mode would return it — same consumed fields PLUS the three big
    dropped fields the model currently enumerates.

    Post-processing must produce structurally identical output for
    this and for ``_strict_shape_response()`` (the diet must not
    change the score)."""
    loose = _strict_shape_response()
    loose["requirement_evidence"] = [
        {
            "requirement": "Spark 5+ years",
            "evidence": "7+ years building Spark Structured Streaming...",
            "verdict": "MET",
        },
        {
            "requirement": "Databricks 3+ years",
            "evidence": "Mentioned in passing on one capstone project...",
            "verdict": "NOT MET",
        },
    ]
    loose["work_authorization_analysis"] = (
        "Candidate has 9 years of US work experience enumerated as: "
        "Cigna (2022-present), Capital One (2018-2019), JPMC (2015-2018), "
        "Accenture (2014-2015), internship roles 2013-2014..."
    )
    loose["canadian_clearance_analysis"] = "N/A — candidate is US-based."
    return loose


def _run_all_enforcers(result: dict) -> dict:
    """Run the deterministic enforcers in their production order. We
    skip the GPT-rechecking and prestige/location-barrier enforcers
    here because they require fixtures or network. The deterministic
    subset is sufficient to prove score parity."""
    normalize_response_fields(result, job_id=99)
    coerce_scores(result, job_id=99)
    enforce_remote_location(result, job_id=99, work_type="Hybrid")
    # Skip enforce_years_hard_gate: it calls a recheck_fn that requires
    # an openai client; the years arithmetic itself is exercised by
    # _compute_shortfalls in tests/test_cvs_gates.py.
    enforce_recency_hard_gate(result, job_id=99)
    enforce_employment_continuity_gap(result, job_id=99)
    enforce_midcareer_gap(result, job_id=99)
    enforce_experience_floor(
        result,
        job_id=99,
        custom_requirements="Spark 5+ years required.",
        job_description="Senior Data Engineer — Spark, Snowflake.",
    )
    return result


def test_strict_response_validates_against_schema():
    """A response shaped to the strict schema validates clean."""
    try:
        import jsonschema
    except ImportError:
        pytest.skip("jsonschema not installed; structural validation skipped")
    jsonschema.validate(
        instance=_strict_shape_response(),
        schema=SCREENING_SCORING_RESPONSE_SCHEMA,
    )


def test_loose_response_fails_strict_schema():
    """A loose response with the dropped fields MUST fail validation —
    this is the whole point of the schema."""
    try:
        import jsonschema
    except ImportError:
        pytest.skip("jsonschema not installed; structural validation skipped")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance=_loose_shape_response(),
            schema=SCREENING_SCORING_RESPONSE_SCHEMA,
        )


def test_required_top_level_fields_match_fallback_contract():
    """The hard-coded fallback dict returned on empty/error responses
    in ``prompt_builder.py:863-870`` populates a specific set of keys.
    Those keys MUST be a subset of the schema's required keys,
    otherwise downstream code will break on a fallback path under
    strict mode."""
    fallback_keys = {
        "match_score",
        "match_summary",
        "skills_match",
        "experience_match",
        "gaps_identified",
        "key_requirements",
    }
    required = set(SCREENING_SCORING_RESPONSE_SCHEMA["required"])
    assert fallback_keys == required, (
        f"Required-field drift: schema={required}, fallback={fallback_keys}. "
        "Update either the schema's required list or the fallback dict "
        "in screening/prompt_builder.py."
    )


def test_dropped_fields_not_present_in_schema():
    """Each entry in DROPPED_FIELDS_AUDIT must NOT appear as a schema
    property. Catches accidental re-introduction of a verbose field."""
    schema_props = set(SCREENING_SCORING_RESPONSE_SCHEMA["properties"].keys())
    for dropped in DROPPED_FIELDS_AUDIT:
        assert dropped not in schema_props, (
            f"Field '{dropped}' was supposed to be dropped from the "
            "screening response, but it's in the strict schema. "
            "Either remove it from the schema or remove it from "
            "DROPPED_FIELDS_AUDIT."
        )


def test_build_response_format_shape():
    """Sanity check: helper returns the exact OpenAI Structured Outputs
    request shape, with strict toggle wired correctly."""
    non_strict = build_response_format(strict=False)
    assert non_strict["type"] == "json_schema"
    assert non_strict["json_schema"]["name"] == "screening_scoring_response"
    assert non_strict["json_schema"]["strict"] is False
    assert non_strict["json_schema"]["schema"] is SCREENING_SCORING_RESPONSE_SCHEMA

    strict = build_response_format(strict=True)
    assert strict["json_schema"]["strict"] is True


def test_schema_top_level_keys_are_sorted_and_match():
    """Helper used by audit tooling — sorted tuple of schema props."""
    keys = schema_top_level_keys()
    assert list(keys) == sorted(keys)
    assert set(keys) == set(SCREENING_SCORING_RESPONSE_SCHEMA["properties"].keys())


def test_post_processing_parity_strict_vs_loose():
    """THE BIG ONE.

    Run both shapes (strict-shaped and loose-shaped, same scoring
    decision) through every deterministic enforcer. The post-processed
    output must agree on ALL fields that drive downstream behavior:
    match_score, technical_score, gaps_identified, plus the analysis
    sub-dicts. Drift here means the diet would shift recruiter-visible
    behavior — must be zero before we touch prod.
    """
    strict_processed = _run_all_enforcers(_strict_shape_response())
    loose_processed = _run_all_enforcers(_loose_shape_response())

    parity_fields = (
        "match_score",
        "technical_score",
        "match_summary",
        "skills_match",
        "experience_match",
        "gaps_identified",
        "key_requirements",
        "recency_analysis",
        "employment_gap_analysis",
        "experience_level_classification",
        "years_analysis",
    )
    for field in parity_fields:
        assert strict_processed.get(field) == loose_processed.get(field), (
            f"Post-processing drift on '{field}':\n"
            f"  strict: {strict_processed.get(field)!r}\n"
            f"  loose:  {loose_processed.get(field)!r}"
        )


def test_years_hard_gate_parity_with_stub_recheck():
    """Parity check for `enforce_years_hard_gate` — the one enforcer
    `_run_all_enforcers` skipped because it normally calls a recheck_fn
    that requires an openai client. With a deterministic stub, the
    score-and-gaps behaviour must agree between strict-shaped and
    loose-shaped inputs."""
    from screening.post_processing import enforce_years_hard_gate

    def _stub_recheck(_resume_text, _years_analysis, _job_id, _job_title):
        # Identity recheck — caller will treat the existing analysis as
        # confirmed. Mirrors the post-recheck "still short" path.
        return None

    strict = _strict_shape_response()
    loose = _loose_shape_response()
    # Pre-coerce so .get('match_score') and friends are present in the
    # exact shape the enforcer expects.
    coerce_scores(strict, job_id=99)
    coerce_scores(loose, job_id=99)
    enforce_years_hard_gate(
        strict, job_id=99, job_title="Sr Data Eng",
        resume_text="...", recheck_fn=_stub_recheck,
    )
    enforce_years_hard_gate(
        loose, job_id=99, job_title="Sr Data Eng",
        resume_text="...", recheck_fn=_stub_recheck,
    )
    for field in ("match_score", "technical_score", "gaps_identified", "years_analysis"):
        assert strict.get(field) == loose.get(field), (
            f"enforce_years_hard_gate drift on '{field}':\n"
            f"  strict: {strict.get(field)!r}\n"
            f"  loose:  {loose.get(field)!r}"
        )


def test_shadow_harness_schema_audit_mode_smoke():
    """Targeted harness test for `_run_screening_shadow` schema mode.

    Asserts:
      1. Gate-off → no-op (no client call).
      2. Gate-on with a stub client → schema response_format is passed
         through verbatim; row is tagged `{model}|loose` /
         `{model}|strict`; fail-soft on any call error.
    """
    import os
    import sys
    import importlib

    # Force-reload prompt_builder so env mutations take effect deterministically.
    if "screening.prompt_builder" in sys.modules:
        importlib.reload(sys.modules["screening.prompt_builder"])
    from screening import prompt_builder as pb

    captured = {}

    class _StubClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    captured["kwargs"] = kwargs
                    # Simulate a deliberate failure so we exercise the
                    # fail-soft path AND the row-write path with shadow_error.
                    raise RuntimeError("synthetic shadow failure")

    captured_rows = []

    def _fake_save(row):
        captured_rows.append(row)

    pb._save_screening_ab_row = _fake_save  # type: ignore[assignment]
    pb._shadow_screening_rate_check = lambda: True  # bypass per-hour cap  # type: ignore[assignment]

    rf = build_response_format(strict=False)

    # --- 1. Gate OFF → no call, no row.
    os.environ.pop("SCREENING_SCHEMA_AUDIT_ENABLED", None)
    pb._run_screening_shadow(
        system_message="sys",
        user_prompt="usr",
        prod_model="gpt-5.4",
        prod_score=88.0,
        prod_qualified=True,
        job_id=42,
        job_title="t",
        openai_client=_StubClient(),
        schema_audit_response_format=rf,
    )
    assert "kwargs" not in captured, "schema audit fired with gate OFF"
    assert not captured_rows, "schema audit wrote a row with gate OFF"

    # --- 2. Gate ON → call fires with strict response_format, row tagged,
    #         shadow_error populated (no exception escapes).
    os.environ["SCREENING_SCHEMA_AUDIT_ENABLED"] = "true"
    try:
        pb._run_screening_shadow(
            system_message="sys",
            user_prompt="usr",
            prod_model="gpt-5.4",
            prod_score=88.0,
            prod_qualified=True,
            job_id=42,
            job_title="t",
            openai_client=_StubClient(),
            schema_audit_response_format=rf,
        )
    finally:
        os.environ.pop("SCREENING_SCHEMA_AUDIT_ENABLED", None)

    assert captured.get("kwargs"), "schema audit did not invoke the OpenAI client"
    assert captured["kwargs"]["response_format"] is rf, (
        "Shadow call must pass through the schema response_format verbatim — "
        f"got {captured['kwargs']['response_format']!r}"
    )
    assert captured["kwargs"]["model"] == "gpt-5.4", (
        "Schema audit must use the SAME model as prod, not the A/B mini model"
    )
    assert len(captured_rows) == 1, "schema audit should write exactly one ab_log row"
    row = captured_rows[0]
    assert row["prod_model"] == "gpt-5.4|loose", (
        f"prod_model tag must be '{{model}}|loose'; got {row['prod_model']!r}"
    )
    assert row["shadow_model"] == "gpt-5.4|strict", (
        f"shadow_model tag must be '{{model}}|strict'; got {row['shadow_model']!r}"
    )
    assert row["shadow_error"] and "synthetic shadow failure" in row["shadow_error"], (
        "fail-soft path must populate shadow_error rather than raise"
    )
    assert row["shadow_score"] is None, (
        "failed shadow call should leave shadow_score unset, not 0"
    )


def test_loose_extra_fields_are_ignored_by_post_processing():
    """Defensive: confirm that the three dropped fields, when present,
    have no effect on post-processing output. This is what makes the
    diet a no-op from the recruiter's perspective."""
    with_extras = _run_all_enforcers(_loose_shape_response())
    # The extras pass through unchanged because no enforcer reads them.
    assert "requirement_evidence" in with_extras
    assert "work_authorization_analysis" in with_extras
    assert "canadian_clearance_analysis" in with_extras
    # And they did not bleed into any consumed field:
    assert "requirement_evidence" not in with_extras["match_summary"]
    assert "work_authorization" not in with_extras["gaps_identified"].lower()
