# XML Job Feed Reference Number Updater

## Overview

This is a Flask-based web application designed to process XML job feed files and update reference numbers. The application provides a user-friendly interface for uploading XML files, validating their structure, and processing them to ensure proper reference number formatting.

## System Architecture

### Frontend Architecture
- **Template Engine**: Jinja2 templates with Bootstrap 5 dark theme
- **Client-side**: Vanilla JavaScript for drag-and-drop file upload functionality
- **UI Framework**: Bootstrap 5 with custom CSS styling and responsive design
- **Icons**: Font Awesome 6.0 for visual elements
- **Mobile Support**: Responsive design with tablet/mobile breakpoints and touch-friendly interfaces

### Backend Architecture
- **Web Framework**: Flask (Python)
- **Database**: PostgreSQL with SQLAlchemy ORM for schedule and log management
- **Background Processing**: APScheduler for automated XML processing and Bullhorn monitoring
- **File Processing**: Custom XML processor using lxml library
- **Email Service**: SendGrid integration for processing notifications and Bullhorn alerts
- **FTP Service**: Built-in FTP client for automatic file uploads to hosting providers
- **Bullhorn Integration**: Real-time ATS/CRM tearsheet monitoring with job change detection
- **Session Management**: Flask sessions with configurable secret key
- **File Handling**: Temporary file storage with secure filename handling
- **Proxy Support**: ProxyFix middleware for deployment behind reverse proxies

## Key Components

### 1. Flask Application (`app.py`)
- Main application entry point
- Route handlers for file upload and processing
- Security configurations (file size limits, allowed extensions)
- Error handling and user feedback via flash messages

### 2. XML Processor (`xml_processor.py`)
- Validates XML structure and required elements
- Processes job feed files with reference number generation
- Handles XML parsing errors and validation logic
- Maintains tracking of generated reference numbers

### 3. Frontend Interface (`templates/index.html`, `static/script.js`)
- Drag-and-drop file upload interface
- Real-time file validation feedback
- Progress indicators for processing
- Responsive design with dark theme

### 4. Application Entry Point (`main.py`)
- Simple import module for deployment compatibility

### 5. XML Integration Service (`xml_integration_service.py`)
- Automatically syncs Bullhorn job changes with XML files
- Maps Bullhorn job data to XML format with proper formatting
- Handles job additions, removals, and updates in XML files
- Ensures job IDs are properly formatted in parentheses after titles
- Generates unique reference numbers for new jobs

### 6. Complete Automation Workflow
- Monitors Bullhorn tearsheets for job changes every 5 minutes
- Automatically updates XML files when jobs are added/removed
- Regenerates reference numbers and processes updated files
- Uploads modified files to SFTP server automatically
- Sends comprehensive email notifications with XML sync information

## Data Flow

### Manual Upload Flow
1. **File Upload**: User selects XML file via drag-and-drop or file picker
2. **Client Validation**: JavaScript validates file type and size
3. **Server Processing**: Flask receives file and stores in temporary directory
4. **XML Validation**: XMLProcessor validates structure and required elements
5. **Reference Number Processing**: System generates/updates reference numbers
6. **Response**: User receives feedback on processing status

### Automated Bullhorn Integration Flow
1. **Monitor Check**: System checks Bullhorn tearsheets every 5 minutes
2. **Job Comparison**: Compares current jobs with previous snapshot
3. **XML Sync**: Automatically updates XML files with job changes
4. **Reference Processing**: Regenerates reference numbers for updated files
5. **File Upload**: Uploads modified files to SFTP server
6. **Email Notification**: Sends detailed reports with XML sync information

## External Dependencies

### Python Libraries
- **Flask**: Web framework for HTTP handling
- **lxml**: XML parsing and manipulation
- **Werkzeug**: WSGI utilities and secure file handling

### Frontend Libraries
- **Bootstrap 5**: UI framework with dark theme
- **Font Awesome 6**: Icon library

## Deployment Strategy

### Configuration
- Environment-based secret key configuration
- Proxy-aware setup for reverse proxy deployments
- Configurable upload limits and file restrictions

### File Storage
- Temporary file storage using system temp directory
- Secure filename handling to prevent directory traversal
- Automatic cleanup of temporary files

