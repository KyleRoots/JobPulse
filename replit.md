# XML Job Feed Reference Number Updater

## Overview
This Flask-based web application automates the processing of XML job feed files to update reference numbers and synchronize job listings with Bullhorn ATS/CRM. It ensures correct reference number formatting, manages XML file updates, handles SFTP uploads, and provides a user-friendly interface for file uploads and validation. The system aims to provide a robust and automated solution for maintaining accurate and classified job listings, ensuring real-time synchronization and a seamless application experience, thereby enhancing job visibility and streamlining application workflows.

**SYSTEM OPTIMIZATION (2025-08-11)**: Completed comprehensive cleanup removing 39+ obsolete debug/emergency scripts, rotated 18MB log file, archived old screenshots/content, and consolidated backup files. **PRE-DEPLOYMENT CLEANUP (2025-08-11)**: Removed additional debug scripts, old backups, temporary files, and test artifacts. **FINAL CLEANUP**: Removed job 34226 debug scripts after confirming issue as Bullhorn API sync problem. **DEPLOYMENT PREPARATION CLEANUP (2025-08-11)**: Removed final 27+ obsolete files including all remaining debug/cleanup scripts and XML backup files. System fully optimized for deployment at 846MB with only essential operational files.

**STATUS (2025-08-12)**: **FULLY OPERATIONAL IN PRODUCTION**: Complete system restoration successful after critical parameter mismatch resolution. **LIVE SERVER**: Restored to 53 jobs with 100% accuracy verification (https://myticas.com/myticas-job-feed.xml). **MONITORING ACTIVE**: 8-step comprehensive monitoring cycle completing in 57.80 seconds with complete field remapping. **EMAIL NOTIFICATIONS**: AI classification spam eliminated - XML Change Monitor delivering focused notifications only for actual job changes. **DATA INTEGRITY**: 100% sync between Bullhorn and XML confirmed through live audit system. **AI PRESERVATION**: Enhanced dual-system approach preserving existing AI classification fields during complete remapping cycles. **PARAMETER FIX DEPLOYED**: XMLIntegrationService.add_job_to_xml() method updated to properly handle AI preservation parameters, resolving system failure.

**RECENT FIXES (2025-08-11)**: 
- ✅ Corrected job ID 32539 description using proper `publicDescription` field
- ✅ Implemented orphan job detection system  
- ✅ Removed 9 duplicate jobs from live XML (62→53 jobs)
- ✅ Bullhorn authentication restored and fully operational
- ✅ **CRITICAL BUG FIX**: Fixed CDATA handling inconsistency causing duplicate creation during monitoring
- ✅ 8-step monitoring system active with complete field accuracy and duplicate prevention
- ✅ **ACTIVITY LOGGING FIX**: Resolved misleading "Upload Success" entries - scheduled processing now only logs when files are actually processed
- ✅ **DASHBOARD JOB COUNT FIX**: Fixed stale job count display - dashboard now always shows fresh, accurate counts from Bullhorn
- ✅ **EMAIL NOTIFICATION SIMPLIFICATION**: Only send emails for actual job adds/removes, not routine field remapping - clearer and more transparent  
- ✅ **LIVE XML CHANGE MONITOR**: Implemented dedicated XML snapshot comparison system with 6-minute monitoring cycles for reliable change detection
- ✅ **COMPREHENSIVE LOGGING**: XML Change Monitor emails now logged in both Activity monitoring and /email-logs pages
- ✅ **EMAIL NOTIFICATION SYSTEM**: XML Change Monitor now fully operational - sends focused notifications for all job additions, removals, and modifications
- ✅ **AI CLASSIFICATION STATIC FIELDS**: jobfunction, jobindustries, and senioritylevel now behave like reference numbers - set once and remain static to prevent unnecessary notifications
- ✅ **TESTING SUCCESSFUL (2025-08-11)**: Static AI classification confirmed working - existing AI fields preserved during monitoring cycles
- ✅ **PRODUCTION DEPLOYMENT SUCCESSFUL (2025-08-11)**: Static AI classification feature deployed and operational in production
- ✅ **CRITICAL BUG FIX**: Fixed CDATA handling inconsistency causing duplicate creation during monitoring
- ✅ **AI PRESERVATION FIX (2025-08-11)**: Fixed critical issue where jobs removed/re-added during remapping bypassed AI preservation - now preserves existing AI values even during complete remapping cycles
- ✅ **DEFINITIVE AI PRESERVATION SOLUTION (2025-08-11)**: Resolved core issue by capturing initial XML snapshot before removals and using it for AI field preservation - should eliminate all classification change notifications
- ✅ **DEPLOYMENT**: Complete with comprehensive dual-monitoring system (Bullhorn + live XML validation)

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
- **XML Processing**: Custom processor utilizing `lxml`
- **Email Service**: SendGrid for notifications
- **SFTP Service**: Built-in SFTP client for secure file uploads (port 2222)
- **ATS Integration**: Real-time Bullhorn ATS/CRM monitoring for job changes, data mapping, and reference number generation.
- **Session Management**: Flask sessions with secure key
- **File Handling**: Secure temporary file storage
- **Proxy Support**: ProxyFix middleware

### Core Features
- **Dual Monitoring Architecture** (Updated 2025-08-11): Two complementary monitoring systems ensure reliable change detection and notifications:

**Enhanced 8-Step Comprehensive Monitoring** (Every 5 minutes): Bullhorn-focused data integrity system:
  1. Fetches ALL jobs from monitored tearsheets in Bullhorn
  2. Adds new jobs from tearsheets to XML
  3. Removes jobs no longer in tearsheets
  4. **COMPLETE REMAPPING**: Re-maps ALL fields for every existing job from Bullhorn (ensures 100% data accuracy)
  5. Uploads all changes to web server (SFTP port 2222)
  6. Completes data synchronization summary (email notifications handled by dedicated XML Change Monitor)
  7. Reviews and fixes CDATA/HTML formatting
  8. Runs FULL AUDIT with automatic corruption detection - uploads clean local XML when orphaned jobs detected on live server

**Live XML Change Monitor** (Every 6 minutes): **Primary email notification system**:
  1. Downloads current live XML from web server
  2. Extracts all job field data (title, description, location, etc.)  
  3. Compares with previous snapshot for precise change detection
  4. **Sends focused email notifications only when actual changes detected**
  5. Maintains snapshot history for reliable comparison
  6. **ONLY EMAIL SOURCE**: All job change notifications come from this system
  
- **Orphan Prevention System**: Automated duplicate detection and removal, conservative cleanup approach, and monitoring safeguards to prevent job pollution
- **Real-Time Progress Tracking**: Visual progress indicators [●●●●●●●○] show current step (Step 1/8 through Step 8/8)
- **Enhanced Audit Reporting**: Detailed summaries of discrepancies found and corrections made
- **Upload Failure Monitoring**: Comprehensive logging of SFTP connection issues in activity monitoring system with detailed diagnostics for troubleshooting
- **Comprehensive Status Logging**: Step-by-step progress updates with clear indicators
- **XML Integration Service**: Manages job data with proper CDATA formatting, reference number generation, HTML consistency, and LinkedIn recruiter tags. Uses Bullhorn's `publicDescription` field for accurate job descriptions. Includes automatic backups, structure validation, duplicate prevention, and MD5 checksums.
- **Job Application Form**: Responsive, public-facing form with resume parsing (extracts contact info from Word/PDF), auto-population of candidate fields, structured email submission, and Bullhorn job ID integration. Features Myticas Consulting branding, dark blue gradient background, glass morphism effects, and supports unique job URLs. Includes robust duplicate prevention and form lockdown mechanisms.
- **UI/UX**: Responsive dark-themed interface with real-time feedback and progress indicators.
- **Security**: Login-protected routes and admin user management.
- **AI-Powered Job Classification**: Integrates OpenAI GPT-4o to classify jobs (jobfunction, jobindustries, senioritylevel). These fields are static after initial population - only regenerated if job is removed and re-added to tearsheet.
- **Email Delivery Logging**: Comprehensive system for tracking email notifications with a web dashboard.
- **Intelligent File Management**: Automated file consolidation for backup archiving, duplicate detection, temp file cleanup, and storage optimization. Includes immediate cleanup scripts for existing data issues.
- **Dual-Domain Architecture**: Configured for production deployment with `jobpulse.lyntrix.ai` for the main application and `apply.myticas.com` for job application forms, supporting environment-aware URL generation.
- **Monitoring System**: Implements a RapidChangeTracker to detect and report multiple job state transitions within short monitoring cycles. Features enhanced email notifications with job lifecycle details and comprehensive state monitoring. Includes timeout protection and smart time management for monitoring cycles. Ensures scheduler auto-restarts on application reloads.
- **Health Endpoints**: Optimized, ultra-fast dedicated health endpoints for deployment monitoring systems (`/health`, `/ready`, `/alive`, `/ping`).

### Technical Implementation Details
- **XML Processing**: Requires root element 'source', specific required elements (title, company, date, referencenumber), and validation. Preserves existing reference numbers during ad-hoc changes unless a job is actively modified.
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