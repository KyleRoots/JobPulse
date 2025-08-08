# XML Job Feed Reference Number Updater

## Overview
This Flask-based web application automates the processing of XML job feed files to update reference numbers and synchronize job listings with Bullhorn ATS/CRM. It ensures correct reference number formatting, manages XML file updates, handles SFTP uploads, and provides a user-friendly interface for file uploads and validation. The system aims to provide a robust and automated solution for maintaining accurate and classified job listings, ensuring real-time synchronization and a seamless application experience, thereby enhancing job visibility and streamlining application workflows.

## User Preferences
Preferred communication style: Simple, everyday language.

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
- **FTP Service**: Built-in FTP client for file uploads
- **ATS Integration**: Real-time Bullhorn ATS/CRM monitoring for job changes, data mapping, and reference number generation.
- **Session Management**: Flask sessions with secure key
- **File Handling**: Secure temporary file storage
- **Proxy Support**: ProxyFix middleware

### Core Features
- **Automated Workflow**: Monitors Bullhorn, syncs job changes to XML, regenerates reference numbers (for modified jobs), uploads to SFTP, and sends email notifications with detailed change summaries. Modified jobs receive new reference numbers and are prioritized in the XML feed for enhanced visibility.
- **XML Integration Service**: Manages job additions, removals, and updates in XML files, ensuring proper formatting, reference number generation, HTML consistency fixes, and LinkedIn recruiter tag integration. Includes safeguards like automatic backups, structure validation, duplicate detection, and MD5 checksums. Implements a comprehensive field sync service to ensure all Bullhorn fields are accurately reflected in XML, with automatic duplicate removal and field mismatch correction.
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