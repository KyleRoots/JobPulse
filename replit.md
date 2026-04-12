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
- **Background Processing**: APScheduler for automated tasks (e.g., tearsheet monitoring, SFTP uploads, Scout Vetting cycle).
- **XML Processing**: Custom `lxml` processor for data handling and HTML consistency.
- **Email Service**: SendGrid for notifications.
- **AI/LLM Integration**: OpenAI GPT-5.4 (flagship) standardized across ALL modules — candidate vetting, screening, resume formatting, quality auditing, job requirements extraction, duplicate candidate detection, job classification, Scout Support conversation analysis, Scout Prospector research, and occupation/title extraction. Unlimited API cost structure negotiated. GPT-5/5.4 do NOT support `temperature` parameter (only default=1 allowed).
- **Embedding Service**: OpenAI text-embedding-3-large for similarity-based pre-filtering in candidate-job matching.
- **Error Tracking**: Sentry SDK integration.
- **Testing**: Comprehensive pytest suite.
- **Proxy Support**: `ProxyFix` middleware.
- **Screening Engine Architecture**: Modular mixin package (`screening/`) with an orchestrator in `candidate_vetting_service.py` for managing AI prompts, Bullhorn note formatting, notifications, candidate detection, job management, and recovery. Batch-optimized: tearsheet jobs loaded once per cycle (not per candidate), candidates processed 5-at-a-time via ThreadPoolExecutor with thread-safe Bullhorn access (threading.Lock) and proper SQLAlchemy session isolation (per-thread app_context, primitive data returned from threads, re-queried in main thread for post-processing).
- **Dual XML Feed System**: Generates two XML files (`myticas-job-feed-v2.xml` and `myticas-job-feed-pando.xml`) every 30 minutes.
- **Dual-Cycle Monitoring**: 5-minute tearsheet monitoring and 30-minute automated SFTP upload cycles.
- **Job Application Forms**: Public forms with multi-brand support, resume parsing, and Bullhorn integration including duplicate candidate detection and profile enrichment.
- **Resume HTML Formatting**: Three-layer process including GPT-5.4 formatting.
- **AI Vision OCR for Scanned PDFs**: Automatic fallback for image-based/scanned PDF resumes. When PyMuPDF/PyPDF2 extract less than 50 chars from a large PDF (>5KB), pages are rendered to PNG images at 200 DPI and sent to GPT-4.1-mini vision for text extraction. Applied in both the vetting pipeline (`vetting/resume_utils.py`) and the application intake pipeline (`resume_parser.py`). Handles up to 5 pages per resume.
- **Bullhorn JSON-Enveloped File Unwrapping**: Bullhorn's file API sometimes returns resume files wrapped in JSON (`{"File":{"contentType":"...","fileContent":"<base64>"}}`). The resume download function in `screening/detection.py` automatically detects and decodes this format before passing to extraction.
- **AI Job Classification**: Classifies jobs based on LinkedIn taxonomy.
- **Scout Vetting**: AI-powered candidate screening using GPT-5.4 with embedding pre-filtering, experience-level classification, two-phase scoring, work authorization/security clearance inference, configurable global screening prompts, employment gap penalties, and AI-native recency relevance enforcement with strict justification requirements.
- **Vetting System Health Monitoring**: Automated checks for Bullhorn, OpenAI, database, and scheduler status.
- **Scout Screening Portal**: Recruiter-facing dashboard for AI match results, scores, and qualification status.
- **Scout Screening Quality Auditor**: Background AI audit to review "Not Qualified" results for scoring errors, with auto-trigger re-vets.
- **Bullhorn Note Duplicate Safeguard**: Prevents stale "Incomplete" or "Analysis failed" (0%) notes from blocking successful re-screen results by overriding if only supersedable notes exist within a 6-hour window. Failed-analysis notes (0% scores from API errors) are now classified as "Scout Screen - Incomplete" instead of "Not Qualified".
- **Genuine 0% Non-Fit Classification**: Candidates who score 0% but had successful analysis (have `CandidateJobMatch` records, no `error_message`, and `total_jobs_matched` is populated) are classified as "Not Qualified" — not "Incomplete". "Incomplete" is reserved for actual errors (no match records, error messages, or missing job data). Auto-retry (`_reset_zero_score_failures`) also skips genuine non-fits to avoid wasting batch slots.
- **Zero-Score Verification**: When a candidate scores 0% on ALL jobs, an automatic re-verification step runs on the top 3 jobs using gpt-4.1-mini. The AI reviews the original 0% assessment and confirms whether it's truly zero relevance or if an honest revised score is warranted for transferable skills/partial overlap. No artificial cap — the AI returns its honest assessment. Revised scores update match records with `[Verified]` prefix; confirmed 0% scores are left unchanged.
- **Zero-Score Failure Retry Limiter**: `ParsedEmail.vetting_retry_count` tracks how many times a candidate has been auto-retried after 0% API failures. After `MAX_ZERO_SCORE_RETRIES` (2) retries, the candidate is marked `retry_blocked` on the vetting log, preventing endless recycling that consumes batch slots. Counter resets on successful vetting.
- **Automated Duplicate Candidate Merge**: Two-mode system (bulk scan and scheduled check) for merging duplicate candidate records with an audit trail.
- **Candidate Data Cleanup (Scheduled)**: Background job to extract missing emails from resume files, reparse empty candidate descriptions, and fill missing occupation/title fields using AI resume analysis (gpt-4.1-mini).
- **Activity Log (Super-Admin Only)**: System-wide admin visibility tracking login history, module usage, email delivery, and active users.
- **Vetting Sandbox (Super-Admin Only)**: 5-stage wizard for manually testing the AI vetting pipeline.
- **Scout Support**: AI-powered internal ATS support ticket module with two-tier approval workflow, AI intake, clarification loops, solution proposals, and Bullhorn API execution. Includes Knowledge Hub for self-learning via uploaded documents (PDF, DOCX, TXT) and OneDrive integration for document synchronization. Admin conversation takeover: when ticket is escalated, admin can reply directly to user from ticket detail page or via email; ticket moves to `admin_handling` status and AI steps aside. User replies during admin handling are forwarded to admin. Closed ticket reopening: user replies to closed/completed tickets trigger AI re-analysis with full conversation/execution history; AI handles directly if possible, proposes new solution if needed, or re-escalates. Direct escalation keywords bypass AI. Intelligent retry with contextual notifications: retry emails include failure analysis and new strategy summary. Attachment persistence: uploaded screenshots/files stored in `SupportAttachment` model (binary in DB) and displayed as thumbnails/download links on both admin and user ticket detail pages; escalation emails reference stored attachments. Admin AI drafting: during `admin_handling`, admin email replies are classified as either direct user messages or AI instructions; AI instructions trigger GPT-5 to generate requested content (e.g., draft emails) and send back to admin only, without forwarding to the user.
- **Platform Support (Feedback-to-Ticket)**: User feedback creates `SupportTicket` records with platform categories, simplified workflow, and a "My Tickets" page for users to view their tickets. Admin can reply to users directly from the ticket detail page, creating a full conversation thread with audit trail. User email replies are routed via SendGrid inbound webhook, recorded in the conversation, and the admin is notified. AI auto-response is preserved in `_handle_platform_reply_ai()` for future activation.
- **Scout Prospector**: AI-powered B2B sales prospecting tool for staffing recruiters. Uses OpenAI Responses API (GPT-5.4 flagship) with `web_search_preview` tool and **multi-pass research engine** (3 search angles: Job Boards, Growth News, Industry Directories) to discover up to 50 unique companies per run. Deduplicates across passes and ranks by qualification score. Features: ICP CRUD with industry/size/geography/job-type/hiring-signal criteria (with Select All/Deselect All for checkboxes), AI Suggest Criteria (auto-fills Steps 2–4 from description with confirmation toast), GPT-powered web research sessions, prospect management with qualification scoring and status tracking (new/hot/warm/cold/contacted/not_interested), CSV export (includes phone numbers), prospect detail views with key contacts (name/title/email/phone/LinkedIn) and source URLs, hiring activity with parsed "View Source" buttons, and branded help guide at `/scout-prospector/guide`. Header toggle pill included for non-admin multi-module users. Settings page displays "Prospector" with crosshairs icon. Gunicorn timeout set to 300s to accommodate multi-pass research. Files: `scout_prospector_service.py`, `routes/scout_prospector.py`, `templates/scout_prospector*.html`, `templates/scout_prospector_guide.html`. Theme accent: `#3b82f6` (royal blue), `data-module="prospector"`, `active_page="prospector"`. Models: `ProspectorProfile`, `ProspectorRun`, `Prospect`.

