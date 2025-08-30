# XML Job Feed Reference Number Updater

## Overview
This Flask-based web application automates the processing of XML job feed files to update reference numbers and synchronize job listings with Bullhorn ATS/CRM. It provides a robust, automated solution for maintaining accurate job listings, ensuring real-time synchronization, and streamlining application workflows, thereby enhancing job visibility. The system ensures correct reference number formatting, manages XML file updates, handles SFTP uploads, and offers a user-friendly interface for file uploads and validation.

## Recent Critical Fixes (Aug 30, 2025)

### Client Branding Domain Detection Fix (1:10 AM UTC)
**ISSUE RESOLVED**: STSI jobs accessed via apply.stsigroup.com were showing Myticas branding instead of STSI branding
**ROOT CAUSE**: Job application route was hardcoded to always render 'apply.html' (Myticas template) regardless of requesting domain
**SOLUTION IMPLEMENTED**: Added domain-based template selection with intelligent client detection
**TECHNICAL APPROACH**: 
- Added `request.host.lower()` domain detection in job application route
- Logic: `stsigroup.com` domains → `apply_stsi.html`, all others → `apply.html` (default Myticas)
- Preserves all existing functionality while enabling client-specific branding
- Added logging to track template selection for monitoring
**RESULT**: STSI jobs now correctly display STSI branding when accessed via apply.stsigroup.com
**EXTENSIBILITY**: Framework ready for additional client domains in the future

## Recent Critical Fixes (Aug 29, 2025)

### 48-Hour Reference Refresh Upload Fix (3:48 PM UTC) - CRITICAL SYSTEM FIX
**ISSUE RESOLVED**: 48-hour reference refresh was updating local XML but NOT uploading to server, causing live XML to show old reference numbers despite email confirmations
**ROOT CAUSE**: Reference numbers were excluded from monitoring cycle uploads (marked as STATIC_FIELDS), so refresh relied on monitoring to detect changes but monitoring ignored reference number changes
**SOLUTION IMPLEMENTED**: Added direct upload capability to `reference_number_refresh()` function with complete separation from monitoring cycle
**TECHNICAL APPROACH**: 
- Used identical locking mechanism as 5-minute monitoring cycle (`monitoring.lock` with 4-minute timeout)
- Added intelligent conflict detection (waits 30 seconds if monitoring is running, then skips if still busy)
- Upload happens immediately after local refresh, ensuring server synchronization
- Enhanced email notifications now include upload status (success/failure/skipped)
- Uploads to `myticas-job-feed-v2.xml` (consistent with V2 migration)
**COORDINATION**: Both 48-hour refresh and 5-minute monitoring now use same lock file, guaranteeing mutual exclusion and zero conflicts
**RESULT**: Next 48-hour refresh will update local XML AND upload to server immediately, ensuring live reference numbers are always fresh
**VERIFICATION**: Monitor logs for "✅ Reference refresh complete: Local XML updated AND uploaded to server" message

### Email Notification Optimization (12:10 AM UTC)
**CHANGE**: Temporarily disabled email notifications from regular XML monitoring cycles (every 6 minutes) to reduce redundant notifications
**APPROACH**: Modified `xml_change_monitor.py` to accept `enable_email_notifications` parameter and set to `False` for regular monitoring cycles
**PRESERVED**: Email notifications continue working for manual refresh button and 48-hour automatic refresh
**RESULT**: Cleaner notification flow - users only receive emails for manual actions and scheduled refreshes, not regular monitoring cycles
**REVERT INSTRUCTIONS**: Change `enable_email_notifications=False` to `enable_email_notifications=True` in app.py line 6473

## Recent Critical Fixes (Aug 20, 2025)

### Upload Consolidation Fix (1:45 AM UTC)
**ISSUE**: Multiple upload sources causing duplicate uploads seconds apart in Recent Activity
**ROOT CAUSE**: Three different functions were triggering uploads - immediate sync, daily refresh, and Step 6
**SOLUTION**: Disabled all upload sources except Step 6 of the 8-step monitor
**RESULT**: Single, clean uploads every 5 minutes instead of duplicates

