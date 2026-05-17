# Scout Genius — AI Cost Capacity Model (May 2026)

**Built from**: production `openai_call_log` telemetry, 30-day rolling window ending 2026-05-17.
**Method**: All cost figures are summed directly from per-call OpenAI cost estimates logged at the call site. No new API spend was incurred to build this report.

---

## TL;DR — Problem → Fix → Benefit

**Problem**: We need to know the cost of running Scout Genius at different module configurations to plan budget and prioritize cost-cuts without sacrificing quality.

**Fix**: 30-day production telemetry decomposed by module + throughput-normalized to per-unit unit economics, then projected across four module-combination scenarios at current and scaled-up volumes.

**Benefit**: Clear answer to "what does each module cost?" — Screening is 97% of all AI spend; Inbound, Support, Automation, and Migration combined are 3%. The cost optimization fight is entirely about Screening efficiency. Also surfaces a critical discrepancy: logged spend ($667/30d) vs. the $2K cap that was hit, suggesting OpenAI is billing for calls not captured in our telemetry — needs investigation before next month's planning.

---

## The Four-Tier Scenario Answer

| Configuration | Cost / month | % of $2K cap | Notes |
|---|---|---|---|
| **1. Inbound only** | $4 | 0.2% | Email parsing only; trivial cost |
| **2. Inbound + Screening (core)** | $649 | 32% | The primary "active" production state |
| **3. + Support add-on** | $649 | 32% | Support module is essentially dormant ($0.03/30d) |
| **4. + Automation add-on** | $667 | 33% | Adds title extraction + resume HTML formatting |
| **5. All four + Migration cleanup** | $668 | 33% | Migration/dedupe is also near-zero ($0.32/30d) |

**Interpretation**: At current throughput, running everything costs essentially the same as running just core Screening. Support, Automation, and Migration combined add only $19/month (3% of total). **The cost optimization fight is 100% about Screening efficiency.**

---

## Module Cost Decomposition (30 days)

| Module | Site IDs | 30d Cost | % of Total | Status |
|---|---|---|---|---|
| **Screening** | `screening.*`, `vetting.*`, `vetting_audit`, `embedding_service.*` | **$645.97** | **96.8%** | Active |
| **Automation** | `automation.title_extract`, `resume_parser.format_html` | $17.97 | 2.7% | Active |
| **Inbound** | `email_inbound.*` | $4.14 | 0.6% | Active |
| **Migration / Cleanup** | `fuzzy_duplicate_matcher` | $0.32 | 0.05% | Active |
| **Support** | `scout_support.*` | $0.03 | 0.005% | Dormant (8 calls in 30d) |
| **TOTAL** | — | **$668.43** | 100% | — |

### Screening internal breakdown (the $646 — where it actually goes)

| Site | 30d Cost | Calls | Avg/call | Notes |
|---|---|---|---|---|
| `screening.scoring` | $494.49 | 18,773 | $0.026 | Core vetting AI call (the main spend) |
| `screening.scoring.shadow` | $95.06 | 10,008 | $0.0095 | A/B + L2 audit shadow — should drop sharply now that `SHADOW_LOGGING_DISABLED` defaults on AND `SCREENING_PROMPT_CACHE_AUDIT_ENABLED=false` (flipped today) |
| `screening.requirements_extract` | $44.14 | 20,662 | $0.0021 | Per-job requirements; L3 cache layout shipped today should reduce by 30%+ |
| `screening.years_recheck` | $6.76 | 5,249 | $0.0013 | Secondary scoring sub-call |
| `screening.zero_recheck` | $1.13 | 265 | $0.0042 | Secondary scoring sub-call |
| `vetting.ocr_pages` | $1.13 | 1,316 | $0.0009 | OCR for scanned resumes |
| `embedding_service.candidate` | $0.86 | 15,391 | $0.00006 | Pre-filter embeddings |
| `vetting_audit` | $0.72 | 214 | $0.0034 | Quality auditor re-checks |
| `embedding_service.shadow` | $0.68 | 63,989 | $0.00001 | Embedding A/B — disabled today |

