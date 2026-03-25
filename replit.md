# Scout Genius™ - AI-Powered Job Feed Automation Platform

## Overview
Scout Genius is a Flask-based web application designed to automate XML job feed processing, synchronize job listings with Bullhorn ATS/CRM, and provide AI-powered candidate vetting. Its primary purpose is to maintain accurate, real-time job listings, streamline application workflows, and enhance recruitment efficiency through automation. The project aims to become a multi-tenant SaaS platform, revolutionizing recruitment operations. Key capabilities include automated job feed generation, Bullhorn integration, and AI-powered candidate screening (Scout Vetting).

## User Preferences
Preferred communication style: Simple, everyday language.
Deployment workflow: Always confirm deployment requirements at the end of any changes or updates.
Development Approval Process: Before executing any development task, always provide "stack recommendation" including:
  - Autonomy level (Economy/Power)
  - Brief rationale for the choice
  - Wait for user approval before proceeding
Source of Truth: GitHub repository (KyleRoots/Scout Genius) — main branch.
Dev Admin Credentials: username=`admin`, password=`MyticasXML2025!`

## System Architecture

### UI/UX Decisions
- **Template Engine**: Jinja2 with Bootstrap 5 (dark theme).
- **Client-side**: Vanilla JavaScript.
- **Icons**: Font Awesome 6.0.
- **Dual-Domain Architecture**: Supports `app.scoutgenius.ai`, `apply.myticas.com` / `apply.stsigroup.com`, and `support.myticas.com` / `support.stsigroup.com`.
- **Microsoft SSO**: `support.myticas.com` uses Microsoft Entra ID (Office 365) single sign-on via OAuth 2.0.

### Technical Implementations
- **Web Framework**: Flask (Python 3.11) with modular route blueprints.
- **Database**: PostgreSQL with SQLAlchemy ORM and Alembic.
- **Authentication**: Flask-Login for user management, with username/email login and password reset.
- **Authorization**: Granular module-based access control.
- **User Management**: Admin interface for user CRUD, module subscriptions, Bullhorn User ID assignment, and account activation/reset, including "View As" impersonation.
- **Background Processing**: APScheduler for automated tasks (e.g., tearsheet monitoring, SFTP uploads, Scout Vetting cycle).
- **XML Processing**: Custom `lxml` processor for data handling and HTML consistency.
- **Email Service**: SendGrid for notifications.
- **AI/LLM Integration**: OpenAI GPT-5 for candidate vetting, screening, resume formatting, quality auditing, and job requirements extraction. GPT-5.4 retained for job classification only. Unlimited API cost structure negotiated — upgrade to higher models approved when accuracy demands it.
- **Embedding Service**: OpenAI text-embedding-3-large for similarity-based pre-filtering in candidate-job matching (upgraded from text-embedding-3-small).
- **Error Tracking**: Sentry SDK integration.
- **Testing**: Comprehensive pytest suite.
- **Proxy Support**: `ProxyFix` middleware.
- **Screening Engine Architecture**: Modular mixin package (`screening/`) — `CandidateVettingService` inherits from 6 focused mixins: `PromptBuilderMixin` (AI prompts, GPT calls, post-processing), `NoteBuilderMixin` (Bullhorn note formatting), `NotificationMixin` (recruiter emails), `CandidateDetectionMixin` (candidate discovery), `JobManagementMixin` (tearsheet jobs, requirements), `RecoveryMixin` (auto-retry safeguards). Orchestrator in `candidate_vetting_service.py` (~1,068 lines) keeps `__init__`, `process_candidate`, `run_vetting_cycle`, config accessors, and lock management. External imports (`from candidate_vetting_service import CandidateVettingService`) remain unchanged.

