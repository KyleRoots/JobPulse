# Bootstrap Instructions for the First Agent

> **Read this before doing anything else.** This file exists to give the very first agent on a fresh Sentinal Solutions Replit project a precise, ordered checklist so the project starts on rails. Delete this file once the bootstrap is complete (it has served its purpose).

## Step 0 — Confirm Auto-Loaded Context

You should already have `replit.md` in your context (Replit auto-loads it every turn). If you don't, stop and tell the user "I don't see `replit.md` in my context — please confirm the starter kit was unpacked correctly."

## Step 1 — Read the Blueprint

Read `docs/Sentinel_Solutions_Blueprint.md` end-to-end (~25 min for a human; instant for you). Pay particular attention to:

- **Section 0** — Working Conventions. These are the user's communication preferences; honor them on every message.
- **Section 1** — Executive Summary. Understand what Sentinal IS and (just as important) what it ISN'T.
- **Section 13** — Phased Build Roadmap. This dictates what gets built first.
- **Section 15** — "What the New-Project Agent Needs to Do First." This is your specific marching orders.

## Step 2 — Greet the User

Per the working conventions, lead with a TL;DR. A good first-message structure:

> **TL;DR**
> - I've read the Sentinal Solutions blueprint and your working conventions.
> - I understand: [one-line summary of what Sentinal is]
> - I propose we start with: [Phase 1 or whatever the user prioritizes from blueprint section 13]
> - **Stack recommendation:** [Economy or Power] — [one-line rationale]
> - Awaiting your approval to proceed.

## Step 3 — Wait for Approval Before Building

Per the development approval process: do NOT start coding until the user has approved both the autonomy mode AND the scope. Even on a fresh project. Especially on a fresh project.

## Step 4 — First Real Build Task

Once approved, the most likely starting point per blueprint section 13 (Phase 1) is one of:
- The data model skeleton (blueprint section 8)
- The signal library scaffold (blueprint section 4)
- The R/A/G scoring engine stub (blueprint section 9)
- The recruiter UI mockup (blueprint section 10)

The user will tell you which. Don't guess.

## Step 5 — Establish Source of Truth

Confirm with the user:
- The GitHub repository name and URL for this project
- That `main` is the working branch
- Whether they want any branch-protection or PR-based workflow from day one

Update `replit.md` Source of Truth line with the actual repo URL once confirmed.

## Step 6 — Set Dev Admin Credentials

The starter `replit.md` says "Dev Admin Credentials: TBD". Once you scaffold an admin/auth system, ask the user for the dev admin username/password convention (likely the same pattern as Scout Genius: `admin` / `<ProductName><Year>!` or similar) and update `replit.md`.

## Step 7 — Delete This File

Once steps 1-6 are complete, delete `docs/sentinal_starter_kit/BOOTSTRAP.md`. The starter kit folder itself can be flattened (move files to root) or kept as a docs subfolder — your call with the user. The bootstrap file is single-use.

---

## Standing Ground Rules (always-on)

| Rule | Source |
|---|---|
| Lead every thorough output with a TL;DR (Problem → Fix → Benefit) | Blueprint § 0, replit.md |
| Stack recommendation (Economy/Power) before any build task | replit.md |
| Confirm deployment requirements at the end of any change | replit.md |
| Plain language, no jargon unless user shows technical depth | replit.md |
| Sentinal does NOT auto-reject or auto-qualify candidates — it informs the recruiter | Blueprint § 1, § 12 |
| Sentinal is non-biometric, non-decisioning — DO NOT integrate facial recognition, ID verification, or hard-identity APIs | Blueprint § 1, § 12 |
| Multi-tenant from day one — never store cross-tenant data in shared scope | Blueprint § 6 |
