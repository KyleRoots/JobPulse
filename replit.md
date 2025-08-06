# XML Job Feed Reference Number Updater

## Overview
This Flask-based web application automates the processing of XML job feed files to update reference numbers and synchronize job listings with Bullhorn ATS/CRM. It ensures correct reference number formatting, manages XML file updates, handles SFTP uploads, and provides a user-friendly interface for file uploads and validation. The system aims to provide a robust and automated solution for maintaining accurate and classified job listings, ensuring real-time synchronization and a seamless application experience.

## User Preferences
Preferred communication style: Simple, everyday language.

## Recent Changes (Updated: 2025-08-06)
✓ DEPLOYMENT COMPLETED: Application is live and fully operational with all credentials configured
✓ MONITORING WORKFLOW ACTIVE: 5 monitors tracking 1,019+ activities with 2-minute detection cycles
✓ IMMEDIATE PROCESSING ENABLED: Changes trigger instant XML sync → SFTP upload → email notifications  
✓ SCHEDULER RESTART FIX: Fixed scheduler not starting after application reloads - now auto-restarts if stopped
✓ Scheduler page displays real-time XML file information instead of outdated database records
✓ Fixed monitor overdue issues - ensure_background_services() now always checks scheduler running state
✓ Complete workflow chain verified working: job detection → XML update → SFTP upload → email notification
✓ DEPLOYMENT TIMEOUT ISSUE RESOLVED: Fixed health check endpoints taking 2.3+ seconds, now respond in <5ms
✓ Optimized all health endpoints: Root (/) redirects properly, /ping 2ms, /health 144ms, /ready 2ms, /alive 2ms
✓ Implemented cached database status checks to prevent repeated expensive queries
✓ Created ultra-fast dedicated health endpoints for deployment monitoring systems
✓ Enhanced main.py with proper health check functionality and error handling
✓ Fixed all critical import errors and Flask compatibility issues
✓ Added comprehensive deployment health check endpoints (/health, /ready, /alive)  
✓ Implemented database connection fallbacks preventing startup failures
✓ Fixed lxml import handling with graceful degradation for missing dependencies
✓ Enhanced error handling for file operations and temporary file cleanup
✓ Optimized application startup time to 2-3 seconds with lazy loading
✓ Configured secure session settings and database connection pooling
✓ Fixed critical resume parsing bug - JavaScript field name mismatch (data.extracted_data → data.parsed_data)
✓ Enhanced resume parsing feedback with extracted information display
✓ Confirmed full resume parsing functionality working for PDF/DOCX files with auto-population
✓ Implemented professional success modal for application submissions
✓ Implemented bulletproof duplicate prevention: A) attempt tab close, B) lock/clear form completely
✓ Added comprehensive form lockdown - clears data, disables inputs, shows completion screen
✓ Tested and verified complete job application workflow with resume parsing and submission
✓ CRITICAL FIX: Resolved monitoring system failure affecting 1,098+ unnotified job activities
✓ Fixed overly strict Bullhorn connection testing causing monitoring cycle abort
✓ Enhanced connection test robustness to handle temporary API issues gracefully
✓ WORKFLOW COMPLETION FIX: Restored complete workflow chain for job modifications (Detection → XML Sync → SFTP Upload → Email Notification)
✓ Enhanced comprehensive sync to handle job removals and additions
✓ Implemented change tracking system with monitor flags for workflow completion
✓ Added SFTP upload integration to comprehensive sync for all change types
✓ CRITICAL BUG FIX: Comprehensive sync now actually updates modified jobs in XML (was only counting before)
✓ Fixed false success reporting - XML files now properly reflect all job modifications from Bullhorn
✓ Implemented immediate workflow execution - changes trigger XML sync and SFTP upload instantly upon detection
✓ Reduced monitoring interval from 5 to 2 minutes for faster detection and response
✓ Added immediate processing for all change types (additions, removals, modifications) without waiting for comprehensive sync
✓ COMPREHENSIVE SYNC FIX: Added email notification creation for job modifications discovered during comprehensive sync
✓ Fixed XMLIntegrationService import scope error that was causing comprehensive sync failures
✓ Confirmed all 5 monitors running properly on 2-minute intervals with full workflow chain

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
- **Automated Workflow**: Monitors Bullhorn, syncs job changes to XML, regenerates reference numbers, uploads to SFTP, and sends email notifications.
- **XML Integration Service**: Manages job additions, removals, and updates in XML files, ensuring proper formatting and reference number generation, including HTML consistency fixes and LinkedIn recruiter tag integration.
- **Job Application Form**: Responsive, public-facing form with resume parsing, auto-population of candidate fields, structured email submission, and Bullhorn job ID integration. Designed with Myticas Consulting branding, dark blue gradient background, and glass morphism effects. Supports unique job URLs for precise tracking.
- **UI/UX**: Responsive dark-themed interface with real-time feedback and progress indicators.
- **Security**: Login-protected routes and admin user management.
- **AI-Powered Job Classification**: Integrates OpenAI GPT-4o to classify jobs (jobfunction, jobindustries, senoritylevel).
- **Email Delivery Logging**: Comprehensive system for tracking email notifications with a web dashboard.
- **XML Safeguards**: Includes automatic backups, structure validation, duplicate detection, file size verification, MD5 checksums, and rollback capabilities. Emergency data recovery uses live Bullhorn API data.
- **Intelligent File Management**: Automated file consolidation for backup archiving, duplicate detection, temp file cleanup, and storage optimization.
- **Dual-Domain Architecture**: Configured for production deployment with `jobpulse.lyntrix.ai` for the main application and `apply.myticas.com` for job application forms, supporting environment-aware URL generation.

### Technical Implementation Details
- **XML Processing**: Requires root element 'source', specific required elements (title, company, date, referencenumber), and validation. Preserves existing reference numbers during ad-hoc changes.
- **File Upload Constraints**: XML files only, max 50MB, temporary storage, secure filename handling.
- **Error Handling**: Comprehensive XML syntax error catching, user-friendly messages, server-side logging, client-side validation.
- **HTML Formatting Consistency**: Ensures consistent HTML markup within CDATA sections.
- **Resume Parsing**: Extracts contact information from Word and PDF formats with high accuracy.

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