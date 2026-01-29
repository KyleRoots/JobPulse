# XML Job Feed Reference Number Updater

## Overview
This Flask-based web application automates the processing of XML job feed files to update reference numbers and synchronize job listings with Bullhorn ATS/CRM. Its core purpose is to maintain accurate, real-time job listings, streamline application workflows, and enhance job visibility and efficiency. The system manages XML updates, integrates with SFTP, and provides a user-friendly interface for file operations and validation. The project aims to improve recruitment processes by ensuring data integrity and automating repetitive tasks.

## User Preferences
Preferred communication style: Simple, everyday language.
Deployment workflow: Always confirm deployment requirements at the end of any changes or updates.
**Development Approval Process**: Before executing any development task, always provide "stack recommendation" including:
  - Model type (Standard/High-Performance)
  - Extended thinking (Yes/No)
  - Brief rationale for the choice
  - Wait for user approval before proceeding

## System Architecture

### UI/UX Decisions
- **Template Engine**: Jinja2 with Bootstrap 5 (dark theme)
- **Client-side**: Vanilla JavaScript for interactive elements
- **UI Framework**: Bootstrap 5 with custom CSS for responsive design
- **Icons**: Font Awesome 6.0
- **Dual-Domain Architecture**: Configured for `jobpulse.lyntrix.ai` (main app) and `apply.myticas.com` (job application forms)

### Technical Implementations
- **Web Framework**: Flask (Python)
- **Database**: PostgreSQL with SQLAlchemy for schedules and logs (timezone handling for Eastern Time display)
- **Authentication**: Flask-Login for secure user management
- **Background Processing**: APScheduler for automated tasks and Bullhorn monitoring
- **XML Processing**: Custom `lxml` processor for job data, CDATA formatting, reference number generation, HTML consistency, and LinkedIn recruiter tags.
- **Email Service**: SendGrid for notifications and delivery logging.
- **Session Management**: Flask sessions with 30-day persistence (remember me enabled by default)
- **File Handling**: Secure temporary file storage with cleanup, supporting XML files only (max 50MB).
- **Error Handling**: Comprehensive XML syntax error catching, user-friendly messages, server-side logging, client-side validation.
- **Proxy Support**: ProxyFix middleware

### Feature Specifications
- **Dual-Cycle Monitoring System**: 
  - 5-minute tearsheet monitoring for real-time UI visibility and job change detection
  - 30-minute automated upload cycle for SFTP synchronization
  - Both cycles controlled by dual toggles (`automated_uploads_enabled` and `sftp_enabled`)
- **Smart Tearsheet Auto-Cleanup**: Automatically removes ineligible jobs from tearsheets during monitoring cycles:
  - Jobs with `isOpen=Closed` are auto-removed
  - Jobs with blocked statuses (Qualifying, Hold - Covered, Hold - Client Hold, Offer Out, Filled, Lost - Competition, Lost - Filled Internally, Lost - Funding, Canceled, Placeholder/MPC, Archive) are auto-removed
  - All removals logged to activity dashboard with reason for audit trail
  - Failsafe to prevent wasted resources on closed/filled jobs that users forgot to remove
