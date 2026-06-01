"""Fraud / fake-candidate detection package.

Phase 1 is a deterministic, advisory-only risk layer that scores incoming
candidates using signals Scout Genius already captures (resume hashes, contact
fields, work history, identity reuse, profile embeddings, application velocity).
It NEVER blocks the screening pipeline — it flags risk in the recruiter portal
and, on High-Risk, writes a vendor-neutral note to Bullhorn.

The pure signal evaluators (`fraud_detection.signals`) are dependency-free and
unit-testable in isolation. The engine (`fraud_detection.engine`) gathers DB /
Bullhorn facts and orchestrates persistence + notification.
"""

from fraud_detection.signals import (
    FraudSignal,
    FraudRiskBand,
    FraudAssessmentResult,
    aggregate,
    evaluate_disposable_email,
    evaluate_contact_anomalies,
    evaluate_work_history,
    evaluate_resume_reuse,
    evaluate_identity_reuse,
    evaluate_profile_near_duplicate,
    evaluate_velocity,
    evaluate_linkedin,
    evaluate_name_completeness,
    evaluate_third_party_submission,
    evaluate_jd_mirror,
    evaluate_ai_style_markers,
    extract_linkedin_url,
    is_incomplete_name,
    is_personal_email,
)

__all__ = [
    "FraudSignal",
    "FraudRiskBand",
    "FraudAssessmentResult",
    "aggregate",
    "evaluate_disposable_email",
    "evaluate_contact_anomalies",
    "evaluate_work_history",
    "evaluate_resume_reuse",
    "evaluate_identity_reuse",
    "evaluate_profile_near_duplicate",
    "evaluate_velocity",
    "evaluate_linkedin",
    "evaluate_name_completeness",
    "evaluate_third_party_submission",
    "evaluate_jd_mirror",
    "evaluate_ai_style_markers",
    "extract_linkedin_url",
    "is_incomplete_name",
    "is_personal_email",
]