### Job Application Form Simplification (1:20 AM UTC)
**ISSUE**: Supporting documentation upload section was unnecessary complexity
**SOLUTION**: Removed supporting documentation section from template, kept only resume upload
**CACHE FIX**: Added no-cache headers to force browsers to load fresh content
**RESULT**: Simplified, cleaner application form (requires deployment to production)

## Recent Critical Fixes (Aug 19, 2025)

### External System Conflict RESOLVED (7:22 PM UTC)
**ISSUE**: Another system was uploading XML with wrong reference numbers (W3K1T7SVS8 instead of 4P40G6IGE8)
**ROOT CAUSE**: External WordPress plugin or scheduled task overwriting our uploads to myticas-job-feed.xml
**SOLUTION**: Switched ALL monitoring to use myticas-job-feed-CORRECT-1755627190.xml instead of myticas-job-feed.xml
**RESULT**: System now completely ignores myticas-job-feed.xml and uses CORRECT filename exclusively, preventing any external system interference

### XML Server Synchronization & CDATA Formatting COMPLETELY RESOLVED (6:07 PM UTC)
**ISSUE**: Server XML showed different reference numbers, forward slashes in job titles, and incomplete CDATA formatting
**ROOT CAUSE**: WPEngine/Cloudflare caching layer was serving stale XML versions + Step 7 only wrapped 3 fields in CDATA
**SOLUTION**: 
- **Fixed unescaped ampersands** in job descriptions (12 instances corrected)
- **Enhanced CDATA wrapping** to cover ALL 18 required text fields (not just title/description/company)
- **Applied comprehensive fix** to 74 fields missing CDATA formatting
- **Force-uploaded corrected XML** via SFTP with cache-busting
- **Result**: Server now shows correct reference numbers, no forward slashes, and 1,013 properly CDATA-wrapped fields

### Reference Number Flip-Flopping COMPLETELY RESOLVED (2:00 PM UTC)
**TRUE ROOT CAUSE IDENTIFIED AND FIXED**: The `_update_fields_in_place` function in xml_integration_service.py was not properly extracting existing reference numbers from the CDATA-wrapped XML content before updating jobs, causing ALL jobs to get new reference numbers on every monitoring cycle.

**SOLUTION IMPLEMENTED**: 
- **Fixed reference number extraction** in `_update_fields_in_place` function (xml_integration_service.py line 1298-1307)
- **Now properly extracts and preserves** existing reference numbers from CDATA format before updating
- **Step 8 audit disabled LIVE XML download** (app.py lines 2557-2567) to prevent server sync issues
- **Result**: Reference numbers now remain 100% stable during monitoring cycles - verified across multiple cycles

### CDATA Formatting Issue Fixed (3:57 PM UTC)
**ISSUE**: Step 7 formatting review was incorrectly stripping CDATA wrappers from all XML fields
**ROOT CAUSE**: Regex pattern in Step 7 was matching fields with CDATA and replacing them without CDATA
**FIX APPLIED**: Modified regex to only add CDATA to fields that don't already have it (app.py line 2539)
**RESULT**: CDATA formatting now preserved during monitoring cycles

### Job Synchronization Fix
**RESTORED PROPER JOB REMOVAL**: Re-enabled automatic removal of jobs that no longer exist in Bullhorn tearsheets. This ensures 100% accurate synchronization between Bullhorn and XML:
- **NEW jobs from Bullhorn** → Automatically added to XML with new reference numbers
- **REMOVED jobs from Bullhorn** → Automatically removed from XML
- **MODIFIED jobs in Bullhorn** → Fields updated in-place while preserving reference numbers

### URL Encoding Fix
**SPECIAL CHARACTERS IN JOB TITLES**: Fixed 404 errors for job titles containing "/" characters by replacing them with hyphens before URL encoding. Example: "Legal Invoice Analyst/Initial Reviewer" now generates working application URLs.

### HTML Description Formatting
**KNOWN ISSUE**: XML Change Monitor detects ~30 "modified" jobs each cycle due to HTML description formatting changes between `<span>` and `<p>` tags. This is cosmetic and doesn't affect data integrity or reference numbers.

## User Preferences
Preferred communication style: Simple, everyday language.
Deployment workflow: Always confirm deployment requirements at the end of any changes or updates.

## System Architecture

