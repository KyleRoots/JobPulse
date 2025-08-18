# XML Job Feed Reference Number Updater

## Overview
This Flask-based web application automates the processing of XML job feed files to update reference numbers and synchronize job listings with Bullhorn ATS/CRM. It provides a robust, automated solution for maintaining accurate job listings, ensuring real-time synchronization, and streamlining application workflows, thereby enhancing job visibility. The system ensures correct reference number formatting, manages XML file updates, handles SFTP uploads, and offers a user-friendly interface for file uploads and validation.

## Recent Critical Fix (Aug 18, 2025)
**Reference Number Flip-Flopping Bug Fixed**: Resolved critical issue where reference numbers were changing every 5 minutes between two different values. ROOT CAUSE: The comprehensive monitoring service was using outdated XML snapshots to "preserve" reference numbers, overriding manual refresh changes. SOLUTION: Eliminated snapshot system - monitoring service now always reads current XML state in real-time, preventing conflicts with manual refreshes. Reference numbers are now properly preserved during routine monitoring and only change during manual "Refresh All" operations.

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