### Security Features
- File type validation (XML only)
- File size limits (50MB maximum)
- Secure filename sanitization
- Session-based flash messaging

## Changelog

```
Changelog:
- July 04, 2025. Initial setup and development
- July 04, 2025. Completed XML reference number updater with CDATA preservation and proper formatting
- July 04, 2025. Implemented comprehensive scheduling system with database integration and automated processing
- July 04, 2025. Added responsive design for mobile-friendly file management across all interfaces
- July 04, 2025. Enhanced SFTP/FTP system with dual protocol support and WP Engine optimization
- July 04, 2025. Fixed email notifications (verified sender domain) and SFTP connection (port 2222)
- July 04, 2025. Completed full automation: XML processing → Email notifications → SFTP upload to WP Engine
- July 04, 2025. Fixed filename preservation issue - now maintains original filename (myticas-job-feed-dice.xml)
- July 04, 2025. Added progress indicator modal for manual processing operations
- July 04, 2025. Implemented automated workflow integration for both manual and scheduled processing
- July 05, 2025. Added file replacement functionality - users can now update XML files without affecting schedule settings
- July 05, 2025. Implemented real-time progress tracking for manual operations with live status updates
- July 05, 2025. Fixed contrast issues for better text readability on dark backgrounds
- July 05, 2025. Added global settings page for centralized SFTP and email configuration management
- July 05, 2025. Enhanced manual uploads with SFTP auto-upload and preserved original filenames (no "updated_" prefix)
- July 05, 2025. Fixed SFTP connection testing to use proper SFTP protocol with real-time form validation
- July 05, 2025. Reorganized settings page layout with test connection section positioned after SFTP settings
- July 05, 2025. Updated email settings header to clarify automation-only usage (scheduled vs manual processing distinction)
- July 05, 2025. Fixed manual upload file persistence issue by changing output directory from /tmp to working directory
- July 05, 2025. Streamlined scheduler interface by centralizing all SFTP/email credentials in Global Settings - removed duplicate fields from scheduler forms
- July 05, 2025. Completely removed email field from scheduler form to eliminate confusion - all email notifications now use Global Settings exclusively
- July 05, 2025. Enhanced email notifications with SFTP upload status reporting and removed signature for cleaner presentation
- July 05, 2025. Fixed manual processing email workflow to properly use Global Settings and include SFTP upload status
- July 05, 2025. Completed full automation testing - all three features (XML processing, SFTP upload, email notifications) working perfectly
- July 05, 2025. Improved home page layout by moving Global Settings button next to Schedule Automation button for cleaner navigation
- July 07, 2025. Added comprehensive Bullhorn ATS/CRM integration for monitoring tearsheet job changes with automated email notifications
- July 07, 2025. Enhanced Bullhorn integration to use tearsheet names instead of requiring manual ID entry - system now automatically loads and displays all available tearsheets
- July 07, 2025. Added multiple tearsheet selection capability - users can now select multiple tearsheets and the system creates separate monitors for each one automatically
- July 07, 2025. Renamed "Bullhorn Monitoring" to "ATS Monitoring" with provider selection interface and ATS Settings button for future expansion to support multiple ATS providers
- July 08, 2025. Enhanced monitoring system to handle multiple simultaneous job changes with comprehensive change detection (added, removed, modified jobs), batch update notifications, and detailed email summaries with statistics
- July 08, 2025. Added prominent Job ID references in email notifications with visual badges for easy copy-paste lookup in Bullhorn
- July 08, 2025. Enhanced file replacement functionality to immediately upload new files to SFTP server when replaced (without reference number processing)
- July 08, 2025. Added "Last Upload" timestamp tracking for better user transparency - shows when files were most recently replaced/uploaded
- July 08, 2025. Final code cleanup and production readiness - optimized logging levels and verified all functionality
- July 10, 2025. Restructured Bullhorn monitoring to use JobOrder search queries instead of tearsheets after discovering "Tearsheet" is not a valid Bullhorn entity
- July 10, 2025. Updated monitor creation interface to accept custom search queries (e.g., "status:Open AND isPublic:1") for flexible job monitoring
- July 10, 2025. Enhanced monitoring system to handle both query-based and tearsheet-based monitors for backward compatibility
- July 11, 2025. Updated Bullhorn authentication to use production URL (https://job-feed-refresh.replit.app) after successful whitelisting by Bullhorn Support
- July 11, 2025. Verified successful authentication and API connectivity with production environment - system ready for live monitoring
- July 11, 2025. Confirmed Bullhorn API connection is working perfectly - authentication successful, API calls returning data correctly
- July 11, 2025. Comprehensive code cleanup - removed unused debug/test files, cleaned up excessive logging, optimized code structure for better maintainability and performance
- July 11, 2025. Enhanced tearsheet monitoring interface - fixed JavaScript errors, moved manual tearsheet ID entry to end of form, removed warning messages for cleaner UI
- July 11, 2025. Added comprehensive test email notification system - users can now preview exact email format with realistic sample data before actual job changes occur
- July 11, 2025. Final production cleanup - removed test files (explore_tearsheet_alternatives.py, test_tearsheet_api.py), reduced excessive debug logging, optimized code for deployment readiness
- July 11, 2025. Enhanced monitoring reliability - added false positive detection for job removals, increased API result count to 200 jobs, implemented verification logic to prevent incorrect removal notifications
- July 11, 2025. Added job count transparency - monitors now display current total jobs in tearsheets, email notifications prominently show total job counts, enhanced monitoring logs with job totals for user verification
- July 11, 2025. Fixed critical job count accuracy bug - implemented proper pagination handling in tearsheet API calls to ensure all jobs are retrieved, not just the first 20
- July 11, 2025. Resolved tearsheet pagination limitation by switching from entity API to search API with 'tearsheets.id' query for accurate job retrieval
- July 11, 2025. Implemented hybrid approach for job count accuracy - uses entity API as authoritative source to validate and limit search results, ensuring monitors display exact tearsheet counts matching Bullhorn
- July 11, 2025. Enhanced ATS monitoring dashboard with live job count badges - displays current job counts for all monitors in main overview for complete at-a-glance visibility
- July 12, 2025. Completed comprehensive XML integration system - automatically syncs Bullhorn job changes with XML files, updating job listings with proper formatting and reference numbers
- July 12, 2025. Enhanced monitoring system with full automation - when jobs are added/removed from tearsheets, system automatically updates XML files, regenerates reference numbers, and uploads to SFTP server
- July 12, 2025. Implemented seamless file replacement automation - modified XML files are automatically processed and replace both automation cycle files and web server versions
- July 12, 2025. Enhanced email notifications to include XML sync information - users receive detailed reports on XML file updates including job counts and upload status
- July 12, 2025. Completed full automation testing and verification - all core functionality tested and working correctly, system ready for production use
- July 12, 2025. Enhanced testing system with complete transparency - real XML file processing with step-by-step job additions/removals/updates, downloadable test files showing exact job formatting and reference numbers
- July 12, 2025. Implemented comprehensive test file download system - users can now download actual processed XML files to verify exact formatting, job structure, and reference number generation
- July 12, 2025. Added SFTP timeout protection for testing interface - prevents system hanging during real-time testing while maintaining full functionality for automated processing cycles
- July 12, 2025. Production optimization and code cleanup - optimized database connections, improved error handling, enhanced logging configuration, removed debug mode, and optimized XML parsing for better performance
- July 12, 2025. Fixed critical Bullhorn API authentication issue - resolved POST vs GET method error and added proper Content-Type headers for token exchange, restoring full API connectivity and monitor functionality
```

## User Preferences

```
Preferred communication style: Simple, everyday language.
```

## Technical Notes

### XML Processing Requirements
- Root element must be 'source'
- Required job elements: title, company, date, referencenumber
- Validation checks first 10 jobs for performance
- Error logging for debugging and monitoring

### File Upload Constraints
- Only XML files accepted
- Maximum file size: 50MB
- Files stored temporarily during processing
- Drag-and-drop and traditional file picker support

### Error Handling
- Comprehensive XML syntax error catching
- User-friendly error messages via flash system
- Server-side logging for debugging
- Client-side validation feedback