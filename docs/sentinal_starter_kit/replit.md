# Sentinal Solutions — Candidate Authenticity & Fraud-Signal Layer

## Overview
Sentinal Solutions is a standalone, ATS-agnostic authenticity layer that scores candidate applications against a battery of non-invasive verification signals (public-web cross-checks, content-fingerprinting, contact-info validation, formatting/consistency analysis, document-tampering forensics) and produces a single **Red / Amber / Green** confidence light + an evidence trail + an LLM-generated explanation summary + a recruiter-question playbook.

Sentinal is the **trust layer**, complementary to but independent of any *fit* screening tool. It works with Bullhorn, Greenhouse, Bamboo, Taleo, Workday, Lever, etc. The flagship product blueprint lives at `docs/Sentinel_Solutions_Blueprint.md` — agents working on this project must read it first.

- **Marketing site:** `sentinalsolutions.ai`
- **Customer portal:** `app.sentinalsolutions.ai`
- **Commercial model:** Flat monthly per-tenant subscription, unlimited candidates within fair-use ceiling.

## User Preferences

> ⚠️ These preferences apply to **every interaction** — building, planning, debugging, answering questions, post-deploy reports. Honor them on message #1 and every message thereafter.

Preferred communication style: Simple, everyday language. Avoid jargon unless the user has demonstrated technical depth on that thread.

Deployment workflow: Always confirm deployment requirements at the end of any changes or updates. State clearly whether deploy is required and, if so, what the post-deploy verification plan is.

Development Approval Process: Before executing any development task, always provide a "stack recommendation" including:
  - Autonomy level (Economy/Power)
  - Brief rationale for the choice
  - Wait for user approval before proceeding

Task Plans: Every project task plan must include the recommended autonomy level (Economy/Power) and a one-line rationale at the top, so the user can set the correct mode before the task starts.

TL;DR Required: Whenever the agent produces a thorough output (analysis, recommendation, multi-step plan, build summary, post-deploy report, or anything substantial), it must lead with — or include up front — a TL;DR section using the **Problem → Fix → Benefit** pattern (or equivalent compact summary for non-build outputs). The TL;DR comes BEFORE the deep-dive so the user can grasp intent and direction without reading everything. Apply this to single tasks and multi-task batches alike.

Source of Truth: GitHub repository (KyleRoots/Sentinal-Solutions or successor repo) — `main` branch. The product blueprint at `docs/Sentinel_Solutions_Blueprint.md` is the authoritative product spec; this file (`replit.md`) is the authoritative agent-communication contract.

Dev Admin Credentials: TBD — to be set during initial scaffolding.

## System Architecture

> 🚧 To be populated as the system is built. Refer to `docs/Sentinel_Solutions_Blueprint.md` sections 6 (Architecture), 7 (Tech Stack Recommendation), 8 (Data Model), and 13 (Phased Build Roadmap) for the planned design.

### Planned Foundations (from blueprint)
- **Multi-tenant SaaS** — strict tenant isolation at the data layer
- **API/webhook ingest** — accept candidate records from any ATS
- **Signal library** — modular, weighted, severity-tiered (see blueprint sections 4 & 5)
- **R/A/G scoring engine** — composite score with hard-signal floors (see blueprint section 9)
- **Recruiter UI** — one number, three colors, evidence drill-down, LLM explanation, suggested-questions playbook (see blueprint section 10)
- **Compliance posture** — non-biometric, non-decisioning, signals-only (see blueprint section 12)

## External Dependencies

> 🚧 To be populated as the system is built. Anticipated:
- **Python Libraries:** Flask (or FastAPI), SQLAlchemy, Alembic, OpenAI, requests, BeautifulSoup4
- **External Services:** PostgreSQL, OpenAI, contact-info validation API (TBD), public-web search API (TBD)
- **AI Models:** OpenAI GPT-class models for explanation summaries and recruiter-question playbooks
