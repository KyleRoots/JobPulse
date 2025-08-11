# XML Job Feed Reference Number Updater

## Overview
This Flask-based web application automates the processing of XML job feed files to update reference numbers and synchronize job listings with Bullhorn ATS/CRM. It ensures correct reference number formatting, manages XML file updates, handles SFTP uploads, and provides a user-friendly interface for file uploads and validation. The system aims to provide a robust and automated solution for maintaining accurate and classified job listings, ensuring real-time synchronization and a seamless application experience, thereby enhancing job visibility and streamlining application workflows.

**STATUS (2025-08-11)**: **COMPLETE FIELD REMAPPING WITH REFERENCE PRESERVATION**: System performs 100% field remapping from Bullhorn to XML for ALL existing jobs on EVERY monitoring cycle while PRESERVING existing reference numbers. **REFERENCE NUMBER STABILITY**: Reference numbers remain unchanged during 5-minute sync cycles and are only updated during weekly scheduled automation. **SERVER TIMESTAMP TRACKING**: Scheduler page displays actual SFTP upload times matching live server timestamps for accurate troubleshooting. **MONITORING INTERVAL**: Extended to 5 minutes to accommodate complete remapping of all ~50 jobs. **DATA INTEGRITY**: Every job field (except reference numbers) is fully refreshed from Bullhorn each cycle, ensuring absolute accuracy. **ALL 8 STEPS OPERATIONAL**: Complete monitoring with fetch, add, remove, REMAP ALL (preserving refs), upload (SFTP port 2222), email, HTML fixes, and audit verification.

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
- **Enhanced 8-Step Monitoring System with Complete Field Remapping** (Updated 2025-08-10): Every 5 minutes, performs complete data refresh with 100% accuracy guarantee:
  1. Fetches ALL jobs from monitored tearsheets in Bullhorn
  2. Adds new jobs from tearsheets to XML
  3. Removes jobs no longer in tearsheets
  4. **COMPLETE REMAPPING**: Re-maps ALL fields for every existing job from Bullhorn (ensures 100% data accuracy)
  5. Uploads all changes to web server (SFTP port 2222)
  6. Batches email notifications for efficiency
  7. Reviews and fixes CDATA/HTML formatting
  8. Runs FULL AUDIT with automatic corruption detection - uploads clean local XML when orphaned jobs detected on live server
- **Real-Time Progress Tracking**: Visual progress indicators [●●●●●●●○] show current step (Step 1/8 through Step 8/8)
- **Enhanced Audit Reporting**: Detailed summaries of discrepancies found and corrections made
- **Upload Failure Monitoring**: Comprehensive logging of SFTP connection issues in activity monitoring system with detailed diagnostics for troubleshooting
- **Comprehensive Status Logging**: Step-by-step progress updates with clear indicators
- **XML Integration Service**: Manages job data with proper CDATA formatting, reference number generation, HTML consistency, and LinkedIn recruiter tags. Includes automatic backups, structure validation, duplicate prevention, and MD5 checksums.
- **Job Application Form**: Responsive, public-facing form with resume parsing (extracts contact info from Word/PDF), auto-population of candidate fields, structured email submission, and Bullhorn job ID integration. Features Myticas Consulting branding, dark blue gradient background, glass morphism effects, and supports unique job URLs. Includes robust duplicate prevention and form lockdown mechanisms.
- **UI/UX**: Responsive dark-themed interface with real-time feedback and progress indicators.
- **Security**: Login-protected routes and admin user management.
- **AI-Powered Job Classification**: Integrates OpenAI GPT-4o to classify jobs (jobfunction, jobindustries, senioritylevel).
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