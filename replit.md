# XML Job Feed Reference Number Updater

## Overview
This Flask-based web application automates the processing of XML job feed files to update reference numbers, integrating with Bullhorn ATS/CRM for real-time job synchronization. Its primary purpose is to ensure correct reference number formatting, manage XML file updates, and handle SFTP uploads, providing a robust and automated solution for maintaining accurate job listings. The system offers a user-friendly interface for file uploads, validation, and automated processing.

## User Preferences
Preferred communication style: Simple, everyday language.

## System Architecture

### Frontend
- **Template Engine**: Jinja2 with Bootstrap 5 (dark theme)
- **Client-side**: Vanilla JavaScript for drag-and-drop
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
- **ATS Integration**: Real-time Bullhorn ATS/CRM monitoring for job changes, mapping Bullhorn data to XML, and generating unique reference numbers for new jobs.
- **Session Management**: Flask sessions with secure key
- **File Handling**: Secure temporary file storage
- **Proxy Support**: ProxyFix middleware

### Core Features
- **Automated Workflow**: Monitors Bullhorn tearsheets, syncs job changes to XML, regenerates reference numbers, uploads to SFTP, and sends email notifications.
- **XML Integration Service**: Handles job additions, removals, and updates in XML files, ensuring job IDs are formatted and reference numbers generated. Includes HTML consistency fixes and integration of LinkedIn recruiter tags by converting names to branded tags (e.g., "Michael Theodossiou" → "#LI-MIT").
- **UI/UX**: Responsive dark-themed interface with real-time feedback and progress indicators.
- **Security**: Login-protected routes and admin user management.

### Technical Implementation Details
- **XML Processing Requirements**: Root element 'source', required elements (title, company, date, referencenumber), validation on first 10 jobs.
- **Reference Number Preservation**: Existing reference numbers must be preserved during ad-hoc XML changes; new numbers are generated only for new jobs or during scheduled rebuilds.
- **File Upload Constraints**: XML files only, max 50MB, temporary storage, secure filename handling.
- **Error Handling**: Comprehensive XML syntax error catching, user-friendly flash messages, server-side logging, client-side validation.
- **AI-Powered Job Classification**: Integrates OpenAI GPT-4o to classify jobs (jobfunction, jobindustries, senoritylevel) based on title/description.
- **HTML Formatting Consistency**: Ensures consistent HTML markup within CDATA sections by converting HTML entities.
- **Email Delivery Logging Architecture**: Comprehensive email tracking system with EmailDeliveryLog database model, tracks notification type, job details, delivery status, SendGrid message IDs, error messages, and changes summary. Includes web dashboard at /email-logs.
- **Enhanced EmailService Integration**: EmailService class supports database logging for all email notifications, including individual job change notifications and bulk scheduled processing notifications.
- **XML Safeguards**: Implemented `xml_safeguards.py` for automatic backups, XML structure validation, duplicate detection, file size verification, MD5 checksums, and rollback capabilities.
- **Intelligent File Management**: Automated file consolidation with `file_consolidation_service.py` for backup archiving, duplicate detection, temp file cleanup, and storage optimization. Includes UI controls and daily scheduled cleanup.

## External Dependencies

### Python Libraries
- **Flask**: Web framework
- **lxml**: XML parsing
- **Werkzeug**: WSGI utilities and secure file handling
- **SQLAlchemy**: ORM for database interactions
- **APScheduler**: Background task scheduling
- **Flask-Login**: User session management
- **SendGrid**: Email notifications
- **OpenAI**: AI-powered job classification

### Frontend Libraries
- **Bootstrap 5**: UI framework
- **Font Awesome 6**: Icon library