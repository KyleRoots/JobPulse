---
name: Qualified continuity Sep 23 2026
description: Open decisions and live state for Qualified Staffing so a new laptop or agent can continue without re-litigating finished work.
---

# Qualified continuity (Sep 23 2026)

**Tenant:** `JobPulse-Qualified` (`SCOUT_TENANT=qualified_staffing`), Railway project `scout-genius`.

## Stay put (do not implement unless Kyle asks)

- **Talent Platform API User** (CorporateUser **191**, username `qualifiedstaffing.rest.able`) creates some New Lead candidates. Scout did **not** create them (no `ParsedEmail`). **Source is often blank.** Do **not** assume Indeed and do **not** ship a Talent Platform remap/enrich until a reliable origin signal is confirmed.
- Examples reviewed: Aaron Cheatham `3337910` (has file/description; blank Source/company), William Copeland `3337907` (thin; no file).

## Shipped / live (do not re-propose)

- Blank Candidate `source` fill on **email-inbound duplicates** only (`cb421b01` / `90f851c4`). Existing sources untouched.
- Indeed tearsheet native publish on Qualified: tearsheet **2**, `INDEED_TEARSHEET_PUBLISH_ENABLED=true`, private label `51284`. Publish + unpublish both healthy as of Sep 23 (e.g. published `79365`; unpublished `50`; `pending_unpublish` empty).
- ZipRecruiter → `apply@q-staffing.com`: many notifications arrive **without résumé attachments** (`hasAttachments=false`; link-only HTML). Pipeline still creates/matches candidates from subject. A later resend **with** a real PDF should duplicate-match and enrich (file upload + blank fields + Resume pane); work history/education rows are not re-added on duplicates.

## Ops pointers

- Mailbox pull + Indeed inbound remap run on Qualified; remap currently finds `source:Indeed` backlog empty because Talent Platform rows often have blank Source (different shape than Myticas native Indeed).
- AskToAct MCP in this workspace is **Myticas** Bullhorn, not Qualified. Qualified checks go through JobPulse-Qualified Railway / Qualified Bullhorn REST.
