---
name: Qualified continuity Sep 23 2026
description: Open decisions and live state for Qualified Staffing so a new laptop or agent can continue without re-litigating finished work.
---

# Qualified continuity (Sep 23 2026)

**Tenant:** `JobPulse-Qualified` (`SCOUT_TENANT=qualified_staffing`), Railway project `scout-genius`.

## Shipped / live (do not re-propose)

- **Talent Platform blank source** (Sep 24): owner CorporateUser **191** with blank `source` is filled as **Corporate Website** (`tasks/talent_platform_source.py`). Origin confirmed: general apply form bouncing into Talent Platform. Do not overwrite a non-blank source. Do not treat these as Indeed.
- Blank Candidate `source` fill on **email-inbound duplicates** only (`cb421b01` / `90f851c4`). Existing sources untouched.
- Indeed tearsheet native publish on Qualified: tearsheet **2**, `INDEED_TEARSHEET_PUBLISH_ENABLED=true`, private label `51284`. Publish + unpublish both healthy as of Sep 23 (e.g. published `79365`; unpublished `50`; `pending_unpublish` empty).
- ZipRecruiter → `apply@q-staffing.com`: many notifications arrive **without résumé attachments** (`hasAttachments=false`; link-only HTML). Pipeline still creates/matches candidates from subject. A later resend **with** a real PDF should duplicate-match and enrich (file upload + blank fields + Resume pane); work history/education rows are not re-added on duplicates.

## Ops pointers

- Mailbox pull + Indeed inbound remap run on Qualified; remap currently finds `source:Indeed` backlog empty because Talent Platform rows often have blank Source (different shape than Myticas native Indeed).
- AskToAct MCP in this workspace is **Myticas** Bullhorn, not Qualified. Qualified checks go through JobPulse-Qualified Railway / Qualified Bullhorn REST.
