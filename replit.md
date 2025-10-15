# XML Job Feed Reference Number Updater

## Overview
This Flask-based web application automates the processing of XML job feed files to update reference numbers and synchronize job listings with Bullhorn ATS/CRM. Its primary purpose is to maintain accurate job listings, ensure real-time synchronization, and streamline application workflows, thereby enhancing job visibility and efficiency. The system handles XML file updates, manages SFTP uploads, and provides a user-friendly interface for file operations and validation.

## User Preferences
Preferred communication style: Simple, everyday language.
Deployment workflow: Always confirm deployment requirements at the end of any changes or updates.
**Development Approval Process**: Before executing any development task, always provide "stack recommendation" including:
  - Model type (Standard/High-Performance)
  - Extended thinking (Yes/No)
  - Brief rationale for the choice
  - Wait for user approval before proceeding

## System Architecture

### Frontend
- **Template Engine**: Jinja2 with Bootstrap 5 (dark theme)
- **Client-side**: Vanilla JavaScript for interactive elements with improved download tracking
- **UI Framework**: Bootstrap 5 with custom CSS for responsive design
- **Icons**: Font Awesome 6.0

### Backend
- **Web Framework**: Flask (Python)
- **Database**: PostgreSQL with SQLAlchemy for schedules and logs
- **Authentication**: Flask-Login for secure user management
- **Background Processing**: APScheduler for automated tasks and Bullhorn monitoring (optimized for manual workflow)
- **XML Processing**: Custom processor utilizing `lxml` for managing job data with proper CDATA formatting, reference number generation, HTML consistency, and LinkedIn recruiter tags.
- **Email Service**: SendGrid for notifications and comprehensive email delivery logging.
- **SFTP Service**: Built-in SFTP client (disabled for manual workflow).
- **ATS Integration**: Real-time Bullhorn ATS/CRM monitoring for job changes, data mapping, and reference number generation.
- **Session Management**: Flask sessions with secure key
- **File Handling**: Secure temporary file storage with improved cleanup
- **Proxy Support**: ProxyFix middleware

### Core Features
- **Toggle-Based Automation Architecture** (September 2025):
    - **30-Minute Automated Upload Cycle**: APScheduler-backed automation that runs every 30 minutes when enabled via settings
    - **Dual Toggle Control**: Requires BOTH `automated_uploads_enabled=true` AND `sftp_enabled=true` for automation to execute
    - **Manual Workflow Support**: Can be fully disabled for manual-only operations by toggling settings OFF
    - **Fresh XML Generation**: Pulls from Bullhorn tearsheets (1256, 1264, 1499, 1556, 1257) on-demand for each refresh/upload
    - **STSI Company Formatting**: Properly formats company name as "STSI (Staffing Technical Services Inc.)" for tearsheet 1556
    - **Enhanced XML Processing**: HTML parsing to fix unclosed tags and CDATA wrapping for all XML fields
- **Environment Isolation & Safety** (October 2025):
    - **Environment-Aware Uploads**: Development and production environments upload to separate XML files to prevent cross-contamination
    - **Development Environment**: Uploads ONLY to `myticas-job-feed-v2-dev.xml`
    - **Production Environment**: Uploads ONLY to `myticas-job-feed-v2.xml`
    - **Separate Databases**: Development and production use completely isolated PostgreSQL databases
    - **Independent Schedules**: Each environment maintains its own 120-hour reference refresh schedule
    - **Zero Cross-Contamination**: Development workflows cannot affect production data or files
- **Orphan Prevention System** (October 2025): Automated duplicate detection and removal to prevent job pollution.
    - **Entity API Validation**: Uses Entity API as source of truth for tearsheet membership
    - **Smart Orphan Detection**: When Entity API shows fewer jobs than Search API, identifies and removes orphaned jobs that were removed from tearsheets
    - **Pagination Safeguard**: Association endpoint with proper start/count parameters ensures all Entity job IDs are collected
    - **Data Loss Protection**: Aborts orphan filtering if Entity pagination is incomplete, preventing legitimate jobs from being removed
    - **Robust Filtering**: Only removes jobs that Search API returns but Entity API doesn't recognize (true orphans)
- **Database-First Reference Number Architecture** (October 2025): 
    - **Single Source of Truth**: JobReferenceNumber database table is the authoritative storage for all reference numbers
    - **120-Hour Reference Refresh**: Updates reference numbers in database only (no SFTP upload) - eliminates upload conflicts
    - **30-Minute Upload Cycle**: SimplifiedXMLGenerator ALWAYS loads reference numbers from database before generating XML
    - **Conflict Resolution**: Prevents upload overwrites by making database the primary source, not SFTP or file snapshots
    - **Automatic Persistence**: All reference number changes (manual refresh, automated refresh) save to database immediately
- **Ad-hoc Reference Number Refresh**: Manual "Refresh All" button for immediate reference number updates with database persistence.
- **Job Application Form**: Responsive, public-facing form with resume parsing (Word/PDF), auto-population of candidate fields, and Bullhorn job ID integration. Supports unique branding.
- **Internal Job Classification**: Keyword-based classification system providing instant, reliable categorization (jobfunction, jobindustries, senioritylevel) without external API dependencies.
- **Intelligent File Management**: Automated file consolidation, duplicate detection, temporary file cleanup, and storage optimization.
- **Dual-Domain Architecture**: Configured for `jobpulse.lyntrix.ai` (main app) and `apply.myticas.com` (job application forms) with environment-aware URL generation.
- **Optimized Monitoring System**: Health checks every 2 hours (reduced from 15 minutes) for manual workflow efficiency, with timeout protection and scheduler auto-restarts.
- **Health Endpoints**: Optimized, ultra-fast dedicated health endpoints (`/health`, `/ready`, `/alive`, `/ping`).
- **XML Generation Enhancements** (September 2025): All XML fields now wrapped in CDATA sections for proper data handling, HTML descriptions parsed with lxml for proper tag closure.
- **Simplified XML Generator** (September 2025): Direct Bullhorn integration that pulls from all tearsheets (1256, 1264, 1499, 1556) and generates clean XML on-demand with improved download completion tracking.

