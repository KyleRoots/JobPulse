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
- **Manual Workflow Architecture** (September 2025):
    - **Enhanced 8-Step Comprehensive Monitoring**: Bullhorn-focused data integrity system that fetches, adds, removes, and re-maps all job fields from Bullhorn tearsheets every 5 minutes, ensuring 100% data accuracy for manual downloads. Properly formats STSI company name as "STSI (Staffing Technical Services Inc.)" for tearsheet 1556. **Enhanced with proper HTML parsing to fix unclosed tags and CDATA wrapping for all XML fields**.
    - **Manual Download Notifications**: Change detection and email notifications are triggered ONLY during manual XML downloads, providing relevant change summaries when users actually download files.
    - **SFTP Auto-Upload DISABLED**: All automatic uploads removed - system focuses on job counting transparency and manual download workflow.
- **Orphan Prevention System**: Automated duplicate detection and removal to prevent job pollution.
- **Ad-hoc Reference Number Refresh**: Manual "Refresh All" button for immediate reference number updates (no automatic uploads).
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