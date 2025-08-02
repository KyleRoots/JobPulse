# XML Job Feed Reference Number Updater

## Overview
This Flask-based web application processes XML job feed files to update reference numbers, offering a user-friendly interface for file uploads, validation, and automated processing. Its core purpose is to ensure proper reference number formatting in job feeds, integrate with Bullhorn ATS/CRM for real-time job synchronization, and automatically manage XML file updates and SFTP uploads. The system aims to provide a robust, automated solution for maintaining accurate and up-to-date job listings.

## User Preferences
Preferred communication style: Simple, everyday language.

## Recent Changes (August 2, 2025)
- **ENHANCED MONITORING SAFEGUARDS**: Upgraded false positive detection from 80% to 95% retention threshold, added large batch removal protection (>10 jobs), and XML corruption detection to prevent future false alerts
- **REFERENCE NUMBER SAFEGUARDS IMPLEMENTED**: Added preserve_references flag to rebuild script and established clear guidelines for scheduled automation vs ad-hoc fixes to prevent unintended reference number overwrites
- **FALSE POSITIVE MONITORING RESOLVED**: Fixed monitoring alert that incorrectly reported 16 jobs removed - rebuilt XML from Bullhorn showing correct 70 jobs total, accepted new reference numbers as scheduled automation
- **APPLICATION DEPLOYMENT READY**: Successfully completed email delivery logging system and initiated deployment process, all components tested and operational
- **NAVIGATION ENHANCED**: Added Email Logs button to settings page navigation and optimized email logs page navigation (Dashboard → Settings → Email Logs)
- **EMAIL DELIVERY LOGGING SYSTEM FULLY OPERATIONAL**: Completed comprehensive EmailDeliveryLog database model with complete email tracking functionality, logs notification type, job details, delivery status, SendGrid message IDs, and error messages for troubleshooting
- **EMAIL LOGS WEB DASHBOARD DEPLOYED**: Created fully functional email logs dashboard at /email-logs with real-time statistics, filtering by notification type, status indicators, and detailed email delivery information including SendGrid tracking IDs - fixed 500 error and template inheritance issues
- **ENHANCED EMAIL SERVICE WITH DATABASE INTEGRATION**: Updated EmailService class to include database logging support with _log_email_delivery method, tracks all email notifications (job added/removed/modified and scheduled processing) with detailed metadata
- **INDIVIDUAL JOB CHANGE NOTIFICATIONS**: Replaced bulk email notifications with individual job change notifications for better tracking and troubleshooting, each job change now generates separate email with database logging
- **EMAIL ROUTES INTEGRATION COMPLETED**: Successfully added missing /email-logs and /api/email-logs routes to app.py, resolved 404 errors, integrated pagination and filtering capabilities
- **SIMPLIFIED MONITORING SERVICE WITH DATABASE LOGGING**: Updated SimplifiedMonitoringService to use enhanced EmailService with database support, ensures all job change notifications are properly logged and tracked
- **COMPLETE XML REBUILD FROM TEARSHEETS**: Successfully rebuilt entire XML feed from scratch using Bullhorn API, retrieved all 70 jobs from tearsheets (54 Ottawa, 7 VMS, 9 Clover, 0 Cleveland, 0 Chicago), generated 322KB XML files with 1,260 CDATA sections
- **SIMPLIFIED MONITORING SYSTEM DEPLOYED**: Implemented streamlined monitoring with only 3 notification types (job added, removed, modified), removed comprehensive sync to focus on tearsheet-based tracking only
- **TEARSHEET JOB HISTORY TRACKING**: Added TearsheetJobHistory database model to track job additions/removals from tearsheets for proper change detection and historical auditing
- **SFTP CREDENTIALS CONFIGURED**: Successfully connected to WP Engine SFTP server (mytconsulting.sftp.wpengine.com:2222) and uploaded both XML files to live website
- **AI CLASSIFICATION ACTIVE**: All 70 jobs now have proper AI-powered classification for jobfunction (Information Technology, Consulting, etc.), jobindustries (Computer Software, etc.), and senoritylevel (Mid-Senior level, etc.)
- **LINKEDIN TAGS PRESERVED**: Maintained all recruiter LinkedIn tags in new format "#LI-XX: Name" (e.g., #LI-AG: Adam Gebara, #LI-MIT: Michael Theodossiou) across 11 different recruiters
- **XML SAFEGUARDS MODULE IMPLEMENTED**: Created comprehensive `xml_safeguards.py` module with automatic backup creation, XML structure validation (checking required fields, job counts, CDATA formatting), duplicate detection, file size verification, MD5 checksums, and rollback capabilities on validation failure
- **SAFE XML WRITING INTEGRATED**: Added `_safe_write_xml` method to XMLIntegrationService that validates all XML updates before committing, creates automatic backups, and rolls back changes if validation fails
- **CDATA CORRUPTION FIXED**: Restored proper CDATA formatting across all 1058 fields (70 jobs × 18 fields each) in both XML files using `fix_cdata_complete.py` script, successfully uploaded to live website
- **DUPLICATE JOBS REMOVED**: Identified and removed duplicate entries for jobs 32576 and 34082, reducing total job count from 72 to 70 unique jobs matching Bullhorn tearsheets
- **RECRUITER TAG FORMAT UPDATED**: Changed assignedrecruiter format to include both LinkedIn tag and name (e.g., `<assignedrecruiter><![CDATA[#LI-AG: Adam Gebara]]></assignedrecruiter>`) for auditing purposes
- **RECRUITER MAPPING REVISED**: Updated to 14 approved recruiters only, with Myticas Recruiter and Reena Setya both using #LI-RS tag
- **JOB 34089 DATA CORRECTED**: Fixed truncated description and incorrect country (now shows Canada instead of United States) with full job details from Bullhorn
- **REFERENCE NUMBER PRESERVATION RULES**: 
  - **Ad-hoc fixes**: Must preserve existing reference numbers to maintain external integrations and bookmarks
  - **Scheduled automation**: May generate new reference numbers for comprehensive rebuilds (e.g., fixing data inconsistencies)
  - **New jobs**: Always generate new unique reference numbers
  - **Safeguard mechanism**: rebuild_xml_standalone.py includes --preserve-references flag for ad-hoc fixes
- **CDATA FORMATTING PERMANENTLY FIXED**: Successfully restored CDATA formatting in both XML files using ensure_cdata_format.py script, uploaded to SFTP server with 946 CDATA sections per file
- **LINKEDIN TAGS WITH CDATA FIXED**: Resolved issue where assignedrecruiter LinkedIn tags (#LI-) were missing CDATA wrapping - now all 74 recruiter tags have proper <![CDATA[#LI-XX]]> formatting
- **APPLICATION OPTIMIZATION COMPLETED**: Implemented comprehensive performance improvements including database query optimization, memory-efficient XML processing, batch API calls, and enhanced error recovery systems without affecting existing functionality
- **CDATA FORMATTING RESTORED**: Fixed critical issue where recent updates removed CDATA formatting from XML fields - created specialized script that preserves CDATA while populating missing location data
- **LINKEDIN TAG SYSTEM PERMANENTLY STABILIZED**: Successfully restored LinkedIn tags on live website (https://myticas.com/myticas-job-feed.xml) after temporary reversion, implemented permanent solution to prevent future reversions
- **SFTP UPLOAD RESTORATION COMPLETED**: Force-uploaded XML files to SFTP server, confirmed all 15 LinkedIn tag mappings are now live including #LI-RP, #LI-MIT, #LI-MYT displaying correctly instead of full names
- **MISSING FIELD POPULATION COMPLETED**: Systematically scanned all 74+ jobs using Bullhorn API, fixed 8 jobs with missing state/location fields (including job 34087: Vancouver → BC, job 34083: Toronto → ON), uploaded corrected XML files (396,036 bytes)
- **DATA SYNCHRONIZATION ISSUE RESOLVED**: Fixed live website showing truncated job descriptions - forced SFTP upload of complete XML files ensuring all job modifications properly synchronized
- **WORKFLOW COMPLETION TRACKING ENHANCED**: Resolved Ottawa Sponsored Jobs workflow - job 34087 modifications now properly detected, XML updated, notifications sent, and live website synchronized with complete job data
- **AUTOMATION ROBUSTNESS ENHANCED**: XML processing preserves LinkedIn tags during job updates through enhanced _extract_assigned_recruiter mapping system, preventing tag reversions during Bullhorn sync operations
- **LIVE WEBSITE VERIFICATION COMPLETE**: Confirmed all 15 LinkedIn tag mappings including Nick Theodossiou → #LI-NT and Matheo Theodossiou → #LI-MAT are properly displayed on live website
- **EMAIL NOTIFICATION SYSTEM FULLY RESOLVED**: Fixed critical email delivery issues where monitoring detected job changes but notifications weren't being sent, processed 246+ accumulated pending notifications
- **EMAIL SERVICE CONFIGURATION FIXED**: Corrected GlobalSettings email address lookup logic and updated email service to handle both full Bullhorn objects and simplified job data formats
- **PRODUCTION TESTING VERIFIED**: Test email successfully sent to kroots@myticas.com confirming full system functionality
- **RECRUITER LINKEDIN TAG MAPPING IMPLEMENTED**: Successfully converted all recruiter names to LinkedIn-style tags across both XML files with 15 total mappings
- **XML FILE SYNCHRONIZATION**: Both myticas-job-feed.xml and myticas-job-feed-scheduled.xml now use identical LinkedIn tag mappings and formatting

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
- **XML Integration Service**: Handles job additions, removals, and updates in XML files, ensuring job IDs are formatted and reference numbers generated. **HTML Consistency Fixed (July 31, 2025)**: All job descriptions now have consistent HTML formatting within CDATA sections. **LinkedIn Recruiter Tags (July 31, 2025)**: Integrated recruiter name mapping with 15 LinkedIn-style tags, automatically converting names to branded tags (e.g., "Michael Theodossiou" → "#LI-MIT") while maintaining proper CDATA formatting.
- **UI/UX**: Responsive dark-themed interface with real-time feedback and progress indicators.
- **Security**: Login-protected routes and admin user management.

### Technical Implementation Details
- **XML Processing Requirements**: Root element 'source', required elements (title, company, date, referencenumber), validation on first 10 jobs.
- **Reference Number Preservation**: CRITICAL - When making ad-hoc XML changes, existing `<referencenumber>` values MUST be preserved. New reference numbers are only generated for new jobs or during scheduled automation refresh cycles.
- **File Upload Constraints**: XML files only, max 50MB, temporary storage, secure filename handling.
- **Error Handling**: Comprehensive XML syntax error catching, user-friendly flash messages, server-side logging, client-side validation.
- **AI-Powered Job Classification**: Integrates OpenAI GPT-4o to classify jobs (jobfunction, jobindustries, senoritylevel) based on title/description, using predefined Excel-based mappings.
- **HTML Formatting Consistency**: Ensures all job descriptions have consistent HTML markup by converting HTML entities (e.g., `&lt;strong&gt;`) to proper HTML tags (e.g., `<strong>`) within CDATA sections.
- **Email Delivery Logging Architecture**: Comprehensive email tracking system with EmailDeliveryLog database model, tracks notification_type (job_added/removed/modified/scheduled_processing), job_id, job_title, recipient_email, delivery_status (sent/failed), SendGrid message IDs, error messages, and detailed changes_summary. Includes web dashboard at /email-logs with statistics and filtering capabilities.
- **Enhanced EmailService Integration**: EmailService class initialized with database logging support (db=db, EmailDeliveryLog=EmailDeliveryLog), automatically logs all email notifications through _log_email_delivery method, supports individual job change notifications and bulk scheduled processing notifications.
- **Reference Number Process Documentation**: Created REFERENCE_NUMBER_PROCESS.md with clear guidelines for when to preserve vs regenerate reference numbers, includes command line examples and verification steps for future operations.

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