### Technical Implementation Details
- **XML Processing**: Requires root element 'source' and specific required elements (title, company, date, referencenumber). Preserves existing reference numbers during ad-hoc changes.
- **File Upload Constraints**: XML files only, max 50MB, temporary storage, secure filename handling.
- **Error Handling**: Comprehensive XML syntax error catching, user-friendly messages, server-side logging, client-side validation.
- **HTML Formatting Consistency**: Ensures consistent HTML markup within CDATA sections.
- **Resume Parsing**: Extracts contact information from Word and PDF formats.

### Database Seeding & Auto-Configuration (October 2025)
**Zero-touch production deployment system that automatically configures admin users, SFTP settings, Bullhorn credentials, tearsheet monitors, and all automation from environment secrets.**

#### Environment-Aware Seeding
- **Development Environment**: Auto-seeding with safe defaults for testing
- **Production Environment**: Full auto-configuration from deployment secrets
- **Idempotent Design**: Safe to run multiple times without creating duplicates
- **Automatic Execution**: Runs on every app startup after `db.create_all()`
- **Zero Manual Setup**: Production deploys 100% configured and ready to run

#### Required Environment Secrets
**Admin User:**
- `ADMIN_USERNAME` - Admin username (default: `admin`)
- `ADMIN_EMAIL` - Admin email (default: `kroots@myticas.com`)
- `ADMIN_PASSWORD` - **REQUIRED for production** (no default for security)

**SFTP Configuration:**
- `SFTP_HOSTNAME` - SFTP server hostname (e.g., `yourdomain.sftp.wpengine.com`)
- `SFTP_USERNAME` - SFTP username
- `SFTP_PASSWORD` - SFTP password
- `SFTP_PORT` - SFTP port (default: `22`)
- `SFTP_DIRECTORY` - Upload directory (default: `/`)

**Bullhorn API Configuration:**
- `BULLHORN_CLIENT_ID` - Bullhorn OAuth client ID
- `BULLHORN_CLIENT_SECRET` - Bullhorn OAuth client secret
- `BULLHORN_USERNAME` - Bullhorn API username
- `BULLHORN_PASSWORD` - Bullhorn API password

#### What Gets Auto-Configured
**On every production deployment, the seeding system automatically creates:**
1. ✅ **Admin User** - Created with credentials from `ADMIN_PASSWORD` secret
2. ✅ **SFTP Settings** - All connection details populated from secrets
3. ✅ **Bullhorn API Credentials** - OAuth and API credentials configured
4. ✅ **5 Bullhorn Tearsheet Monitors** - Pre-configured and active:
   - Sponsored - OTT (1256)
   - Sponsored - CHI (1257)
   - Sponsored - VMS (1264)
   - Sponsored - GR (1499)
   - Sponsored - STSI (1556)
5. ✅ **Automation Toggles** - `automated_uploads_enabled` and `sftp_enabled` set to `true`
6. ✅ **Environment Monitoring** - Production health monitoring configured

**Production Deployment (Zero-Touch):**
1. Add all required secrets to Replit App Secrets (one-time setup)
2. Click "Republish" to deploy to Reserved VM
3. Seeding runs automatically on startup
4. **Everything is configured and running** - login and verify!

**No manual steps required:**
- ❌ No settings page configuration
- ❌ No toggle switching
- ❌ No credential entry
- ❌ No monitor setup
- ✅ **Just login and it works!**

**Credential Rotation:**
- All settings automatically update from environment variables on app restart
- To rotate: Update deployment secrets → Republish or restart app
- Changes are logged: `🔄 Updated settings: bullhorn_password, sftp_password`

#### Development vs Production Databases
**Two-Database Architecture:**
- **Development Database** (workspace): For testing, contains dev user accounts
- **Production Database** (Reserved VM): Starts fresh, populated via seeding

**Why Separate:**
- Development data (test accounts, sample jobs) stays isolated
- Production database starts clean with only necessary baseline data
- No test data pollution in production environment

#### Adding More Users
**Method 1: Update Seeding Script (Recommended)**
```python
# Edit seed_database.py
# Add users to create_admin_user() or create new seeding function
```

**Method 2: Manual SQL (Quick)**
```sql
-- Via Database tool in Replit
INSERT INTO "user" (username, email, password_hash, is_admin, created_at)
VALUES ('newuser', 'user@example.com', '<hashed_password>', false, NOW());
```

**Method 3: Admin Interface**
- Future enhancement: Web UI for user management
- Currently: Use SQL or seeding script

#### Troubleshooting Production Login
**Issue**: Cannot log into `jobpulse.lyntrix.ai`
**Cause**: Production database is empty (no users from dev database)
**Solution**: 
1. Verify `ADMIN_PASSWORD` secret is set in deployment
2. Check logs for seeding success: `🌱 Database seeding: Created admin user`
3. If seeding failed, check for errors in startup logs
4. Restart deployment to re-trigger seeding

## External Dependencies

### Python Libraries
- **Flask**: Web framework
- **lxml**: XML parsing
- **SQLAlchemy**: ORM for database interactions
- **APScheduler**: Background task scheduling
- **Flask-Login**: User session management
- **SendGrid**: Email notifications
- **OpenAI**: AI-powered job classification

### Frontend Libraries
- **Bootstrap 5**: UI framework
- **Font Awesome 6**: Icon library