### Feature Specifications
- **Dual XML Feed System**: Generates two XML files (`myticas-job-feed-v2.xml` and `myticas-job-feed-pando.xml`) every 30 minutes.
- **Dual-Cycle Monitoring**: 5-minute tearsheet monitoring and 30-minute automated SFTP upload cycles.
- **Enforce Tearsheet Jobs Public**: Scheduled task to ensure jobs in monitored tearsheets are public in Bullhorn.
- **ATS Credentials Page**: Admin-only interface for managing ATS integration settings.
- **Real-Time Notifications**: Email alerts for new jobs.
- **Environment Isolation**: Separate development and production configurations.
- **Database-First Reference Numbers**: `JobReferenceNumber` table as the single source of truth.
- **Job Application Forms**: Public forms with multi-brand support, resume parsing, and Bullhorn integration.
- **Resume HTML Formatting**: Three-layer process including GPT-5.4 formatting.
- **AI Job Classification**: Classifies jobs based on LinkedIn taxonomy.
- **Scout Vetting**: AI-powered candidate screening using GPT-5.4 with embedding pre-filtering, experience-level classification, two-phase scoring (technical_score before location penalty, match_score after), work authorization/security clearance inference, and configurable global screening prompts. Includes employment gap penalties, mid-career gap penalties, remote location misfire enforcer (Python post-processor), and proximity-aware location penalty tiers. **AI-native recency relevance enforcement**: Rule 14 requires a mandatory `relevance_justification` field in `recency_analysis` — GPT-5.4 must cite specific shared duties/tools/domain overlap before marking a role as relevant. Generic justifications (transferable skills, communication, work ethic) are explicitly disallowed. The Quality Auditor's recency heuristic validates the justification field, flagging weak or missing justifications for AI review and potential re-vet. No Python keyword lists are used — enforcement is entirely AI-driven and works across all job domains.
- **Vetting System Health Monitoring**: Automated checks for Bullhorn, OpenAI, database, and scheduler status.
- **Scout Screening Portal**: Recruiter-facing dashboard for AI match results, scores, and qualification status with grouped candidate view, server-side candidate search, and a "Re-screen" button.
- **Scout Screening Quality Auditor**: Background AI audit to review recent "Not Qualified" results for scoring errors, with auto-trigger re-vets and email summaries.
- **Bullhorn Note Duplicate Safeguard**: Pre-creation check queries Bullhorn for existing Scout Screening notes within a 6-hour window. If all existing notes are "Incomplete" variants, the safeguard is overridden to allow the new complete result to be written. Prevents stale "Incomplete" notes from blocking successful re-screen results.
- **Module Switcher**: UI component for non-admin users with multiple module subscriptions.
- **Company Admin Role**: Manages users within a specific company.
- **Multi-Company Support**: `company` field on User model.
- **Automation Hub (Super-Admin Only)**: Management console for scheduled jobs and built-in automation tools.
- **Candidate Data Cleanup (Scheduled)**: Background job to extract missing emails from resume files and reparse empty candidate descriptions.
- **Activity Log (Super-Admin Only)**: System-wide admin visibility tracking login history, module usage, email delivery, and active users.
- **Vetting Sandbox (Super-Admin Only)**: 5-stage wizard for manually testing the AI vetting pipeline.
- **Resume Character Limit (Vetting)**: AI vetting reads up to 20,000 characters of resume text with improved prompt precision for skill distinction.
- **Experience Estimation (Missing Dates)**: AI prompt instructs GPT-5.4 to estimate professional years based on education or default to 2.0 years if no dates are present.
- **DOCX Resume Parsing**: Multi-layer extraction supporting `.doc` files via antiword conversion and garbled text detection.
- **Scout Support**: AI-powered internal ATS support ticket module with two-tier approval workflow. Tickets (SS-YYYY-NNNN format) are created from the support form for `ats_issue` and `data_correction` categories. AI intake generates understanding summaries, clarification loops, and solution proposals via email. User approves solution → admin (kroots@myticas.com) gives final authorization → AI executes Bullhorn API actions with full audit trail. Inbound email replies routed via support@scoutgenius.ai. Dashboard at `/scout-support` shows ticket list, conversation history, and execution proof. Module-gated via `scout_support` subscription.
- **Knowledge Hub**: Self-learning knowledge system for Scout Support AI. Admin-only page at `/scout-support/knowledge` for uploading reference documents (PDF, DOCX, TXT) and browsing learned resolution patterns. Documents are chunked and embedded (text-embedding-3-large) for similarity-based retrieval. Completed tickets auto-learn resolution patterns. Knowledge context is injected into AI prompts during ticket intake and clarification analysis. Models: `KnowledgeDocument`, `KnowledgeEntry`. Service: `scout_support/knowledge.py`. Routes: `routes/knowledge_hub.py`.
- **Platform Support (Feedback-to-Ticket)**: The header Feedback button now creates real `SupportTicket` records with platform categories (`platform_bug`, `platform_feature`, `platform_question`, `platform_other`). Platform tickets use a simplified flow (acknowledge + admin notification, no Bullhorn execution or two-tier approval). Emails use Scout Genius green branding (#4a9678) instead of Scout Support purple. A "My Tickets" page at `/my-tickets` (accessible to all logged-in users without module subscription) shows the user's tickets across both platform and ATS tiers. Routes: `routes/platform_support.py`. Templates: `my_tickets.html`, `my_ticket_detail.html`.

## Bullhorn Note Creation — Critical Requirements

Any ScoutGenius module that writes notes to Bullhorn records must follow these rules. Failure to comply results in notes that exist in the API but are invisible in the Bullhorn UI.

### personReference (REQUIRED)
- Bullhorn requires `personReference` on every Note entity. Omitting it returns HTTP 400.
- **CRITICAL**: `personReference` must point to a **Person** entity (Candidate or ClientContact), **NOT** a CorporateUser. Bullhorn silently accepts CorporateUser IDs but the UI will not display those notes.
- Use `_resolve_person_reference()` in `scout_support_service.py` as the reference implementation.

### Entity-Specific Payloads

| Target Entity | personReference | Association Method | Action Type |
|---|---|---|---|
| **Candidate** | Candidate ID | `candidates: [{'id': candidateId}]` in PUT payload | Per-module (e.g., "Scout Screen - Qualified") |
| **ClientContact** | ClientContact ID | Automatic via personReference | "General Notes" |
| **JobOrder** | Job's ClientContact ID (fetch from `entity/JobOrder/{id}?fields=clientContact`) | NoteEntity PUT after creation | "Job Update" |
| **Placement** | Placement's Candidate ID (fetch from `entity/Placement/{id}?fields=candidate`) | NoteEntity PUT after creation | "General Notes" |

### To-Many Fields (jobOrders, candidates, etc.)
- `candidates` array CAN be set during PUT (creation) — Bullhorn accepts it.
- `jobOrders`, `clientContacts`, `placements` arrays CANNOT be set during PUT — Bullhorn returns `ATTEMPT_TO_SET_TO_MANY` warning and silently ignores them.
- For JobOrder/Placement notes, use **NoteEntity** to link after creation:
  ```
  PUT entity/NoteEntity
  {'note': {'id': noteId}, 'targetEntityID': entityId, 'targetEntityName': 'JobOrder'}
  ```

### Standard Fields
- `commentingPerson`: Set to the API user's CorporateUser ID (1147490). This is the "Author" in the UI.
- `action`: Must be a registered Note Action type in Bullhorn Admin. Current approved actions: Call-Connected, Call-Left VM, Prescreen, Text Message, Job Update, General Notes, Email, Intake Call, Interview Prep/Debrief Notes, Meeting Notes, Client Meeting/Lunch, Sales Presentation/Proposal, Linkedin Direct Message, Linkedin InMail, Other, Reference Check, Skill Testing Results, Application, Automated Touchpoint, AI Vetter - Accept, AI Vetter - Reject, New Hire, Background Check, Separation Details, Sales Lead Provided, Candidate Referral Given, Important Notes.
- `isDeleted`: Always set to `False`.

### Key Implementation Files
- `scout_support_service.py`: `_resolve_person_reference()`, `_exec_create_note()`, `_add_audit_notes()`, `_link_note_via_note_entity()`
- `bullhorn_service.py`: `create_candidate_note()` (used by Scout Screening — Candidate-only, uses candidates array)
- `screening/note_builder.py`: `create_candidate_note()` mixin (Scout Screening note formatting + creation)

## External Dependencies

- **Python Libraries**: Flask, Flask-Login, Flask-WTF, Flask-SQLAlchemy, lxml, SQLAlchemy, Alembic, APScheduler, gunicorn, SendGrid, OpenAI, tiktoken, PyMuPDF, PyPDF2, python-docx, Paramiko, Requests, httpx, Sentry SDK, bcrypt, BeautifulSoup4.
- **Frontend Libraries**: Bootstrap 5, Font Awesome 6.
- **External Services**: PostgreSQL (Neon-hosted), SendGrid, OpenAI, Bullhorn ATS/CRM, Sentry.
- **AI Models**: OpenAI GPT-5.4 (primary). Migrated from GPT-4o in March 2026 ahead of API deprecation.