# XML Job Feed Reference Number Updater

## Overview
This Flask-based web application automates the processing of XML job feed files to update reference numbers and synchronize job listings with Bullhorn ATS/CRM. It ensures correct reference number formatting, manages XML file updates, handles SFTP uploads, and provides a user-friendly interface for file uploads and validation. The system aims to provide a robust and automated solution for maintaining accurate and classified job listings, ensuring real-time synchronization and a seamless application experience.

## User Preferences
Preferred communication style: Simple, everyday language.

## Recent Changes (Updated: 2025-08-08 - 18:08)
✓ MODIFIED JOB VISIBILITY ENHANCEMENT: Modified jobs now get NEW reference numbers and move to top of XML file for fresh visibility, treating updates like new postings
✓ DUPLICATE PREVENTION SYSTEM: Permanent solution to recurring duplicate issue - 10 Clover tearsheet jobs creating triples fixed, duplicate checking now integrated into add_job_to_xml() function
✓ RAPID CHANGE TRACKING SYSTEM: Implemented RapidChangeTracker class to detect and report multiple job state transitions within single 2-minute monitoring cycles
✓ ENHANCED EMAIL NOTIFICATIONS: Added rapid change alerts showing job lifecycle (added→removed→modified) with timestamps and owner information
✓ COMPREHENSIVE STATE MONITORING: Tracker captures all state changes per job with full timeline and aggregates for comprehensive notifications
✓ SCHEDULER PAGE ENHANCED: XML files now display real-time upload timestamps and file sizes
✓ SFTP TIMESTAMP TRACKING: Comprehensive sync updates last_file_upload when uploading to server
✓ DUPLICATE EMAIL FIX: Prevented duplicate job removal emails by checking if notification already queued for monitor
✓ TIMEOUT PROTECTION: Implemented 110-second timeout to prevent monitoring cycles from exceeding 2-minute window
✓ SMART TIME MANAGEMENT: Comprehensive sync skips automatically if less than 20 seconds remain
✓ OVERDUE PREVENTION: Inline time checking (thread-safe) stops long-running monitors gracefully
✓ CRITICAL SFTP BUG FIX: Fixed missing FTPService import and wrong protocol (FTP vs SFTP) in comprehensive sync
✓ JOB REMOVALS WORKING: Jobs 32266, 32269, 32293 successfully removed from live server
✓ DESCRIPTION CHANGE DETECTION FIX: Added 'description' field to comparison logic so job description changes are now properly detected and synced
✓ EMAIL NOTIFICATION ENHANCEMENT: Modified jobs now include detailed field change summaries in email notifications during comprehensive sync
✓ FIELD MONITORING EXPANSION: Enhanced compare_job_lists to monitor all critical fields (description, publicDescription, address, employmentType, assignedUsers, owner) for comprehensive change detection
✓ EMAIL NOTIFICATION FIX VERIFIED: Job modifications now display detailed field changes with before/after values in email notifications (confirmed working for job 34080)
✓ Monitoring auto-restart implemented - scheduler starts automatically on every app reload
✓ Updated xml_integration_service.py to automatically add '1' to all future job recruiter tags
✓ Created backups before modifying XML files (myticas-job-feed.xml and myticas-job-feed-scheduled.xml)
✓ Verified tag mapping: All recruiter names now map to #LI-XX1 format (e.g., #LI-RS1:, #LI-AG1:, #LI-DSC1:)
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
✓ CRITICAL FIX: Resolved bulk reference number regeneration issue - Added safeguards to prevent scheduled processing from regenerating ALL reference numbers during 2-minute monitoring intervals
✓ ENHANCED MONITORING: Only true scheduled runs (hourly/daily/weekly) now regenerate all reference numbers, monitoring updates only affect modified jobs
✓ IMPROVED LOGGING: Added detailed tracking to identify which jobs are being modified and which fields triggered the changes
✓ MODIFIED JOB REFERENCE FIX: Fixed issue where modified jobs kept old reference numbers - now properly generates NEW reference numbers when dateLastModified != dateAdded
✓ ONE-TO-ONE REFERENCE UPDATE FIX: Comprehensive sync no longer adds "missing" jobs during monitoring (was causing bulk reference regeneration)
✓ MONITORING OPTIMIZATION: Comprehensive sync only runs when actual modifications/removals detected, not for bulk additions
✓ REFERENCE NUMBER PRESERVATION: Only the specific modified job gets a new reference number, all other jobs maintain existing references
✓ ACTIVE MODIFICATION FLAGGING: Added '_monitor_flagged_as_modified' flag to mark jobs actually modified in current cycle
✓ REFERENCE PRESERVATION LOGIC: update_job_in_xml now preserves reference numbers unless job is flagged as actively modified
✓ COMPLETE FIX: Resolved issue where ALL jobs got new reference numbers - now strictly one-to-one relationship maintained
✓ COMPREHENSIVE FIELD SYNC SERVICE: Implemented xml_field_sync_service.py to ensure ALL fields are accurately synchronized between Bullhorn and XML
✓ AUTOMATIC DUPLICATE REMOVAL: Field sync service automatically detects and removes duplicate jobs during monitoring cycles
✓ FIELD MISMATCH CORRECTION: Service compares all fields and fixes discrepancies (e.g., remotetype showing "Onsite" instead of "Remote")
✓ RACE CONDITION PREVENTION: Cross-platform file locking prevents duplicates during rapid concurrent Bullhorn changes
✓ INTEGRATED INTO MONITORING: Field sync runs BEFORE processing changes, ensuring data integrity in every 2-minute cycle
✓ IMMEDIATE CLEANUP SCRIPT: Created immediate_xml_cleanup.py for one-time cleanup of existing duplicates and field mismatches
✓ CRITICAL SYNC ISSUE IDENTIFIED: Monitoring system had safeguard preventing addition of missing jobs during 2-minute cycles to avoid bulk reference regeneration
✓ EMERGENCY FIX DEPLOYED: Restored 50 jobs from backup to XML files and uploaded to SFTP (was only showing 33 of 53 jobs)
✓ ROOT CAUSE DISCOVERED: Comprehensive sync was commented out to prevent adding missing jobs, causing gradual job loss in XML
✓ PERMANENT FIX IMPLEMENTED: Re-enabled comprehensive sync to add missing jobs while preserving existing reference numbers, preventing bulk regeneration

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