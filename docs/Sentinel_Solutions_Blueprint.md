# Sentinal Solutions — Candidate Authenticity & Fraud-Signal Layer
## Product Blueprint (v0.2 · April 30, 2026)

> A standalone, ATS-agnostic add-on that scores candidate authenticity and surfaces a **Red / Amber / Green** signal alongside any screening output. Sentinal Solutions does **not** replace the recruiter's judgment — it equips the recruiter to "tread carefully" or "proceed confidently" before they invest time in a conversation.

> **Domains.** Marketing site: `sentinalsolutions.ai` · Customer portal: `app.sentinalsolutions.ai`

---

## 0. Working Conventions (User Preferences)

These conventions apply to **every interaction** on the Sentinal Solutions product — whether building, planning, answering questions, debugging, or producing analysis. Any agent (Replit Agent, Codex, Claude, future tools) picking up this blueprint must honor them.

| Convention | Rule |
|---|---|
| **TL;DR Required** | Every thorough output (analysis, recommendation, multi-step plan, build summary, post-deploy report, architectural review, or anything substantial) **must lead with — or include up front — a TL;DR section** using the **Problem → Fix → Benefit** pattern (or an equivalent compact summary for non-build outputs). The TL;DR comes BEFORE the deep-dive so the user can grasp intent and direction without reading everything. Applies to single tasks and multi-task batches alike. |
| **Stack Recommendation Before Build** | Before executing any development task, provide a **stack recommendation**: autonomy level (Economy or Power) + a one-line rationale. Wait for explicit user approval before proceeding. |
| **Task Plans Lead with Mode** | Every project task plan must include the recommended autonomy level (Economy/Power) and a one-line rationale at the top, so the user can set the correct mode before the task starts. |
| **Deployment Confirmation** | At the end of any change or update, confirm whether deployment is required and, if so, what the deploy plan is. |
| **Plain Language** | Communicate in plain, everyday language. Avoid jargon unless the user has demonstrated technical depth on that thread. |
| **Source of Truth** | The GitHub repository associated with this product (or its successor repo if/when Sentinal spins out from Scout Genius) is the source of truth. The `main` branch wins ties. |

**Why these are baked into the blueprint, not just the agent's memory:** working conventions live with the product so they survive across agents, sessions, and even tool migrations. A new agent reading this blueprint cold should immediately understand how the user wants to be communicated with.

---

## 1. Executive Summary

**Problem.** Recruiters across every ATS (Bullhorn, Greenhouse, Bamboo, Taleo, Workday, Lever) are flooded with applications where a meaningful percentage are not what they appear to be — agency-spammed resumes recycled under different names, the same person applying under multiple identities, fabricated work history, fake LinkedIn profiles, disposable email addresses, contact info that doesn't connect to a real human. Existing screening tools score *fit*; they don't score *authenticity*.

**Solution.** Sentinal Solutions is a thin, vendor-agnostic authenticity layer that:
1. Ingests a candidate record (resume + contact info + claimed identity links) from any ATS via API or webhook.
2. Runs a battery of **non-invasive verification signals** — public-web cross-checks, content-fingerprinting, contact-info validation APIs, formatting/consistency analysis, and document-tampering forensics.
3. Produces a single **Red / Amber / Green** confidence light + an evidence trail of every signal that fired, plus an LLM-generated **explanation summary** and **recruiter-question playbook** for every Amber/Red result.
4. Posts the result back to the ATS (note, custom field, or widget) and keeps the data multi-tenant-isolated.

**What Sentinal Solutions is NOT.**
- Not facial recognition, biometric, or hard-ID verification (Onfido / Persona / Stripe Identity territory). Deliberately avoided to sidestep consent/regulatory complexity.
- Not a hiring decision system. Sentinal Solutions never auto-rejects, never auto-qualifies. It informs.
- Not coupled to any single ATS or upstream product. The primary identity is "the authenticity layer that works with any ATS."

**Commercial wedge.** Flat-fee unlimited per-tenant subscription. The pitch is "no-brainer fraud insurance" — pay one predictable monthly number, get authenticity signals on every candidate forever.

---

## 2. Positioning & Brand

| Dimension | Decision |
|---|---|
| **Name** | Sentinal Solutions |
| **Domains** | `sentinalsolutions.ai` (marketing) · `app.sentinalsolutions.ai` (portal) |
| **Tagline (working)** | "Know who's really applying." |
| **Audience** | Recruiters, sourcers, hiring managers, talent ops leaders |
| **Buyer** | Director of Talent / VP People / Agency Owner |
| **Pricing model** | Flat monthly tenant fee, unlimited candidates within reason (soft fair-use ceiling, e.g. 250k checks/mo) |
| **Relationship to fit-screening tools** | Sentinal Solutions is the *trust* layer; it complements but does not require any specific *fit* screening tool. The two stack naturally but neither depends on the other. |

**Brand voice.** Watchful without aggression. Sentinal Solutions supports the recruiter's decision — never claims to block bad candidates, never claims to verify identity. The product *surfaces signals*; the recruiter *decides*.

