# XML Job Feed Reference Number Updater

## Overview
This Flask-based web application automates the processing of XML job feed files to update reference numbers and synchronize job listings with Bullhorn ATS/CRM. Its primary purpose is to maintain accurate, real-time job listings, streamline application workflows, and enhance job visibility and efficiency. The system manages XML updates, integrates with SFTP, and provides a user-friendly interface for file operations and validation. The project aims to improve recruitment processes by ensuring data integrity and automating repetitive tasks, ultimately enhancing market potential by offering a robust solution for job feed management.

## User Preferences
Preferred communication style: Simple, everyday language.
Deployment workflow: Always confirm deployment requirements at the end of any changes or updates.
Development Approval Process: Before executing any development task, always provide "stack recommendation" including:
  - Model type (Standard/High-Performance)
  - Extended thinking (Yes/No)
  - Brief rationale for the choice
  - Wait for user approval before proceeding

## System Architecture

### UI/UX Decisions
- **Template Engine**: Jinja2 with Bootstrap 5 (dark theme) for a responsive and modern user interface.
- **Client-side**: Vanilla JavaScript for interactive elements.
- **Icons**: Font Awesome 6.0.
- **Dual-Domain Architecture**: Supports distinct functionalities on `jobpulse.lyntrix.ai` (main app) and `apply.myticas.com` (job application forms).

### Technical Implementations
- **Web Framework**: Flask (Python).
- **Database**: PostgreSQL with SQLAlchemy, including timezone handling for Eastern Time.
- **Authentication**: Flask-Login for secure user management and session persistence.
- **Background Processing**: APScheduler manages automated tasks like Bullhorn monitoring.
- **XML Processing**: Custom `lxml` processor for data handling, reference number generation, and HTML consistency within XML.
- **Email Service**: SendGrid for notifications and delivery logging.
- **File Handling**: Secure temporary file storage with cleanup, supporting XML files only (max 50MB).
- **Error Handling**: Robust XML syntax error detection, user-friendly messages, and comprehensive server-side logging.
- **Proxy Support**: ProxyFix middleware is used for proxy environments.

### Feature Specifications
- **Dual-Cycle Monitoring**: Features 5-minute tearsheet monitoring for UI visibility and 30-minute automated SFTP upload cycles, controlled by independent toggles.
- **Smart Tearsheet Auto-Cleanup**: Automatically removes ineligible (closed, blocked status) jobs from tearsheets during monitoring, logging removals for audit.
- **Real-Time Email Notifications**: Instant email alerts for new jobs added to monitored tearsheets.
- **Environment Isolation**: Separate development and production environments with distinct configurations to prevent data cross-contamination.
- **Orphan Prevention**: Automated duplicate job detection using Bullhorn Entity API to ensure data integrity.
- **Database-First Reference Numbers**: `JobReferenceNumber` table is the single source of truth, updated periodically, with an ad-hoc refresh option.
- **Job Application Form**: Public-facing form with resume parsing (Word/PDF), Bullhorn integration, and unique branding.
- **Resume HTML Formatting**: Converts resume content to HTML for cleaner display in Bullhorn's "Parsed" Resume pane, utilizing a three-layer PDF processing approach:
  1. **PyMuPDF Extraction**: Primary text extraction preserving block spacing
  2. **Deterministic Text Normalization**: Fixes concatenated words (e.g., "PROFESSIONALSUMMARYAnIT" → "PROFESSIONAL SUMMARY An IT") using Unicode whitespace cleanup, non-breaking space normalization, and camelCase boundary detection
  3. **GPT-4o AI Formatting**: Structures content into semantic HTML (headings, paragraphs, bullet lists) with explicit spacing instructions for clean display
- **Keyword-Based Job Classification**: Rapidly categorizes jobs using keyword dictionaries for LinkedIn's taxonomy, with weighted scoring and guaranteed defaults.
- **Intelligent File Management**: Automated consolidation, duplicate detection, and temporary file cleanup.
- **Zero-Job Detection Safeguard**: Prevents XML corruption from empty Bullhorn API responses by blocking updates, creating backups, and sending alerts.
- **Zero-Touch Production Deployment**: Environment-aware database seeding and auto-configuration for critical services and users from environment secrets.
- **AI Candidate Vetting (Premium Add-on)**: An automated candidate-job matching system using GPT-4o, featuring comprehensive detection of inbound applicants, configurable batch processing, resume analysis, AI matching with scoring and detailed explanations, Bullhorn note creation, recruiter notifications, and an audit dashboard.
- **Location-Aware Matching**: AI vetting applies different location rules based on work type - Remote jobs require same country, while On-site/Hybrid prefer same metro area with appropriate score penalties for mismatches.
- **Automatic Job Change Detection**: During each 5-minute monitoring cycle, compares Bullhorn's `dateLastModified` against stored AI interpretation timestamps. When jobs are modified, automatically re-extracts AI requirements while preserving any custom requirement overrides.
- **Vetting System Health Monitoring**: Automated health checks every 10 minutes monitoring Bullhorn connectivity, OpenAI API availability, database status, and scheduler state. Features a dashboard panel showing real-time status, email alerts for critical failures (with 1-hour throttling), and configurable health alert email address.

## External Dependencies

### Python Libraries
- **Flask**: Web framework for building the application.
- **lxml**: Library for efficient XML parsing and manipulation.
- **SQLAlchemy**: ORM for interacting with the PostgreSQL database.
- **APScheduler**: Python library for scheduling in-process background tasks.
- **Flask-Login**: Provides user session management for authentication.
- **SendGrid**: Email platform for sending notifications and tracking delivery.

### Frontend Libraries
- **Bootstrap 5**: CSS framework for developing responsive and mobile-first websites.
- **Font Awesome 6**: Icon library for scalable vector graphics.

## Strategic Planning

### Multi-Tenant SaaS Roadmap
A comprehensive strategic planning document exists at `JOBPULSE_MULTI_TENANT_ROADMAP.md` covering:
- Current single-tenant architecture analysis
- Target multi-tenant SaaS vision for 50+ customers
- Multi-ATS adapter framework (Bullhorn, Greenhouse, Workday)
- Role-based access control (Super Admin, Customer Admin, Recruiter)
- 4-phase implementation roadmap (18-26 weeks total)
- Infrastructure scaling recommendations
- Database schema evolution plan