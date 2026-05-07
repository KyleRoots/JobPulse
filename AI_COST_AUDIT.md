# Scout Genius — AI Cost Optimization Audit

**Date:** May 7, 2026
**Baseline spend:** $4,700/month (April 2026)
**Audit scope:** All 36 OpenAI call sites (34 chat-completions + 2 embeddings) across 13 production files
**Method:** Read-only code audit + 30-day production volume telemetry

---

## TL;DR — Problem → Fix → Benefit

- **Problem:** ~60% of the OpenAI bill is the core vetting verdict (which we cannot downgrade without quality loss). The remaining ~40% (~$1,900/mo) contains 9 call sites that use flagship GPT-5.4 for tasks GPT-4.1-mini handles at parity — pure extraction, classification, OCR, and HTML formatting.
- **Fix:** Phased downgrade. **Phase 1 (Safe, ~9 sites):** flip clear-cut extraction/classification calls to mini. **Phase 2 (A/B Validated, ~9 sites):** validate mini quality on bounded-reasoning tasks via parallel run on historical data, then flip. **Phase 3 (No Action):** 12 sites stay flagship — these are core verdicts, recruiter-facing brand voice, and Bullhorn execution-plan generation.
- **Benefit:** Realistic projected savings of **$900–1,400/month (19–30%)** — taking the bill from $4,700 to ~$3,300–3,800/mo. Zero quality risk on Phase 1; controlled-validation risk on Phase 2; the brain of the product (vetting verdicts, Scout Support solutions, requirements extraction) is fully protected.

---

## 1. The 60/40 reality

Before optimizing, understand where the money goes:

| Call site | Est. monthly cost | % of bill | Lever? |
|---|---|---|---|
| **[11] Main vetting verdict** (33,232 matches × xlarge prompts) | ~$2,800 | ~60% | ❌ Untouchable — this is the brain |
| **[6] Vetting outcome (Scout Vetting)** (4k sessions) | ~$400 | ~9% | ❌ Core verdict |
| **[30] PDF→HTML formatter** (~2,800 resumes × 20k+8k tokens) | ~$650 | ~14% | ✅ T1 (mechanical formatting) |
| **[8] Years recheck** (subset of matches × large prompts) | ~$300 | ~6% | ✅ T1 (arithmetic with formula) |
| **[27] AI resume parser** (2,836 emails) | ~$120 | ~3% | ✅ T1 (schema extraction) |
| All other 31 call sites combined | ~$430 | ~8% | Mixed |

**Key insight:** ~70% of the bill is two flagship calls that *must* stay flagship for quality reasons. The savings opportunity lives in the remaining 30%, where ~$900/mo is sitting in T1-tier tasks running on a flagship model.

---

## 2. Categorized recommendations

### 2A. Phase 1 — DOWNGRADE_MINI (9 call sites, ~$900/mo savings)
Pure extraction, classification, or formatting tasks. ~96% cost reduction per call. Low/zero quality risk; reversible in minutes.

| # | Call site | Current | Volume/mo | Est. savings | Risk |
|---|---|---|---|---|---|
| [27] | `email_inbound_service/ai_mixin.py:67` — AI resume parser | gpt-5.4 | 2,836 | **~$120** | Very low (downstream `is_valid_name` validator) |
| [30] | `resume_parser.py:230` — PDF→HTML formatter | gpt-5.4 | ~2,800 | **~$470** | Very low (mechanical reformatting) |
| [8] | `screening/prompt_builder.py:126` — years arithmetic recheck | gpt-5.4 | ~10,000 | **~$280** | Very low (gated: only override if delta ≥ 0.5yr) |
| [31] | `automation_service/resume_mixin.py:720` — title extractor | gpt-5.4 | ~3,000 | **~$23** | Low (length validator + NONE fallback) |
| [3] | `job_classification_service.py:121` — closed-taxonomy classifier | gpt-5.4 | ~120 | ~$2 | Very low (closed lists) |
| [15] | `scout_support/conversation.py:972` — intent classifier | gpt-5.4 | ~10 | <$1 | Very low (keyword fallback) |
| [17] | `scout_support/conversation.py:1126` — handling intent | gpt-5.4 | ~10 | <$1 | Very low (keyword fallback) |
| [25] | `scout_support_service.py:365` — platform ticket understand | gpt-5.4 | ~1 | <$1 | Very low (no ATS execution risk) |
| [22] | `scout_support/ai_analysis.py:760` — attachment image OCR | gpt-5.4 | ~5 | <$5 | Low (verify mini vision call signature first) |
| | | | **Phase 1 total** | **~$900/mo** | |

