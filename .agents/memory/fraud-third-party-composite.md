---
name: Fraud third-party-submission composite gating
description: The exact email eligibility rule for the "third-party submission" fraud composite, and why.
---

# Fraud third-party-submission composite gating

The "possible third-party submission" composite signal fires only when an
incomplete candidate name is paired with a **present, valid, non-personal,
non-disposable** email domain (corporate/agency/custom). It is gated in the
engine, not in the pure evaluator.

**Why:** A missing or malformed email is NOT evidence of an agency-submitted
shell profile — treating "unknown" as "non-personal" let the composite fire on
just an incomplete name, producing false Review-band scores. A disposable email
is its own (separately scored) signal, so counting it here would double-count
the same address. The composite must represent the specific legit-looking
corporate-domain pattern only.

**How to apply:** When wiring `evaluate_third_party_submission`, compute the
email eligibility in the engine: require `email`, an `@`, a dotted domain, and
`not is_personal_email(email) and not is_disposable_domain(email)`. Pass the
negation as the evaluator's `email_personal` arg. Keep the foreign-location flag
a soft amplifier only — never a standalone trigger.