- **Real-Time Email Notifications**: Instant email alerts sent to kroots@myticas.com when new jobs are added to any monitored tearsheet. Notifications include job ID, title, timestamp, and monitor name for easy Bullhorn search and tracking.
- **Environment Isolation**: Separate development and production environments, including distinct XML upload targets (`-dev.xml` vs. `.xml`), isolated PostgreSQL databases, and independent schedules to prevent cross-contamination.
- **Orphan Prevention**: Automated duplicate detection and removal using Bullhorn Entity API for validation against Search API results, preventing job pollution and ensuring data integrity.
- **Database-First Reference Numbers**: `JobReferenceNumber` table is the single source of truth for all reference numbers, updated every 120 hours without SFTP uploads. SimplifiedXMLGenerator loads reference numbers from the database.
- **Ad-hoc Reference Number Refresh**: Manual "Refresh All" option for immediate database updates.
- **Job Application Form**: Public-facing form with resume parsing (Word/PDF), auto-population of candidate fields, Bullhorn job ID integration, and unique branding.
- **Keyword-Based Job Classification**: Lightning-fast (<1 second) job categorization using comprehensive keyword dictionaries for LinkedIn's official taxonomy (28 job functions, 20 industries, 5 seniority levels). Weighted scoring system prioritizes title matches (3x) over description. Guaranteed defaults ensure all taxonomy fields are always populated. Eliminates AI timeout risks and API costs.
- **Intelligent File Management**: Automated consolidation, duplicate detection, and temporary file cleanup.
- **Health Endpoints**: Optimized `/health`, `/ready`, `/alive`, `/ping` endpoints.
- **XML Generation Enhancements**: All XML fields wrapped in CDATA, HTML descriptions parsed with `lxml` for tag closure.
- **Zero-Job Detection Safeguard**: Prevents XML file corruption when Bullhorn API returns 0 jobs due to temporary errors. System automatically blocks updates, creates timestamped backups in `xml_backups/`, and sends single alert email (preventing the November 6, 2025 email flood incident from recurring).
- **Zero-Touch Production Deployment**: Environment-aware database seeding and auto-configuration for admin users, SFTP, Bullhorn credentials, tearsheet monitors, and automation toggles from environment secrets. Idempotent design preserves user settings post-initial deployment.
- **AI Candidate Vetting (Premium Add-on)**: Automated candidate-job matching system using GPT-4o:
  - **Detection (100% Coverage)**: Uses ParsedEmail-based detection to capture ALL inbound applicants (both new and existing candidates with updated resumes). Fallback to Bullhorn "Online Applicant" search for candidates entering through other channels.
  - **Configurable Batch Size**: Admin-configurable batch size (1-100, default 25) per 5-minute cycle for handling variable applicant volumes
  - **Resume Analysis**: Extracts resume files (PDF/DOCX/DOC) from candidate profiles in Bullhorn
  - **AI Matching**: Compares each candidate's resume against all active jobs in monitored tearsheets using GPT-4o, focusing on mandatory requirements only (ignores nice-to-haves)
  - **Scoring**: Generates match scores (0-100%), detailed fit explanations, and key qualifications
  - **Note Creation**: Creates Bullhorn notes on ALL candidates with vetting results (qualified and non-qualified) for complete audit trail
  - **Recruiter Notifications**: Sends email alerts to job-assigned recruiters when candidates score ≥80% (configurable threshold) with clickable Bullhorn links
  - **Audit Dashboard**: Tabbed interface showing All Candidates, Recommended (80%+), and Not Recommended with:
    - Expandable candidate cards showing match details, scores, skills, and gaps
    - Direct Bullhorn hyperlinks to candidate and job profiles for quick sanity checks (cls45.bullhornstaffing.com)
    - Filter by recommended vs not recommended for quality auditing
  - **Job Requirements Interpretation**: AI extracts mandatory requirements from job descriptions, with admin override capability for custom requirements per job
  - **Admin UI**: Settings page at `/vetting` with enable/disable toggle, threshold configuration, batch size setting, and activity dashboard
  - **Sample Notes**: Preview page at `/vetting/sample-notes` showing exact note formats for qualified and non-qualified candidates
  - **Production Default**: Vetting is enabled by default in production deployments, disabled in development
  - **Tables**: `vetting_config` (settings), `candidate_vetting_log` (processing history), `candidate_job_match` (score details), `job_vetting_requirements` (custom/AI requirements)
  - **ParsedEmail Tracking**: `vetted_at` column tracks when applications have been processed by AI vetting

## External Dependencies

### Python Libraries
- **Flask**: Web framework
- **lxml**: XML parsing
- **SQLAlchemy**: ORM for database interactions
- **APScheduler**: Background task scheduling
- **Flask-Login**: User session management
- **SendGrid**: Email notifications and delivery tracking

### Frontend Libraries
- **Bootstrap 5**: UI framework
- **Font Awesome 6**: Icon library

## Technical Learnings & Known Limitations

### Bullhorn REST API Field Constraints
- **Assignments Field Not Supported**: The `assignments` field (containing "Recruiter" data visible in Bullhorn UI) is NOT accessible via Bullhorn's REST API. This field was removed from all API queries as of November 9, 2025 (previously caused intermittent 400 errors).
- **To-Many Association Limitation**: To-many associations like `assignments[N]` with nested fields don't work in Entity API or Search API queries
- **Working Recruiter Extraction**: System successfully extracts recruiter data using fallback hierarchy:
  1. `assignedUsers(firstName,lastName)` - primary source
  2. `responseUser(firstName,lastName)` - fallback
  3. `owner(firstName,lastName)` - final fallback
