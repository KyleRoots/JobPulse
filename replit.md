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
- **Session Management**: Flask sessions with secure keys
- **File Handling**: Secure temporary file storage with cleanup, supporting XML files only (max 50MB).
- **Error Handling**: Comprehensive XML syntax error catching, user-friendly messages, server-side logging, client-side validation.
- **Proxy Support**: ProxyFix middleware

### Feature Specifications
- **Automated Uploads**: 30-minute automated upload cycle, controlled by dual toggles (`automated_uploads_enabled` and `sftp_enabled`). Supports manual-only operations.
- **Environment Isolation**: Separate development and production environments, including distinct XML upload targets (`-dev.xml` vs. `.xml`), isolated PostgreSQL databases, and independent schedules to prevent cross-contamination.
- **Orphan Prevention**: Automated duplicate detection and removal using Bullhorn Entity API for validation against Search API results, preventing job pollution and ensuring data integrity.
- **Database-First Reference Numbers**: `JobReferenceNumber` table is the single source of truth for all reference numbers, updated every 120 hours without SFTP uploads. SimplifiedXMLGenerator loads reference numbers from the database.
- **Ad-hoc Reference Number Refresh**: Manual "Refresh All" option for immediate database updates.
- **Job Application Form**: Public-facing form with resume parsing (Word/PDF), auto-population of candidate fields, Bullhorn job ID integration, and unique branding.
- **AI-Powered Job Classification**: Uses OpenAI GPT-5 with LinkedIn's official taxonomy for job functions, industries, and seniority levels. Includes a keyword-based fallback system and optimized for batch processing.
- **Intelligent File Management**: Automated consolidation, duplicate detection, and temporary file cleanup.
- **Streamlined Automation**: A single 30-minute cycle for pulling fresh jobs, applying AI classification, and SFTP upload. Includes 2-hour health checks.
- **Health Endpoints**: Optimized `/health`, `/ready`, `/alive`, `/ping` endpoints.
- **XML Generation Enhancements**: All XML fields wrapped in CDATA, HTML descriptions parsed with `lxml` for tag closure.
- **Zero-Touch Production Deployment**: Environment-aware database seeding and auto-configuration for admin users, SFTP, Bullhorn credentials, tearsheet monitors, and automation toggles from environment secrets. Idempotent design preserves user settings post-initial deployment.

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