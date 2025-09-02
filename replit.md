# XML Job Feed Reference Number Updater

## Overview
This Flask-based web application automates the processing of XML job feed files to update reference numbers and synchronize job listings with Bullhorn ATS/CRM. Its primary purpose is to maintain accurate job listings, ensure real-time synchronization, and streamline application workflows, thereby enhancing job visibility and efficiency. The system handles XML file updates, manages SFTP uploads, and provides a user-friendly interface for file operations and validation.

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
    - **Enhanced 8-Step Comprehensive Monitoring**: Bullhorn-focused data integrity system that fetches, adds, removes, and re-maps all job fields from Bullhorn tearsheets, ensuring 100% data accuracy and uploading changes to the web server. Properly formats STSI company name as "STSI (Staffing Technical Services Inc.)" for tearsheet 1556.
    - **Live XML Change Monitor**: Primary email notification system that downloads the live XML, compares it with previous snapshots, and sends focused email notifications only for actual job content changes.
- **Orphan Prevention System**: Automated duplicate detection and removal to prevent job pollution.
- **Ad-hoc Reference Number Refresh**: Manual "Refresh All" button for immediate reference number updates with SFTP upload and notifications.
- **Job Application Form**: Responsive, public-facing form with resume parsing (Word/PDF), auto-population of candidate fields, and Bullhorn job ID integration. Supports unique branding.
- **AI-Powered Job Classification**: Integrates OpenAI GPT-4o to classify jobs (jobfunction, jobindustries, senioritylevel).
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
- **SQLAlchemy**: ORM for database interactions
- **APScheduler**: Background task scheduling
- **Flask-Login**: User session management
- **SendGrid**: Email notifications
- **OpenAI**: AI-powered job classification

### Frontend Libraries
- **Bootstrap 5**: UI framework
- **Font Awesome 6**: Icon library