**Why these are safe:** Every call has either (a) a closed taxonomy / fixed schema, (b) a downstream deterministic validator catching errors, or (c) negligible recruiter visibility. Resume parser already has `is_valid_name` blocklists for mini's typical failure modes. Years recheck is gated by a 0.5-year delta threshold. PDF→HTML has explicit "preserve all content verbatim" instruction — mechanical, not creative.

### 2B. Phase 2 — NEEDS_AB_TEST (9 call sites, ~$400–500/mo additional savings)
Light reasoning tasks where mini is *probably* sufficient but quality regression would be visible. Validate on historical data before flipping.

| # | Call site | Current | Validation method |
|---|---|---|---|
| [4] | `scout_vetting_service.py:326` — vetting question generation | gpt-5.4 | Recruiter blind-rates 30 mini vs flagship questions for tone/specificity |
| [5] | `scout_vetting_service.py:589` — reply intent + answer extraction | gpt-5.4 | Back-test 100 historical replies, compare answer-extraction completeness |
| [10] | `screening/prompt_builder.py:306` — zero-score reverify | gpt-5.4 | Replay zero-score logs, confirm rescue rate within 5% of flagship |
| [13] | `scout_support/conversation.py:633` — platform follow-up reply | gpt-5.4 | Sample 20 historical platform tickets, recruiter blind-rate |
| [26] | `routes/scout_screening.py:454` — requirements optimizer | gpt-5.4 | 20 raw requirements rewritten by both, recruiter picks better blind |
| [29] | `email_inbound_service/ai_mixin.py:311` — duplicate name validator | gpt-5.4 | Backfill of name-matched pairs with known truth labels |
| [34][35] | `fuzzy_duplicate_matcher.py:640/646` — fuzzy duplicate scoring | gpt-5.4 | Labeled set of historical pairs; compare precision/recall, can lift 0.90 threshold to compensate |
| [2] | `scout_prospector_service.py:519` — ICP refinement | gpt-5.4 | Side-by-side on 50 ICPs, recruiter picks |

