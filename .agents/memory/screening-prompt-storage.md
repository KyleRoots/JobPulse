---
name: Screening prompt lives in DB, not just the file
description: Where the live global screening / clearance instructions actually come from, and how a change reaches prod.
---

The user-facing "Global Screening Instructions" (clearance/work-auth inference rules, etc.) are stored in the DB as `VettingConfig.global_custom_requirements`. `config/global_screening_prompt.txt` is only the **seed** loaded at seeding time.

**Why:** editing the file alone does NOT change production behavior — the live value is the DB row, which recruiters/admins can also have edited via `/vetting/settings`.

**How to apply:** to change prod prompt wording, either (a) edit the field in `/vetting/settings` (takes effect immediately, no republish, since it's DB config), or (b) edit the seed file for parity AND re-seed. The agent is read-only on prod DB, so for prod changes hand the user the exact find-and-replace text to paste into the settings field. Keep the seed file in sync so future re-seeds don't reintroduce old wording.