- **Success Rate**: Current configuration achieves 95.6% recruiter tag population (65 of 68 jobs in production)

### November 6, 2025 Incident Analysis
- **What Happened**: Invalid `assignments` API field caused Bullhorn to return 0 jobs. Monitoring service interpreted this as "all jobs deleted" and removed all 68 jobs from XML, triggering 68 false "new job" emails when API recovered.
- **Root Cause**: Missing safeguard to detect empty API responses as errors vs. legitimate data.
- **Fix Implemented**: Zero-job detection safeguard now blocks updates when API returns 0 jobs but XML contains ≥5 jobs. System creates backup and sends single alert instead of processing the corrupt data.

### January 28, 2026 - Note Creation Fix for Email Parsing
- **Issue**: Candidate notes were not being created for email-parsed applications despite successful candidate creation
- **Root Cause**: Bullhorn One API requires `commentingPerson` field to identify who created the note
- **Fix Implemented**: 
  1. Store `userId` from REST login response in `BullhornService.user_id`
  2. Add `commentingPerson` with authenticated user ID to all note creation requests
  3. Track note creation status (`ai_summary_created`, `fallback_created`, `all_notes_failed`) in `parsed_email.processing_notes`
  4. Include note ID in processing notes for verification
- **Fallback Behavior**: If AI summary note fails, system creates a basic application note with available data

## Bullhorn One Migration (January 26, 2026)

### Overview
The system supports both legacy Bullhorn and Bullhorn One (new) API endpoints via an environment variable toggle. This allows seamless switching between environments without code changes.

### Environment Variable Toggle
- **Variable**: `BULLHORN_USE_NEW_API`
- **Default**: `false` (uses legacy Bullhorn endpoints)
- **To enable Bullhorn One**: Set `BULLHORN_USE_NEW_API=true`

### Bullhorn One Endpoints (New - January 2026)
When `BULLHORN_USE_NEW_API=true`:
- **Auth URL**: `https://auth-east.bullhornstaffing.com/oauth/authorize`
- **Token URL**: `https://auth-east.bullhornstaffing.com/oauth/token`
- **REST Login URL**: `https://rest-east.bullhornstaffing.com/rest-services/login`
- **REST URL**: `https://rest45.bullhornstaffing.com/rest-services/dcc900/`

### Legacy Endpoints (Current)
When `BULLHORN_USE_NEW_API=false` or not set:
- Uses `https://rest.bullhornstaffing.com/rest-services/loginInfo` to dynamically discover OAuth and REST URLs

### Bullhorn One Credentials (January 2026)
- **Username**: `myticasbh1.api`
- **Client ID**: `676f3cfc-c611-4d23-a8bc-9b595a01d4ab`
- **Password & Client Secret**: Stored securely in Bullhorn Settings page

### Migration Steps
1. **Before January 26**: Keep `BULLHORN_USE_NEW_API=false` or unset (uses legacy)
2. **On January 26**: 
   - Go to **Bullhorn Settings** page in the app
   - Update credentials with Bullhorn One username, password, client ID, and client secret
   - Set `BULLHORN_USE_NEW_API=true` in environment variables
3. **Test Connection**: Use "Test Connection" button on Bullhorn Settings page or `/api/bullhorn/connection-test` endpoint
4. **Monitor**: Check application logs for successful authentication with new endpoints

### API Endpoints for Testing
- `GET /api/bullhorn/api-status` - View current API mode and endpoints (no connection test)
- `POST /api/bullhorn/connection-test` - Test Bullhorn connection with current configuration

### Bullhorn One Tearsheet IDs (January 2026)
The following tearsheet IDs are configured in the new Bullhorn One environment:
| Tearsheet Name | ID |
|----------------|------|
| Sponsored - STSI | 1531 |
| Sponsored - GR | 1474 |
| Sponsored - VMS | 1239 |
| Sponsored - CLE | 1233 |
| Sponsored - CHI | 1232 |
| Sponsored - OTT | 1231 |

**Note**: These IDs may differ from the legacy environment. After migration, verify tearsheet monitors are pointing to the correct IDs in the Tearsheet Monitors page.

### Rollback
If issues arise, set `BULLHORN_USE_NEW_API=false` to revert to legacy endpoints immediately.