---

## 3. The R/A/G Model

The single most important UX decision: **one number, three colors, a click for evidence — plus a plain-English summary and a short list of suggested questions for the recruiter to ask the candidate.**

| Light | Meaning | Recruiter action |
|---|---|---|
| 🟢 **Green** | No meaningful authenticity signals fired. Resume content, contact info, and public footprint all line up. | Proceed normally. |
| 🟡 **Amber** | One or more soft signals fired (e.g. LinkedIn name partial-match, formatting irregularities, low-confidence contact validation). Worth a glance before a call. | Read the explanation. Use the suggested questions on the screening call to confirm or dispel the flagged signals. |
| 🔴 **Red** | Multiple hard signals fired or a single high-severity signal (e.g. duplicate-fingerprint match across tenants, disposable email + invalid phone, claimed LinkedIn URL doesn't exist, document tampering detected). | Investigate before investing time. Use the suggested questions to probe directly. Sentinal Solutions does NOT recommend rejection — many red lights have innocent explanations, and the candidate deserves a chance to clarify. |

**Scoring logic (v1).** Each signal carries:
- A **weight** (0.0–1.0) calibrated against historical fraud cases
- A **confidence** (0.0–1.0) on whether the signal definitively fired
- A **severity tier** (soft | moderate | hard)

`composite = Σ(weight × confidence)` with hard signals having a hard floor:
- 0 hard signals + composite < 0.25 → 🟢
- 1 hard signal OR composite 0.25–0.60 → 🟡
- 2+ hard signals OR composite > 0.60 → 🔴

The thresholds are tunable per-tenant in v2. Rationale: an agency placing contractors will tolerate more amber than a Fortune-500 corporate recruiter.

**Explanation + question generation (new in v0.2).** For every Amber and Red result, the engine makes a single LLM call (cheap model, ~$0.001 per call) that takes the fired signals + their evidence and produces:
- A 2–4 sentence **plain-English summary** of why the result is Amber or Red.
- 2–4 **suggested screening questions** the recruiter can ask the candidate to confirm or dispel the flagged signals.

Both are stored alongside the evidence trail and rendered inline in the widget. Green results skip this step entirely (no LLM cost, no UI clutter).

---

## 4. MVP Signal Library

These five signals are the launch set. They were chosen because they (a) cover the biggest fraud archetypes seen in practice, (b) are technically achievable without manual intervention, and (c) don't depend on any biometric or hard-ID data.

### 4.1 LinkedIn Cross-Check
**Goal.** Confirm the human on the resume actually exists on LinkedIn and the public profile lines up with the claimed work history.

**Method.**
1. If the resume includes a LinkedIn URL → fetch the public profile, extract name, current title, employment history, education, location.
2. If no LinkedIn URL → search by `(name, current employer, location)` triplet via a search API (e.g. SerpAPI, Bing Web Search). Take the top match if exactly one strong candidate; otherwise mark "ambiguous."
3. Compare: name match, current title alignment, employment-history overlap (last 2 jobs), education school match.

**Signals fired.**
- 🔴 LinkedIn URL on resume returns 404 / private / removed → likely fabricated link
- 🟡 No LinkedIn presence found at all for a claimed senior professional → unusual
- 🟡 LinkedIn name matches but employment history shows a totally different career → could be wrong John Smith OR resume is fabricated
- 🟢 Strong cross-match on name + last employer + tenure dates within ±6 months

**Failure mode mitigation.** Common-name handling: when 5+ plausible matches exist, fall back to "ambiguous" rather than guessing. Always show the recruiter the top 3 candidate profiles in the evidence trail so they can confirm visually.

### 4.2 Resume Fingerprint / Duplicate Detection
**Goal.** Detect when the *same resume* (or near-clones) is being submitted across multiple identities or tenants — a classic agency-spam signature.

**Method.**
1. Extract resume text → normalize → compute multiple fingerprints:
   - **Structural fingerprint**: section ordering, bullet count per section, heading patterns, font usage from PDF metadata
   - **Content fingerprint**: SimHash or MinHash over normalized text body
   - **Embedding fingerprint**: dense vector via OpenAI `text-embedding-3-large` (chosen for high recall on near-duplicates and strong general-purpose semantic similarity)
2. Store in a tenant-scoped + cross-tenant fingerprint DB.
3. On new submission: search for near-duplicates by all three.

**Signals fired.**
- 🔴 Same content fingerprint, *different name and contact info*, within tenant or across tenants → agency spam or identity fraud
- 🟡 Very similar structural fingerprint (likely same template) appearing >5 times in 30 days under different names → agency batch
- 🟡 Same candidate name + contact, *different content fingerprint* → suspicious resume rewriting (often title-inflation)

**Privacy guardrail.** Cross-tenant matching uses only fingerprints (irreversible hashes + embeddings), never the underlying PII. The cross-tenant pool is opt-in at the tenant-admin level and can be disabled.

### 4.3 Multi-Format / Multi-Title Inconsistency Flag
**Goal.** Detect candidates who submit different resumes — different formatting, different job titles, different stated experience years — when applying to multiple jobs.

**Method.**
1. Within a tenant, on each submission, look up prior submissions by the canonical email/phone (post-validation).
2. For prior submissions, compute drift on: claimed total years of experience, claimed seniority level (extracted via LLM), claimed job titles for the same role/company.
3. Compare formatting & structural fingerprint.

**Signals fired.**
- 🔴 Same person, prior submission claimed 3 yrs experience, current claims 8 yrs → inflation
- 🔴 Same person, prior submission listed company X as a Junior Engineer 2020-2022, current submission lists company X as a Tech Lead 2020-2022 → fabrication
- 🟡 Same person, dramatically different structural template — could be legitimate (tailored resume) or could indicate ghost-writing service
- 🟢 Same person, consistent claims across submissions

**Why this matters.** This is the single signal most validated by recruiter intuition. Recruiters already mentally do this when they remember a candidate. Sentinal Solutions does it across a tenant's full history.

### 4.4 Contact-Info Validation
**Goal.** Verify the email and phone number on the resume are real, owned, and not throwaway.

**Method.** Integrate with established contact-validation APIs. Recommended vendors (pick 1–2 for MVP):

| Provider | Strengths | Notes |
|---|---|---|
| **NeverBounce / ZeroBounce / Hunter.io** | Email validity, MX, role-account detection, disposable-domain flagging | $0.003-0.008 per check at volume |
| **Numverify / Twilio Lookup** | Phone validity, line type (mobile / VOIP / landline), carrier, region | Twilio Lookup is the gold standard, ~$0.005-0.05 per check depending on tier |
| **Have-I-Been-Pwned API** | Whether the email appears in known breaches (proxy for "this is a real account that has existed for years" vs. throwaway) | Free for non-commercial; commercial license modest |
| **EmailRep.io** | Reputation score, age estimate, social-profile presence | Free tier, paid for volume |

**Signals fired.**
- 🔴 Email on a known disposable-domain provider (mailinator, guerrillamail, 10minutemail, etc.) → throwaway
- 🔴 Phone number is invalid / unassigned / does not connect → fabricated
- 🟡 Phone is VOIP-only (Google Voice, Skype, TextNow) → not necessarily fraud, but a soft signal especially for senior roles
- 🟡 Email exists but has no breach footprint and no social-profile association — possibly created today
- 🟢 Email is 5+ years old, multi-platform footprint, valid MX, deliverable; phone is mobile carrier in claimed region

### 4.5 Document Tampering Forensics *(new in v0.2)*
**Goal.** Detect resumes that have been recycled, edited post-hoc, or generated from a shared agency template — by inspecting the PDF's own metadata and structural fingerprints rather than its content.

**Why this is in the MVP.** This is the cheapest signal in the library to operate (zero recurring API cost — runs entirely locally on the PDF), one of the lowest-false-positive (PDF metadata is factual, not interpretive), and covers a fraud archetype no contact-validation or LinkedIn-cross-check signal touches: *the same PDF being reskinned with a different name and shipped 50 times by an agency*. It also pairs naturally with Signal 4.2 (Resume Fingerprint): the fingerprint says "this content has been seen before"; document forensics says "and here's the smoking gun — it was modified yesterday by `recruiter@bigagency.com`."

**Method.**
1. **PDF metadata extraction** — read the PDF's embedded metadata via PyMuPDF / pdfplumber:
   - `CreationDate`, `ModDate` (creation vs. last-modified timestamps)
   - `Author`, `Producer`, `Creator` fields
   - PDF version, page count, embedded font subset list
   - XMP metadata block if present
2. **Tampering heuristics** — apply a rule set:
   - `ModDate` > `CreationDate` by more than ~1 hour → file was edited after creation
   - `Author` field contains an organizational name that doesn't match the candidate (e.g. resume claims "Jane Doe" but `Author = "TempStaffSolutions HR"`)
   - `Producer` field shows a known resume-generator template (resume.io, novoresume, kickresume) but the content claims to be a custom hand-written resume
   - Font subset analysis: the PDF embeds character ranges for unused glyphs → indicates the underlying file was a template with placeholder text that's been swapped out
   - Inconsistent `CreationDate` across multiple submissions from the same purported author within a tenant
3. **Cross-reference with Signal 4.2** — if the content fingerprint is shared with a prior submission AND the metadata shows recent modification, severity escalates from amber to red.

**Signals fired.**
- 🔴 Same content fingerprint across 3+ submissions, all with identical `Author` field but different candidate names → agency template swap
- 🔴 PDF `ModDate` is within the last 24 hours AND the resume claims years-old work history with "last updated" prose → freshly-edited recycled file
- 🟡 `Author` field is an organizational name that doesn't match the candidate
- 🟡 Resume claims to be hand-crafted but `Producer` is a known template service
- 🟡 PDF version / metadata signature matches another tenant's flagged submissions (cross-tenant pool, opt-in only)
- 🟢 Metadata is consistent: `CreationDate` ≈ `ModDate`, `Author` matches candidate name, no template indicators

**Failure mode mitigation.**
- Many legitimate resumes are exported from Word → metadata is sparse or anonymous (`Author = "Microsoft Word"`). This is *not* a signal. The rules only fire when metadata reveals something *inconsistent* with the candidate's claims.
- Resumes saved as PDF from web tools (LinkedIn export, Indeed download) have predictable metadata signatures we whitelist.
- Recruiters who've personally edited a candidate's resume (a normal agency practice for legitimate placements) shouldn't trigger red — only flag when the modifying entity *isn't* a known recruiter party for that tenant.

**Cost & latency.** Pure local processing. ~50ms per resume on commodity hardware. No external API calls. No vendor risk.

---

## 5. Future-Phase Signal Library

Held out of MVP intentionally to ship fast. Each is pre-scoped with a one-line MVP integration path.

| Signal | One-liner | Phase |
|---|---|---|
| **AI-written resume detection** | Run resume through GPTZero / Originality.ai or self-hosted classifier; flag amber if probability > 70%. Note: not a fraud indicator on its own — many honest candidates use ChatGPT to format. Held out of MVP because of false-positive risk. | v2 |
| **Public web presence cross-check** | If candidate claims GitHub/StackOverflow/personal site, verify the link resolves and the activity matches claimed seniority. | v2 |
| **Geographic plausibility** | Compare claimed location vs. submission IP/timezone (where ATS exposes it). Big mismatches → amber. | v2 |
| **Sanctions / restricted-party screening** | OFAC/UK/EU lists. Often required by enterprise customers. | v3 |
| **Behavioral velocity** | Same fingerprint submitted to 50+ jobs in 24h across the cross-tenant pool → spray-and-pray flag. Requires meaningful cross-tenant pool data, so realistically v3+. | v3 |
| **Voice/video screening intake** | Optional: candidate records a 30-sec self-intro; check against claimed identity for *consistency only* (accent, claimed-name pronunciation). Stops short of biometric ID. Requires careful consent UX. | v4 — only if customer demand justifies the privacy lift |

---

## 6. Architecture

### 6.1 Topology
```
┌─────────────────────────────────────────────────────────────┐
│                       ATS layer (any)                       │
│  Bullhorn  ·  Greenhouse  ·  Bamboo  ·  Taleo  ·  Workday   │
└─────────────────────────┬───────────────────────────────────┘
                          │  webhook / REST / connector
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              Sentinal Solutions Edge (FastAPI)              │
│  · POST /v1/score          (sync — returns R/A/G in <2s)    │
│  · POST /v1/candidates     (async — queues for batch run)   │
│  · GET  /v1/candidates/:id (fetch result + evidence trail)  │
│  · Webhook callbacks to ATS when async completes            │
└────────┬────────────────────────────────────┬───────────────┘
         │                                    │
         ▼                                    ▼
┌─────────────────────┐              ┌─────────────────────┐
│  Sync Scorer        │              │  Async Worker Pool  │
│  - Cheap signals    │              │  - All signals      │
│  - In-process,      │              │  - Celery / RQ /    │
│    sub-second       │              │    APScheduler      │
└──────┬──────────────┘              └──────┬──────────────┘
       │                                    │
       └────────────────┬───────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                    Signal Engines                           │
│ LinkedIn │ Fingerprint │ Inconsistency │ Contact │ DocForensics │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│            Postgres (multi-tenant schema-per-tenant)        │
│   + pgvector for embeddings                                 │
│   + cross-tenant fingerprint pool (opt-in, anonymized)      │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 Sync vs. Async
**Both, with async as the workhorse and sync as the premium path.**

- **Sync `/v1/score`**: Returns within 2 seconds. Runs only the cheap signals (contact-info validation cache hits, fingerprint lookup, prior-submission inconsistency check, document forensics — all local or cached). LinkedIn cross-check is *initiated* but if it doesn't return in 1.5s the sync result returns "amber pending" and a webhook fires when full result is ready.
- **Async `/v1/candidates`**: ATS posts the candidate, Sentinal Solutions responds 202 Accepted with a job ID, and posts the full R/A/G + evidence trail back via webhook within 30–90 seconds.

Most ATS connectors will use async by default. Sync exists for premium customers who want inline gating in their own application form.

### 6.3 Multi-Tenancy
**Schema-per-tenant on a shared Postgres cluster** for v1. Reasons:
- Hard data isolation (a Postgres permission boundary, not a row-level filter)
- Per-tenant backups, restores, and deletion are trivial (drop schema)
- Cross-tenant fingerprint pool lives in a separate `_shared` schema with anonymized data only
- Trivial to migrate a single tenant to a dedicated DB if they outgrow shared infra

**Tenant identification.** Every API call carries an API key → resolves to a tenant ID → all queries scoped to that tenant's schema. Standard FastAPI middleware.

### 6.4 ATS Connectors
Three integration mechanisms, customers pick what they want:

1. **REST + webhook (default).** Customer's ATS posts candidate to Sentinal Solutions, Sentinal Solutions webhooks back. Works for any ATS that supports outbound webhooks (most do).
2. **Pre-built connectors** for the top ATSes. Sentinal Solutions polls the ATS, scores, writes a note + custom field back. Order of build:
   - Bullhorn (first — leverages alpha-customer integration knowledge)
   - Greenhouse (well-documented API, big TAM)
   - Workday (enterprise — slow to build but high contract value)
   - Lever, Bamboo, Taleo (long tail)
3. **Browser extension** (v2). Overlays the R/A/G widget on any ATS UI without integration. Reads candidate name + email from the DOM, calls the Sentinal Solutions API, paints the widget. Useful for customers whose ATS is on lockdown or who want to trial without IT involvement.

---

## 7. Tech Stack Recommendation

| Layer | Choice | Why |
|---|---|---|
| **Language** | Python 3.12 | Mature ecosystem for resume parsing, embeddings, LLM, web scraping; deep talent pool |
| **API framework** | **FastAPI** | Async-first, OpenAPI docs free, type-safe — the right fit for an API-first product |
| **Worker queue** | **Celery + Redis** OR **RQ + Redis** | Sentinal Solutions is API+worker dominant; in-process schedulers won't scale across replicas. Celery is the obvious choice. |
| **Database** | PostgreSQL 16 + pgvector + pg_trgm | Fingerprint search needs vector similarity; trigram for name fuzzy match |
| **ORM / migrations** | SQLAlchemy 2.0 + Alembic | Industry standard; first-class async support in SQLAlchemy 2.0 |
| **LLM** | OpenAI (GPT-5.4 for hard reasoning, GPT-4.1-mini for cheap classification + explanation generation) | Strong reasoning quality, easy to swap providers via thin abstraction layer if needed |
| **Embeddings** | OpenAI `text-embedding-3-large` | High recall on near-duplicates, 3072-dim vectors fit pgvector cleanly |
| **PDF / document parsing** | PyMuPDF (`fitz`) for metadata + text; pdfplumber as fallback | PyMuPDF gives full metadata access for Signal 4.5 (document forensics) at sub-50ms latency |
| **Web scraping** | `httpx` + `selectolax` for parsing; **Playwright** for LinkedIn (which fights scrapers) | Headless browser only when necessary; respect robots & rate limits |
| **Deployment** | Replit Reserved VM Deployment + Replit Postgres + Upstash Redis (or Replit Redis when GA) | Stay on Replit infra for ops simplicity; can graduate to Fly.io / GCP if scale demands |
| **Observability** | Sentry (errors) + Logfire or OpenTelemetry → Honeycomb (traces) | Per-signal latency tracing is critical for diagnosing slow checks |
| **Auth** | Tenant API keys (simple) for service-to-service; optional Microsoft Entra / Google SSO for the admin web UI | Standard pattern for B2B SaaS APIs |
| **Admin UI** | Server-rendered Jinja + Bootstrap initially, graduate to a React SPA only if customer demand exceeds a basic admin panel | Don't over-build the UI for an API-first product |

**Architectural posture.** Sentinal Solutions is API-first, not web-app-first. Every core capability is reachable via a clean REST endpoint before any UI is built on top. The admin UI consumes the same APIs the customer's ATS does.

---

## 8. Data Model (skeleton)

```
tenant
  id, name, api_key_hash, settings_jsonb, plan, created_at,
  cross_tenant_pool_opt_in (bool, default false)

candidate_check
  id, tenant_id, external_candidate_id, ats_type,
  email_canonical, phone_canonical, name_canonical,
  resume_text_hash, resume_content_fingerprint, resume_embedding (vector),
  resume_structural_fingerprint_jsonb,
  resume_pdf_metadata_jsonb,           -- raw metadata captured for Signal 4.5
  rag_color (enum: green/amber/red),
  composite_score (float),
  explanation_summary (text),          -- LLM-generated, only populated for amber/red
  suggested_questions_jsonb,           -- LLM-generated, only populated for amber/red
  status (enum: pending/processing/complete/error),
  created_at, completed_at

signal_result
  id, candidate_check_id, signal_name (enum),
  fired (bool), severity (enum), weight, confidence,
  evidence_jsonb,  -- includes raw API payloads, screenshots URL, comparison details
  ran_at, latency_ms, cost_cents

cross_tenant_fingerprint  (in _shared schema)
  id, content_fingerprint, structural_fingerprint_jsonb,
  embedding (vector),
  pdf_metadata_signature_jsonb,         -- anonymized PDF metadata fingerprint for Signal 4.5
  first_seen_at, last_seen_at,
  tenant_count (int),  -- how many distinct tenants have seen this
  submission_count (int)
  -- NO PII. Names, emails, phones never enter this table.

audit_log
  id, tenant_id, action, actor (api_key_id or user_id),
  candidate_check_id, payload_jsonb, ts
```

---

## 9. Scoring Engine — Calibration Strategy

Most of the engineering risk in v1 lives in **scoring calibration**: what weights and thresholds produce R/A/G colors that recruiters trust?

**Phase 0 (pre-launch).** Hand-tune weights from a small labeled set (200–500 candidates: 50 confirmed fraud, 50 confirmed legitimate, rest unknown). Goal: 95%+ recall on confirmed fraud, false-positive rate < 5% on confirmed legitimate.

**Phase 1 (early customers).** Every R/A/G result is reviewable by the recruiter, who can mark it "agree" / "disagree." That feedback feeds a per-tenant calibration model.

**Phase 2 (after 100k+ checks).** Train a gradient-boosted classifier on the per-signal outputs to learn weights instead of hand-tuning. Each tenant gets the option of a tenant-specific model trained on their feedback.

### 9.1 Explanation + Question Generation

Every Amber and Red result triggers a single LLM call (GPT-4.1-mini, ~$0.001 per call) with a structured prompt:

**Inputs:**
- The list of fired signals, each with severity, weight, confidence, and a short evidence summary
- The candidate's basic claims (current title, claimed years of experience, last employer)
- The R/A/G color and composite score

**Outputs:**
- `explanation_summary` (string, 2–4 sentences): plain-English rationale for the color
- `suggested_questions` (array of strings, 2–4 items): probing questions the recruiter can ask the candidate to confirm or dispel the flagged signals

**Example output for a Red result on document tampering + fingerprint match:**
```
explanation_summary: "This resume's content fingerprint matches three other
submissions in the last 30 days, all using different candidate names but the
same author metadata ('TalentBridge Recruiting'). The PDF was last modified
12 hours ago, suggesting the file is being recycled and re-skinned for each
submission."

suggested_questions: [
  "Can you walk me through who prepared this resume for you?",
  "I notice the PDF's author field references a recruiting agency — are you
   currently represented by them?",
  "Could you tell me about your role on the most recent project listed,
   including dates and your specific contribution?"
]
```

Green results skip this step entirely (no LLM cost, no UI clutter — green means "no flags worth talking about").

### 9.2 Explainability as Trust Currency

Even when we move to a learned model, the recruiter never sees a black-box "Sentinal Solutions says 0.73." They see the color, the explanation summary, the suggested questions, and the per-signal evidence trail. The model's internals are never the user-facing artifact. **Explainability is the product's trust currency.**

---

## 10. Recruiter UI / UX

### 10.1 The widget
The atomic deliverable. Renders inline wherever the recruiter sees the candidate.

```
┌────────────────────────────────────────────────────┐
│  Sentinal:  🟡 AMBER          [view evidence ▼]    │
│  3 of 9 signals fired                              │
└────────────────────────────────────────────────────┘
```

Click → expands to a four-part evidence panel:

```
┌─ WHY AMBER ──────────────────────────────────────────┐
│ This candidate's LinkedIn profile name matches but  │
│ the listed employment history diverges from the     │
│ resume. The PDF was modified within the last day   │
│ and the email account has minimal historical foot-  │
│ print, suggesting the file may be a freshly-pre-    │
│ pared submission rather than a long-maintained CV.  │
└─────────────────────────────────────────────────────┘

┌─ SUGGESTED QUESTIONS FOR THE CALL ──────────────────┐
│ • Could you tell me about your transition from     │
│   Acme to BigCo — the dates on your LinkedIn show   │
│   a different timeline.                             │
│ • Who prepared this resume for you?                 │
│ • Can you walk me through your role at the company  │
│   listed as your most recent employer?              │
└─────────────────────────────────────────────────────┘

┌─ LinkedIn cross-check ─────────────  🟡 SOFT FLAG ──┐
│ Profile found, name matches, but employment        │
│ history shows different employers in 2023-2024.    │
│ View profile →                                     │
└─────────────────────────────────────────────────────┘

┌─ Resume fingerprint ──────────────── 🟢 PASSED ────┐
│ Content fingerprint is unique. No duplicates       │
│ found in tenant or cross-tenant pool.              │
└─────────────────────────────────────────────────────┘

┌─ Multi-submission consistency ────  🟢 PASSED ────┐
│ No prior submissions from this candidate.          │
└─────────────────────────────────────────────────────┘

┌─ Contact validation ──────────────── 🟡 SOFT FLAG ─┐
│ Email valid but 0 breach history (~7 days old).   │
│ Phone is mobile, valid, in claimed region.         │
└─────────────────────────────────────────────────────┘

┌─ Document forensics ──────────────── 🟡 SOFT FLAG ─┐
│ PDF was last modified 18 hours ago. Author field   │
│ matches candidate name. No template indicators.    │
└─────────────────────────────────────────────────────┘
```

### 10.2 Embedding patterns
- **Bullhorn**: write to a custom Sentinal Solutions field + drop a note containing the WHY summary, suggested questions, and evidence trail
- **Greenhouse**: candidate scorecard custom section
- **Workday**: candidate review page custom widget
- **Browser extension**: floating overlay anchored to candidate name DOM element

### 10.3 Admin dashboard (per tenant)
- Recent checks with R/A/G distribution
- Per-signal fire rates (helps the tenant tune their thresholds)
- Cross-tenant fingerprint matches (if opted in)
- Cost-of-checks meter
- API key management
- ATS connector configuration

---

## 11. Commercial Model

| Item | Decision |
|---|---|
| **Tier 1** | $X/mo flat, unlimited within fair use (e.g. 50k checks/mo), top 4 ATSes, async only |
| **Tier 2** | $Y/mo, higher fair-use ceiling, sync API, browser extension, custom signal weights |
| **Tier 3 / Enterprise** | Negotiated, dedicated DB, sanctions screening, SOC2 reports, SSO, custom signal development |
| **Pilot offer** | 30-day free trial, no card required, capped at 1k checks |
| **Cross-tenant pool** | Opt-in incentive: tenants who opt in get *priority access* to new signals + a 10% discount |

Pricing ranges deliberately blank — needs market research with 5–10 prospective customers before locking in. Anchor: position against "cost of one bad hire that wastes a recruiter's afternoon" (~$200) — Sentinal Solutions should pay for itself if it prevents one wasted screening call per month.

---

## 12. Compliance, Ethics & Legal Guardrails

This is the section that, if done badly, kills the product. Decisions worth being deliberate about:

1. **Candidate consent.** Sentinal Solutions's checks are run on data the candidate already submitted to the ATS. The ATS's existing consent should cover most signals (validating contact info you provided, comparing to your prior submissions, checking your *publicly* posted LinkedIn, inspecting metadata in the file you uploaded). Each customer's ToS should be updated to disclose authenticity-screening. We provide template language.

2. **Public data only.** Sentinal Solutions never logs into LinkedIn or scrapes behind a wall. We use either LinkedIn's official APIs (limited but cleanest) or public-search-engine results (explicitly permitted). No credential-based scraping. This is non-negotiable — it's both an ethical line and a legal one (LinkedIn v. hiQ aside, this is contested territory).

3. **No biometric / hard-ID data.** Per product direction. Avoids GDPR Article 9 / BIPA / Illinois biometric laws.

4. **Right to explanation.** Under GDPR Article 22 and various state US laws, candidates may have a right to know why an automated decision affected them. Sentinal Solutions never makes the decision — the recruiter does — but tenants must be able to produce an evidence trail on request. Audit log + per-check `evidence_jsonb` + `explanation_summary` satisfies this.

5. **No protected-class signals.** The signal library deliberately excludes anything that proxies for race, gender, age, national origin, religion, disability, sexual orientation. This must be reviewed *before* shipping each new signal — formal sign-off in the build process.

6. **Cross-tenant pool consent.** Opt-in only, opt-out at any time, irreversible-hash data only. Document this in plain English in the signup flow.

7. **Right to be forgotten.** Candidate-deletion API: a tenant can submit a candidate ID and we purge all data. Cross-tenant fingerprints retained (no PII, irreversible) but the link to the original tenant is severed.

8. **SOC2 Type II target by month 9** if selling to enterprise. Drift-prevention from day one is way cheaper than retrofitting.

---

## 13. Phased Build Roadmap

Each phase ends with a *shippable* product. Don't let scope creep across phases.

### Phase 1 — Internal Alpha (weeks 1–6)
**Goal:** Run Sentinal Solutions on a friendly first-customer's existing Bullhorn data — a sister product (Scout Genius) operated by the same founding team — to prove the engine end-to-end before any external sales.

Deliverables:
- Scaffold: FastAPI app, Postgres, Celery + Redis, Sentry, basic admin UI
- Multi-tenant foundation (single tenant for alpha)
- Bullhorn connector (poll + webhook write-back)
- Signal 1: Contact-info validation (NeverBounce + Twilio Lookup)
- Signal 2: Resume fingerprint (intra-tenant only)
- Signal 5: Document tampering forensics (PyMuPDF metadata inspection)
- R/A/G scoring engine with hand-tuned weights
- Explanation + suggested-question LLM generation for amber/red
- Recruiter widget rendered in Bullhorn note + custom field
- Admin dashboard with check history + per-signal stats

**Approval gate before Phase 2:** Run on 1 month of real alpha-customer candidate flow. Compare R/A/G results to recruiter intuition on a sampled set of 200. If precision/recall on confirmed-fraud cases is above (TBD threshold), proceed.

### Phase 2 — Closed Beta (weeks 7–14)
**Goal:** 3 paying design-partner customers on Bullhorn.

Deliverables:
- Signal 3: LinkedIn cross-check (search + match logic, common-name handling)
- Signal 4: Multi-format / multi-submission consistency
- Cross-tenant fingerprint pool (opt-in)
- Sync `/score` endpoint
- Tenant onboarding flow + API key issuance
- Billing integration (Stripe)
- Pricing locked

### Phase 3 — Public Launch + Greenhouse (weeks 15–22)
- Greenhouse connector
- AI-written-resume detection signal (v2)
- Public web presence cross-check signal (v2)
- Per-tenant threshold tuning UI
- Public marketing site at `sentinalsolutions.ai`
- SOC2 Type I attestation kickoff

### Phase 4 — Scale (weeks 23+)
- Workday connector
- Browser extension
- Geographic plausibility signal
- Sanctions screening (enterprise tier)
- Per-tenant learned models

---

## 14. Open Questions / Risks Worth Flagging

These should be answered before serious build effort, not during:

1. **LinkedIn TOS risk.** We need a clean answer on whether to (a) pay for LinkedIn's official Talent Insights API (expensive, restricted), (b) rely solely on public search-engine results (cheaper, less reliable), or (c) use a third-party LinkedIn data vendor (PeopleDataLabs, Proxycurl — varies legally). This is the single biggest open legal/cost question.

2. **Cost per check.** Crude estimate per check (MVP signals only):
   - Email validation: $0.005
   - Phone validation: $0.01
   - LinkedIn lookup (via Proxycurl-class vendor): $0.05–$0.10
   - LLM calls (resume parsing + comparison + explanation generation): $0.02–$0.05
   - Document forensics: $0 (local)
   - **Total: ~$0.10–$0.20 per full check**
   At $X/mo for 50k checks, gross margin needs deliberate planning. Can the cheap signals (contact validation, fingerprint, document forensics) run on every check and the expensive ones (LinkedIn, LLM-heavy paths) only when cheap signals fire amber+? Probably yes; design for it.

3. **Calibration data scarcity.** We don't have a labeled fraud dataset to start. Plan: bootstrap from manual review of a sample of the alpha customer's historic candidates, plus published fraud archetype patterns, plus customer feedback loop.

4. **Cold start for cross-tenant pool.** With one tenant (alpha), the cross-tenant pool adds zero value. Need to articulate this honestly to early customers and price the fingerprint pool benefit as a v2 feature.

5. **What "support the recruiter's decision" means in writing.** Legal review on the product's marketing language to ensure we never claim Sentinal Solutions "verifies identity" or "prevents fraud" — both are overstated and would create liability. We *surface signals*, we don't *verify*.

6. **Alpha-customer coupling vs. independence.** Tempting to share infrastructure with the friendly first customer for early efficiency — same Postgres, same Replit account, same OpenAI key. Resist. Sentinal Solutions is its own product, its own customers, its own security blast radius. Pay the small overhead now to avoid disentangling later. New Replit project, new database, new secrets, new everything.

---

## 15. What the New-Project Agent Needs to Do First

When you spin up the new Replit project, hand the first agent this document plus the following starter prompt:

> Read `sentinal_solutions_blueprint.md`. Build Phase 1 (Internal Alpha). Recommend Power autonomy because this is foundational architecture work where rework is expensive. Before writing any code, propose:
> (a) the directory layout
> (b) the FastAPI app structure (routers, dependencies, settings)
> (c) the Celery + Redis topology
> (d) the Postgres schema for the tenant + candidate_check + signal_result tables (note: candidate_check now includes `resume_pdf_metadata_jsonb`, `explanation_summary`, and `suggested_questions_jsonb`)
> (e) the secrets you need (OpenAI key, NeverBounce key, Twilio Lookup key, Bullhorn credentials issued for the alpha-customer integration, Sentry DSN)
>
> Get user approval on each before implementing. Do not start with the LinkedIn signal — start with contact-info validation and document tampering forensics together, because they have the lowest external-dependency complexity (document forensics is fully local) and give us a complete end-to-end path (intake → score → webhook back) on day one with two of the five MVP signals already producing R/A/G output.

---

## 16. Glossary

- **R/A/G** — Red / Amber / Green confidence light. Sentinal Solutions's primary output.
- **Signal** — An individual check (e.g. "LinkedIn cross-check") that fires or doesn't.
- **Fingerprint** — An irreversible hash or vector representation of resume content used for duplicate detection. No PII.
- **Cross-tenant pool** — Opt-in shared fingerprint database that lets one tenant benefit from fraud patterns first seen by another. Anonymized.
- **Sync vs. async** — Sync = HTTP request/response in <2s, only cheap signals. Async = job queued, full result via webhook in 30–90s.
- **Tenant** — A paying customer organization. Schema-isolated.
- **Connector** — A pre-built integration to a specific ATS (Bullhorn, Greenhouse, etc.).
- **Document forensics** — Inspection of PDF metadata, structural fingerprints, and font subset patterns to detect tampering, recycling, or template-based agency spam. Signal 4.5.
- **Explanation summary** — Plain-English LLM-generated rationale for an Amber or Red result, surfaced to the recruiter alongside the color.
- **Suggested questions** — LLM-generated probing questions the recruiter can ask the candidate to confirm or dispel flagged signals. Generated for every Amber and Red result.

---

*End of v0.2 blueprint. Iterate freely.*
