"""
Strict JSON Schema for the screening.scoring response.

WHY THIS EXISTS
---------------
The screening scoring call is ~67% of our OpenAI spend, and the *output*
side (the model's writing) is the dominant cost within that. The model
is currently free to return any JSON object, and per the system prompt
it enumerates three large blocks that no downstream code reads:

  - requirement_evidence              (per-requirement quotes)
  - work_authorization_analysis       (full US work history enumeration)
  - canadian_clearance_analysis       (full Canadian role enumeration)

Field-usage audit (2026-05-19) confirmed those three are
**system-prompt-instructed but never consumed** by post_processing.py,
note_builder.py, recruiter UI templates, or the DB.

This module defines a JSON Schema that lists **only the consumed fields**
and uses ``additionalProperties: false`` at the top level to forbid the
unread blocks. Switching the API call from
``response_format={"type":"json_object"}`` to a json_schema response
format using this schema is the lever — the model still writes the
fields we keep, but won't write the ones we omit.

Sub-objects (``recency_analysis``, ``employment_gap_analysis``,
``experience_level_classification``) are also locked down to the keys
post-processing actually reads. ``years_analysis`` is left as an
open-keys object because it is a dict-of-skill-names (e.g.
``{"Databricks": {...}, "Kafka": {...}}``) which is variable per job.

Round-trip safety: every field in this schema is something that
``screening/post_processing.py`` reads via ``result.get(...)``. A
response shaped by this schema MUST flow through every enforcer
without raising. See ``tests/test_output_schema.py``.
"""
from __future__ import annotations

from typing import Any


SCREENING_SCORING_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "match_score",
        "match_summary",
        "skills_match",
        "experience_match",
        "gaps_identified",
        "key_requirements",
    ],
    "properties": {
        "match_score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
        },
        "technical_score": {
            "type": ["integer", "null"],
            "minimum": 0,
            "maximum": 100,
        },
        "match_summary": {"type": "string", "maxLength": 1500},
        "skills_match": {"type": "string", "maxLength": 1500},
        "experience_match": {"type": "string", "maxLength": 1500},
        "gaps_identified": {"type": "string", "maxLength": 2000},
        "key_requirements": {"type": "string", "maxLength": 2000},
        "years_analysis": {
            "type": "object",
            "description": (
                "Dict keyed by skill name. Each value reports whether the "
                "candidate meets the requirement and the year counts that "
                "drive enforce_years_hard_gate."
            ),
            "additionalProperties": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "meets_requirement": {"type": "boolean"},
                    "required_years": {"type": "number", "minimum": 0},
                    "estimated_years": {"type": "number", "minimum": 0},
                },
            },
        },
        "recency_analysis": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "most_recent_role_relevant": {"type": "boolean"},
                "second_recent_role_relevant": {"type": "boolean"},
                "months_since_relevant_work": {
                    "type": ["integer", "number"],
                    "minimum": 0,
                },
                "penalty_applied": {"type": ["integer", "number", "string", "null"]},
                "relevance_justification": {"type": "string", "maxLength": 500},
                "most_recent_role": {"type": "string", "maxLength": 300},
            },
        },
        "employment_gap_analysis": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "gap_months": {"type": ["integer", "number", "string"]},
                "penalty_applied": {"type": ["integer", "number", "string", "null"]},
                "last_role_end_date": {"type": "string", "maxLength": 50},
                "largest_midcareer_gap_months": {
                    "type": ["integer", "number", "string"]
                },
                "midcareer_gap_penalty_applied": {
                    "type": ["integer", "number", "string", "null"]
                },
                "midcareer_gap_between": {"type": "string", "maxLength": 200},
            },
        },
        "experience_level_classification": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "classification": {"type": "string", "maxLength": 50},
                "highest_role_type": {"type": "string", "maxLength": 50},
                "total_professional_years": {
                    "type": ["number", "integer"],
                    "minimum": 0,
                },
            },
        },
    },
}


# Top-level fields the AI currently emits that this schema deliberately
# OMITS. Keep this list aligned with the field-usage audit; if any
# downstream consumer starts reading one of these, add it to the schema
# above instead of removing it from this list.
DROPPED_FIELDS_AUDIT = (
    "requirement_evidence",
    "work_authorization_analysis",
    "canadian_clearance_analysis",
)


def build_response_format(strict: bool = False) -> dict[str, Any]:
    """Build the OpenAI ``response_format`` kwarg for the strict-schema call.

    Parameters
    ----------
    strict:
        When True, request OpenAI's full Structured Outputs guarantee
        (``strict: true``). This mode forbids open-keys objects, so we
        cannot use it as long as ``years_analysis`` remains a
        dict-keyed-by-skill-name. Default False — the schema is used as
        a strong guide rather than a hard contract, which is still
        sufficient to suppress the dropped fields in practice.

    Returns
    -------
    A dict suitable to pass directly as ``response_format=`` to
    ``openai_client.chat.completions.create(...)``.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "screening_scoring_response",
            "schema": SCREENING_SCORING_RESPONSE_SCHEMA,
            "strict": strict,
        },
    }


def schema_top_level_keys() -> tuple[str, ...]:
    """Sorted tuple of the top-level keys allowed by the strict schema.

    Used by tests and audit tooling to assert no unexpected keys
    appear in shadow responses.
    """
    return tuple(sorted(SCREENING_SCORING_RESPONSE_SCHEMA["properties"].keys()))