**Phase 2 estimated savings if all flip:** ~$400–500/mo. Realistic capture (some won't pass A/B): ~$250–400/mo.

### 2C. Phase 3 — KEEP_FLAGSHIP (12 call sites, $0 savings, intentional)
The brain of the product. Downgrading any of these costs more in lost placements / bad ATS mutations / brand damage than the entire savings from all other downgrades combined.

| # | Call site | Why protected |
|---|---|---|
| [11] | Main vetting verdict (`screening/prompt_builder.py:446`) | Drives every shortlist decision |
| [6] | Scout Vetting outcome (`scout_vetting_service.py:712`) | Final qualified/not-qualified verdict |
| [9] | Job requirements extraction (`screening/prompt_builder.py:224`) | Cascades into every match for that job |
| [7] | Candidate-facing follow-up email | Brand voice — candidates judge us by tone |
| [1] | Prospector web-search research | Open-ended discovery + ranking with web tool |
| [12][14][16][18][19][20][21] | Scout Support: solution proposals, admin questions, execution-step refinement, draft generator, initial understanding, clarification re-analysis, retry strategy | All trigger real Bullhorn mutations or admin-facing decisions |

### 2D. Already mini, no action (4 sites)
- `scout_support/knowledge.py:265` — failure lesson summarizer
- `email_inbound_service/ai_mixin.py:160` — last-resort identity extractor
- `vetting/resume_utils.py:149` and `:253` — vision OCR (raw file + PDF pages)

### 2E. Embeddings — separate cost axis (2 sites)
`text-embedding-3-large` × ~11,000 candidate embeddings + 120 job embeddings/mo. Estimated <2% of total bill. **Recommendation: leave alone.** Downgrading to `-3-small` would cut embedding cost ~80% (~$30/mo savings) but degrade the Layer 1 cosine pre-filter, which could *increase* total cost by sending more irrelevant pairs to the flagship vetting layer.

---

## 3. Projected savings — three scenarios

| Scenario | Monthly bill | Savings | % cut |
|---|---|---|---|
| **Today (baseline)** | $4,700 | — | — |
| **Phase 1 only** (low-risk wins) | ~$3,800 | ~$900 | ~19% |
| **Phase 1 + Phase 2 partial** (realistic) | ~$3,400 | ~$1,300 | ~28% |
| **Phase 1 + Phase 2 full** (best case if all A/Bs pass) | ~$3,300 | ~$1,400 | ~30% |

**Annualized:** $10,800–16,800/year saved.

> **Honesty note:** these are model-based estimates from prompt-size class × call volume × current OpenAI pricing. Actual savings will vary ±15%. Phase 0 (below) installs real per-call-site cost telemetry so the next audit uses ground-truth numbers, not estimates.

---

## 4. Recommended phased rollout

### Phase 0 — Instrument cost telemetry (2–3 hr, prerequisite)
Before downgrading anything, add token-usage logging to a single helper used by every OpenAI call site. Capture: call_site_id, model, input_tokens, output_tokens, $-cost, timestamp. Store in a lightweight `openai_call_log` table. **Outcome:** real per-call-site dollar attribution from day one, so we can prove savings instead of estimating them.

### Phase 1 — Safe downgrades (4–6 hr build + 1 week monitoring)
Flip the 9 DOWNGRADE_MINI sites in a single PR. Add a `Settings.MODEL_TIER_OVERRIDE` env knob so any site can be reverted in seconds without a redeploy. Monitor for 1 week:
- Resume parser: watch `is_valid_name` rejection rate
- PDF→HTML formatter: spot-check 20 resume previews for layout regressions
- Years recheck: watch the override-triggered rate (should not change materially)
- Title extractor: watch length-validator rejection rate

### Phase 2 — A/B validation harness + selective downgrades (1–2 weeks)
Build a thin "shadow mode" wrapper: for each NEEDS_AB_TEST call, run mini in parallel with flagship, log both outputs, ship flagship's. Collect 100+ samples per site, then have a recruiter (or YOU) judge the comparisons blind. Flip only the sites that pass.

### Phase 3 — Quarterly re-audit
Re-run this audit every 90 days against fresh telemetry. Catch new call sites added by future features and recheck whether mini's quality has improved enough to flip more T2 → T1.

---

## 5. Risk register

| Risk | Mitigation |
|---|---|
| Mini regresses on a downgraded call site without us noticing | Phase 0 telemetry + 1-week monitoring window per site + env-var revert switch |
| A/B harness leaks doubled cost during validation | Cap shadow-mode at 100 samples per site, then auto-disable |
| Recruiter notices tone difference on candidate-facing emails | Those calls (#7, #18) stay KEEP_FLAGSHIP — never downgraded |
| Volume estimates are off (savings overstated) | Phase 0 telemetry replaces estimates with truth before Phase 1 commits |
| OpenAI changes pricing mid-rollout | Telemetry tracks actual $ regardless; thresholds are relative not absolute |

---

## 6. What this audit does NOT cover (future work)

1. **Per-customer cost dashboard** (you mentioned this) — parked for after Phase 1 ships and we have telemetry. Becomes trivial once Phase 0 lands.
2. **Prompt compression** — many flagship prompts could be shortened 30–50% with no quality loss (e.g., the action-type catalog repeated in #19 and #20). Separate audit, larger build.
3. **Caching** — embeddings and classifier outputs for identical inputs are not cached; ~5–15% additional savings possible.
4. **Embedding-tier audit** — separate exercise, low priority.
5. **Batch API** — async non-time-sensitive calls (audit logs, fuzzy-dup background scans) could use OpenAI Batch API at 50% discount. Worth ~$50–100/mo.

---

## 7. Recommended next step

Build **Phase 0 + Phase 1 as one Power task** (~6–8 hours). Phase 0 alone is low-value without Phase 1 acting on it; bundling them ships the savings AND the proof-it-saved-them telemetry in one deploy.

If you approve, I'll write the project-task plan with full scope, acceptance criteria, regression test list, and rollback procedure.

---

*Raw per-call-site characterization is in `AI_COST_AUDIT_RAW.md` (36 sections, full prompt/output/visibility/tier reasoning).*