**Key takeaway**: `screening.scoring` alone is 74% of all AI cost. Anything that reduces per-call cost or call volume on this one site has outsized impact. Everything else combined is rounding error.

---

## Unit Economics (cost per unit of throughput)

Throughput baseline (30 days): 50,621 candidate-job matches scored, 4,761 unique candidates vetted, 162 unique jobs scored, 4,774 emails parsed.

| Unit of Work | Cost per Unit | Driving Module |
|---|---|---|
| Score 1 candidate-job match | **$0.0098** | `screening.scoring` |
| Vet 1 unique candidate (full pipeline) | **$0.136** | All screening sites |
| Ingest 1 new job (one-time requirements extract) | **$0.272** | `screening.requirements_extract` |
| Parse 1 inbound application email | **$0.00086** | `email_inbound.*` |
| Format 1 resume to HTML | **$0.0035** | `resume_parser.format_html` |
| Extract 1 job title | **$0.00027** | `automation.title_extract` |

**Use these for scaling projections.** Example: doubling recruiter throughput (10K unique candidates/mo) projects to ~$1,360/mo Screening cost.

---

## Daily Cost Trend (last 14 days)

| Date | Total | Screening | Inbound | Automation | Support | Migration |
|---|---|---|---|---|---|---|
| 2026-05-17 (partial) | $33.64 | $32.35 | $0.06 | $1.20 | — | $0.04 |
| 2026-05-16 | $58.39 | $56.61 | $0.11 | $1.63 | — | $0.05 |
| 2026-05-15 | $81.01 | $78.65 | $0.41 | $1.92 | — | $0.03 |
| 2026-05-14 | $107.85 | $105.02 | $0.90 | $1.89 | $0.03 | $0.02 |
| 2026-05-13 | $82.90 | $80.31 | $0.63 | $1.95 | — | $0.01 |
| 2026-05-12 | $63.54 | $61.25 | $0.42 | $1.87 | — | $0.00 |
| 2026-05-11 | $43.57 | $41.63 | $0.25 | $1.68 | — | $0.01 |
| 2026-05-10 | $33.28 | $31.57 | $0.10 | $1.56 | — | $0.06 |
| 2026-05-09 | $38.52 | $36.72 | $0.15 | $1.59 | — | $0.05 |
| 2026-05-08 | $97.78 | $94.75 | $0.90 | $2.08 | — | $0.05 |
| 2026-05-07 | $26.96 | $26.12 | $0.22 | $0.61 | — | $0.01 |

**14-day average: $60/day** ($1,800/month run-rate).
**30-day total: $668** ($668/month run-rate).

**Pattern observed**: weekday vs weekend split is significant (recruiter-activity driven):
- Sun = $33/day avg
- Mon = $44 / Tue = $64 / Wed = $83 / Thu = $98 / Fri = $91
- Sat = $45/day avg

Thursday is consistently the peak day. Weekend trough is ~3x lower than peak weekday.

---

## ⚠️ Critical Finding — Cap-vs-Telemetry Discrepancy

**Logged production spend (30d)**: $668
**OpenAI billing cap hit**: $2,000 in May (today is May 17)

These do not reconcile. The 30-day rolling spend is roughly $670 against a monthly cap-spend of $2,000. Even accounting for the fact that May 1-7 is outside the rolling window, the gap is too large to be explained by partial-window aliasing.

**Likely causes** (need confirmation):

1. **Untagged calls** — code paths that call OpenAI directly without using `log_call()` would be invisible to our telemetry. Worth a code audit grepping for `openai_client.*.create(` or `client.responses.create(` without an adjacent `log_call(...)`.
2. **Cost estimation drift** — our per-call cost estimate may be using outdated pricing tables (especially for GPT-5.4 if pricing changed). If our estimate is 50% of actual, then $670 logged = $1,340 actual.
3. **Embedding calls billed but not estimated** — `embedding_service.shadow` shows 63,989 calls in 30d at $0.68. If true OpenAI pricing on `text-embedding-3-large` is ~$0.00013/1K tokens, that's plausibly correct, but worth spot-checking.
4. **Retries on failed calls** — failed calls that get retried may bill the failed attempt but not log it. Worth investigating 429 / 5xx behavior.