### Bullhorn Note Creation — Critical Requirements
- **`personReference` (REQUIRED)**: Must point to a **Person** entity (Candidate or ClientContact), not a CorporateUser, to be visible in Bullhorn UI.
- **Entity-Specific Payloads**: Defines `personReference`, association method, and action type for Candidate, ClientContact, JobOrder, and Placement notes.
- **To-Many Fields**: `candidates` array can be set during PUT; `jobOrders`, `clientContacts`, `placements` arrays cannot be set during PUT and require `NoteEntity` to link after creation.
- **Standard Fields**: `commentingPerson` (API user's CorporateUser ID: 1147490), `action` (registered Note Action type), and `isDeleted` (always `False`).

## External Dependencies

- **Python Libraries**: Flask, Flask-Login, Flask-WTF, Flask-SQLAlchemy, lxml, SQLAlchemy, Alembic, APScheduler, gunicorn, SendGrid, OpenAI, tiktoken, PyMuPDF, PyPDF2, python-docx, Paramiko, Requests, httpx, Sentry SDK, bcrypt, BeautifulSoup4.
- **Frontend Libraries**: Bootstrap 5, Font Awesome 6.
- **External Services**: PostgreSQL (Neon-hosted), SendGrid, OpenAI, Bullhorn ATS/CRM, Sentry.
- **AI Models**: OpenAI GPT-5.4 (primary).