### Frontend
- **Template Engine**: Jinja2 with Bootstrap 5 (dark theme)
- **Client-side**: Vanilla JavaScript for interactive elements
- **UI Framework**: Bootstrap 5 with custom CSS for responsive design
- **Icons**: Font Awesome 6.0

### Backend
- **Web Framework**: Flask (Python)
- **Database**: PostgreSQL with SQLAlchemy for schedules and logs
- **Authentication**: Flask-Login for secure user management
- **Background Processing**: APScheduler for automated tasks and Bullhorn monitoring
- **XML Processing**: Custom processor utilizing `lxml` for managing job data with proper CDATA formatting, reference number generation, HTML consistency, and LinkedIn recruiter tags.
- **Email Service**: SendGrid for notifications and comprehensive email delivery logging.
- **SFTP Service**: Built-in SFTP client for secure file uploads.
- **ATS Integration**: Real-time Bullhorn ATS/CRM monitoring for job changes, data mapping, and reference number generation.
- **Session Management**: Flask sessions with secure key
- **File Handling**: Secure temporary file storage
- **Proxy Support**: ProxyFix middleware

### Core Features
- **Dual Monitoring Architecture**:
    - **Enhanced 8-Step Comprehensive Monitoring**: Bullhorn-focused data integrity system that fetches, adds, removes, and re-maps all job fields from Bullhorn tearsheets, ensuring 100% data accuracy and uploading changes to the web server. Includes comprehensive auditing and orphan job detection.
    - **Live XML Change Monitor**: Primary email notification system that downloads the live XML, compares it with previous snapshots, and sends focused email notifications only for actual job content changes, excluding static fields like reference numbers and AI classifications.
- **Orphan Prevention System**: Automated duplicate detection and removal to prevent job pollution.
- **Real-Time Progress Tracking**: Visual indicators show current step during processing.
- **Enhanced Audit Reporting**: Detailed summaries of discrepancies and corrections.
- **Upload Failure Monitoring**: Comprehensive logging of SFTP connection issues.
- **Comprehensive Status Logging**: Step-by-step progress updates.
- **Ad-hoc Reference Number Refresh**: Manual "Refresh All" button for immediate reference number updates while preserving all job data, with SFTP upload, email notifications, and activity logging.
- **Job Application Form**: Responsive, public-facing form with resume parsing (Word/PDF), auto-population of candidate fields, structured email submission, and Bullhorn job ID integration. Features unique branding and robust duplicate prevention.
- **UI/UX**: Responsive dark-themed interface with real-time feedback.
- **Security**: Login-protected routes and admin user management.
- **AI-Powered Job Classification**: Integrates OpenAI GPT-4o to classify jobs (jobfunction, jobindustries, senioritylevel), with these fields remaining static after initial population.
- **Intelligent File Management**: Automated file consolidation, duplicate detection, temporary file cleanup, and storage optimization.
- **Dual-Domain Architecture**: Configured for `jobpulse.lyntrix.ai` (main app) and `apply.myticas.com` (job application forms) with environment-aware URL generation.
- **Monitoring System**: Implements RapidChangeTracker for detecting and reporting multiple job state transitions, enhanced email notifications, timeout protection, and scheduler auto-restarts.
- **Health Endpoints**: Optimized, ultra-fast dedicated health endpoints (`/health`, `/ready`, `/alive`, `/ping`).

### Technical Implementation Details
- **XML Processing**: Requires root element 'source' and specific required elements (title, company, date, referencenumber). Preserves existing reference numbers during ad-hoc changes.
- **File Upload Constraints**: XML files only, max 50MB, temporary storage, secure filename handling.
- **Error Handling**: Comprehensive XML syntax error catching, user-friendly messages, server-side logging, client-side validation.
- **HTML Formatting Consistency**: Ensures consistent HTML markup within CDATA sections.
- **Resume Parsing**: Extracts contact information from Word and PDF formats.

## External Dependencies

### Python Libraries
- **Flask**: Web framework
- **lxml**: XML parsing
- **Werkzeug**: WSGI utilities
- **SQLAlchemy**: ORM for database interactions
- **APScheduler**: Background task scheduling
- **Flask-Login**: User session management
- **SendGrid**: Email notifications
- **OpenAI**: AI-powered job classification

### Frontend Libraries
- **Bootstrap 5**: UI framework
- **Font Awesome 6**: Icon library