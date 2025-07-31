# XML Job Feed Reference Number Updater

## Overview
This Flask-based web application processes XML job feed files to update reference numbers, offering a user-friendly interface for file uploads, validation, and automated processing. Its core purpose is to ensure proper reference number formatting in job feeds, integrate with Bullhorn ATS/CRM for real-time job synchronization, and automatically manage XML file updates and SFTP uploads. The system aims to provide a robust, automated solution for maintaining accurate and up-to-date job listings.

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
- **Automated Workflow**: Monitors Bullhorn tearsheets every 5 minutes, syncs job changes to XML, regenerates reference numbers, uploads to SFTP, and sends email notifications. Ensures XML consistency with all active monitors.
- **XML Integration Service**: Handles job additions, removals, and updates in XML files, ensuring job IDs are formatted and reference numbers generated. **HTML Consistency Fixed (July 31, 2025)**: All job descriptions now have consistent HTML formatting within CDATA sections.
- **UI/UX**: Responsive dark-themed interface with real-time feedback and progress indicators.
- **Security**: Login-protected routes and admin user management.

### Technical Implementation Details
- **XML Processing Requirements**: Root element 'source', required elements (title, company, date, referencenumber), validation on first 10 jobs.
- **File Upload Constraints**: XML files only, max 50MB, temporary storage, secure filename handling.
- **Error Handling**: Comprehensive XML syntax error catching, user-friendly flash messages, server-side logging, client-side validation.
- **AI-Powered Job Classification**: Integrates OpenAI GPT-4o to classify jobs (jobfunction, jobindustries, senoritylevel) based on title/description, using predefined Excel-based mappings.
- **HTML Formatting Consistency**: Ensures all job descriptions have consistent HTML markup by converting HTML entities (e.g., `&lt;strong&gt;`) to proper HTML tags (e.g., `<strong>`) within CDATA sections.

## External Dependencies

### Python Libraries
- **Flask**: Web framework
- **lxml**: XML parsing
- **Werkzeug**: WSGI utilities and secure file handling
- **SQLAlchemy**: ORM for database interactions
- **APScheduler**: Background task scheduling
- **Flask-Login**: User session management
- **SendGrid**: Email notifications (requires account)
- **OpenAI**: AI-powered job classification

### Frontend Libraries
- **Bootstrap 5**: UI framework
- **Font Awesome 6**: Icon library