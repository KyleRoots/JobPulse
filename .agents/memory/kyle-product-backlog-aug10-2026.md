---
name: Kyle product backlog Aug 10 2026
description: Intentional product backlog from Kyle (Aug 10 2026) — Redeployment Autopilot (sooner) and BD Signal Engine (backburner). Do not implement unless asked.
---

# Kyle product backlog (Aug 10 2026)

**Source:** Kyle Roots, intentional product intent (conversation Aug 10 2026).  
**Status:** Backlog only — **do not implement** unless Kyle (or a follow-up task) explicitly asks. This note exists so future agents treat these as agreed direction, not speculative ideas.

---

## 1. Redeployment Autopilot — sooner rather than later

**Priority:** Follow-up enhancement; pick up **sooner rather than later** when capacity allows.

**Agreed approach (constraints are part of the product intent):**

1. **Notify-only first** — surface ending placements × top open job matches to humans (alerts / queue / UI — reuse existing notification patterns where possible).
2. **Human-gated submit** — never create Bullhorn `JobSubmission` automatically on the first version. Humans approve; automation suggests.
3. **After / with `dateEnd` hygiene audit** — ship (or pair) with cleanup of placement end-date data quality so “ending soon” signals are trustworthy.
4. **Reuse existing matching stack** — do not build a parallel matcher; wire into current candidate↔job matching / scoring paths already in JobPulse.

**Out of scope for v1:** auto-submit, scrape-heavy discovery, replacing Prospector.

---

## 2. BD Signal Engine — backburner

**Priority:** **Backburner / low.** Do not prioritize ahead of Redeployment Autopilot or core screening/inbound work.

**Agreed approach:**

- **Do not** build a scrape-heavy BD / signal module as a standalone product.
- **Only possible later** as something like **Prospector v2**: draft-for-approval signals (human-gated), not auto-outreach from scrapes.
- If resurfaced, reconfirm with Kyle before design or implementation.

---

## Agent pickup checklist

When Kyle asks to start Redeployment Autopilot:

- [ ] Confirm notify-only + human-gated submit still desired
- [ ] Plan `dateEnd` hygiene audit in parallel or first
- [ ] Inventory existing match / placement / open-job APIs and notification surfaces
- [ ] Explicitly forbid auto `JobSubmission` in the first milestone

When anyone proposes BD Signal / scrape BD:

- [ ] Point here — backburner; only as Prospector v2 draft-for-approval if ever