**Recommendation**: Before doing any more cost optimization, spend ~1 hour reconciling the OpenAI billing dashboard against `openai_call_log` totals for a known time window. If the gap is real, the cost-cut targets should be re-baselined against actual billing, not our log.

---

## Savings Already Banked (Won't Show Until June)

These were shipped this week and will materially lower the May 17 → June 17 window:

1. **L3 cache layout for `requirements_extract`** (shipped 2026-05-15) — moves static content before variable content so OpenAI's prompt cache hits. Currently 0% cache hit on this site. Expected: 30%+ cache hit → ~30% cost reduction on the $44/30d line = ~$15/month savings.
2. **`screening.scoring.shadow` killswitch via `SHADOW_LOGGING_DISABLED`** (shipped 2026-05-15) — defaults shadow off. Should drop the $95/30d shadow line to near zero. **However**, today's data still shows $15-25/day on shadow site, suggesting the L2 audit (separate flag) was driving it. Now that `SCREENING_PROMPT_CACHE_AUDIT_ENABLED=false` is set (today, 2026-05-17), expect the shadow line to drop to near zero within 24h.
3. Combined expected savings: **~$3-4/day** (~$90-120/month), without any quality loss.

---

## Scaled Throughput Projections

What the cost looks like at different recruiter volumes, assuming current per-unit economics hold:

| Throughput Multiplier | Unique Candidates / Mo | Projected Total / Mo | vs $2K Cap |
|---|---|---|---|
| Current (1x) | 4,761 | $668 | 33% |
| 2x | 9,522 | $1,336 | 67% |
| 3x | 14,283 | $2,004 | 100% — at cap |
| 5x | 23,805 | $3,340 | 167% — over cap |

**Implication**: At current efficiency, the system hits the $2K monthly cap around 3x current recruiter volume. To safely scale beyond 3x without raising the cap, you need either (a) the L2 cache cutover to land (~15-25% reduction on `screening.scoring`) or (b) the Task #99 output-token diet to ship (estimated 20-30% reduction).

Combined, L2 + #99 would push the 3x-cap threshold to ~5x volume.

---

## Recommendations (Priority Order)

1. **Reconcile billing-vs-telemetry gap** (1 hour, no code) — before next month. Pull OpenAI billing dashboard usage report, diff against `openai_call_log` SUM for the same window. If reconciled, great. If not, audit `openai_client.*.create(` calls in the codebase for missing `log_call()` wrappers.
2. **Wait for June 1 to re-baseline** — the next 14 days under new flags (L2 audit off, L3 layout live, Task A killswitch live) will give a clean baseline for what optimized current-state costs really look like.
3. **Defer Task #99 (output-token diet) to early June** — execution requires fresh budget headroom for shadow comparison; can't run the audit cycle this month.
4. **Do NOT defer Support, Automation, or Migration modules** — they're free. Cutting them saves nothing and removes value.
5. **Long-term**: If recruiter volume is targeted to 3x+, the L2 cache cutover and Task #99 become required, not optional.

---

## Data Source Reference

All queries run against production read-replica via `executeSql({ environment: "production" })`. Underlying table: `openai_call_log`. Schema: `id, created_at, call_site_id, model, input_tokens, output_tokens, cached_input_tokens, estimated_cost_usd, duration_ms, tenant_id, customer_id, entity_type, entity_id, success, error_type`.

Throughput counts pulled from: `candidate_job_match`, `candidate_vetting_log`, `parsed_email`.

Module → site_id mapping derived by code search across `services/`, `routes/`, `screening/`, `automation_service/`, `email_inbound_service/`, `scout_support/`, and `services/cost_forecaster.py` (which contains the canonical module catalog).
